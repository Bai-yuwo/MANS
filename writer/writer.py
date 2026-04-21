"""
writer/writer.py

Writer 核心逻辑 —— MANS 系统中唯一调用主力大模型生成正文的组件。

职责边界：
    1. 接收 InjectionEngine 组装好的上下文（InjectionContext）。
    2. 渲染 Jinja2 提示词模板（writer/prompts/writer.j2）。
    3. 调用主力大模型（writer 角色），流式输出场景文本。
    4. 触发 UpdateExtractor 进行异步状态更新（不阻塞写作流程）。
    5. 将生成的场景草稿保存到 StoryDB。
    6. 支持场景重写（regenerate），包含旧状态回滚和新状态提取。

设计原则：
    - 唯一生成器：只有 Writer 调用主力大模型生成正文。所有其他组件维护知识库并准备上下文。
    - 流式输出：支持 SSE 实时推送到前端，提升用户体验。
    - 异步更新：生成完成后立即触发 UpdateExtractor，不等待其完成，避免阻塞主流程。
    - 错误隔离：单场景失败不影响其他场景；向量化/更新失败不中断主流程。
    - 质量校验：生成后进行字数、完整性等基础校验，防止半截文本污染知识库。

主要方法：
    - write_scene(): 生成单个场景文本（回调式流式输出）。
    - write_scene_stream(): 生成单个场景文本（异步迭代器式流式输出）。
    - regenerate_scene(): 根据反馈重新生成场景（含旧状态回滚）。
    - regenerate_scene_stream(): 重新生成场景（流式迭代器版本）。
    - get_scene_draft(): 获取已保存的场景草稿。

异常体系：
    - WriterError: 基类异常，包含 stage 和 details 字段便于定位问题环节。
    - PromptRenderError: Jinja2 模板渲染失败（模板缺失或变量错误）。
    - GenerationError: LLM 调用失败（网络、限流、认证等）。
    - InvalidOutputError: 输出校验失败（字数不足、格式错误等）。
    - SaveError: 场景草稿保存失败。

典型用法：
    writer = Writer(project_id="xxx")

    # 方式1：回调式流式输出（适合 Web 后端通过 SSE 推送到前端）
    async def stream_callback(token: str):
        await send_to_frontend(token)

    text = await writer.write_scene(
        scene_plan=scene_plan,
        chapter_plan=chapter_plan,
        stream_callback=stream_callback
    )

    # 方式2：异步迭代器（适合需要自行处理流式数据的场景）
    async for token in writer.write_scene_stream(
        scene_plan=scene_plan,
        chapter_plan=chapter_plan
    ):
        print(token, end="")
"""

import asyncio
import json
from pathlib import Path
from typing import Optional, Callable, AsyncIterator
from datetime import datetime

from jinja2 import Environment, FileSystemLoader, TemplateNotFound

from core.config import get_config
from core.llm_client import LLMClient, LLMError
from core.schemas import ScenePlan, ChapterPlan, InjectionContext
from core.injection_engine import InjectionEngine
from core.update_extractor import UpdateExtractor
from core.logging_config import get_logger, log_exception

logger = get_logger('writer.writer')


class WriterError(Exception):
    """
    Writer 基础异常。

    所有 Writer 相关异常均继承此类，便于调用方统一捕获。
    包含 stage 字段标识失败发生的环节，details 字段提供调试上下文。

    Attributes:
        stage: 失败环节标识（prompt_render / generation / validation / save_draft / update）。
        details: 包含额外上下文信息的字典（如章节号、场景索引、实际字数等）。
    """
    def __init__(self, message: str, stage: str = "", details: dict = None):
        super().__init__(message)
        self.stage = stage
        self.details = details or {}


class PromptRenderError(WriterError):
    """Jinja2 提示词模板渲染失败。通常由模板文件缺失或模板变量未定义引起。"""
    pass


class GenerationError(WriterError):
    """LLM 文本生成失败。包括网络错误、认证失败、限流、超时等底层 API 异常。"""
    pass


class InvalidOutputError(WriterError):
    """生成文本输出校验失败。如字数不足目标50%、文本为空、格式严重错误等。"""
    pass


class SaveError(WriterError):
    """场景草稿保存失败。StoryDB 原子写入操作失败。"""
    pass


class Writer:
    """
    Writer —— MANS 系统中唯一的正文生成器。

    Writer 的核心任务是：根据 InjectionEngine 提供的上下文（强制层+检索层），
    渲染 Jinja2 模板后调用主力大模型，流式生成场景正文。

    写作流程：
        1. InjectionEngine.build_context() → 组装 InjectionContext。
        2. _render_prompt() → 将 InjectionContext 渲染为 Jinja2 模板。
        3. LLMClient.stream() → 流式调用 writer 角色模型，逐 token 输出。
        4. _validate_generated_text() → 校验生成文本质量（字数、非空等）。
        5. _trigger_update() → 触发 UpdateExtractor 异步提取状态变更。
        6. _save_scene_draft() → 将场景草稿原子写入 StoryDB。

    依赖延迟初始化：
        InjectionEngine、UpdateExtractor、StoryDB 均采用延迟初始化策略，
        避免在仅创建 Writer 实例时就触发耗时操作（如加载向量模型）。

    模板环境复用：
        类级 _template_env 缓存所有实例共享的 Jinja2 Environment，
        避免重复从磁盘加载模板文件。
    """

    # 类级缓存：所有 Writer 实例共享同一个 Jinja2 Environment
    _template_env: Optional[Environment] = None

    def __init__(self, project_id: str):
        """
        初始化 Writer。

        注意：此构造函数仅进行轻量级初始化（配置读取、LLMClient 创建）。
        重依赖组件（InjectionEngine、UpdateExtractor、StoryDB）在首次使用时延迟初始化。

        Args:
            project_id: 项目唯一标识，用于隔离不同项目的数据。
        """
        self.project_id = project_id
        self.config = get_config()
        self.llm_client = LLMClient()
        self.injection_engine: Optional[InjectionEngine] = None
        self.update_extractor: Optional[UpdateExtractor] = None

        # 知识库引用（延迟初始化）
        self._story_db = None

        # 复用类级缓存的 Jinja2 模板环境，避免重复磁盘 I/O
        if Writer._template_env is None:
            prompts_dir = Path(__file__).parent / "prompts"
            Writer._template_env = Environment(
                loader=FileSystemLoader(str(prompts_dir)),
                trim_blocks=True,
                lstrip_blocks=True
            )
        self.template_env = Writer._template_env

        # 延迟初始化标志
        self._initialized = False

    def _ensure_initialized(self):
        """
        确保依赖组件已初始化。

        延迟初始化 InjectionEngine 和 UpdateExtractor，避免在构造 Writer 时
        就触发耗时操作（如 Embedding 模型加载）。此方法在首次需要时自动调用。
        """
        if not self._initialized:
            self.injection_engine = InjectionEngine(self.project_id)
            self.update_extractor = UpdateExtractor(self.project_id)
            self._initialized = True

    @property
    def story_db(self):
        """
        延迟初始化故事库（StoryDB）。

        首次访问时动态导入并创建 StoryDB 实例，后续访问直接返回缓存实例。
        延迟导入避免循环依赖和启动时不必要的模块加载。
        """
        if self._story_db is None:
            from knowledge_bases.story_db import StoryDB
            self._story_db = StoryDB(self.project_id)
        return self._story_db

    async def write_scene(
        self,
        scene_plan: ScenePlan,
        chapter_plan: ChapterPlan,
        stream_callback: Optional[Callable[[str], None]] = None,
        sync_update: bool = False,
        temperature: float = None
    ) -> str:
        """
        生成单个场景文本（回调式流式输出）。

        标准流程：
            1. Injection Engine 组装上下文 → InjectionContext。
            2. 渲染 Jinja2 提示词 → 完整 prompt 字符串。
            3. 调用主力大模型流式生成 → 逐 token 接收。
            4. 实时推送到前端（通过 stream_callback）。
            5. 校验生成结果（字数是否达标、文本是否非空）。
            6. 触发异步更新（UpdateExtractor，可选同步等待）。
            7. 保存场景草稿到 StoryDB。

        字数估算：
            中文字符与 token 的比例约为 1:1.5~2，因此 max_tokens 估算为
            target_word_count * 2，确保生成长度足够覆盖目标字数。

        Args:
            scene_plan: 场景规划（包含意图、POV人物、出场人物、目标字数等）。
            chapter_plan: 章节规划（包含章节目标、情绪走向等）。
            stream_callback: 用于将流式 token 发送到前端的回调函数（可选）。
            sync_update: 是否等待 UpdateExtractor 完成（默认 False，异步执行）。
            temperature: 生成温度（默认 0.75，创意与一致性的平衡点）。

        Returns:
            生成的完整场景文本字符串。

        Raises:
            PromptRenderError: 模板渲染失败。
            GenerationError: LLM 调用失败（网络/限流/超时）。
            InvalidOutputError: 生成文本校验失败（字数不足等）。
            SaveError: 保存草稿失败。
        """
        self._ensure_initialized()

        try:
            # Step 1: Injection Engine 组装上下文
            injection_ctx = await self.injection_engine.build_context(
                scene_plan=scene_plan,
                chapter_plan=chapter_plan
            )

            # Step 2: 渲染 Jinja2 提示词
            prompt = self._render_prompt(injection_ctx)
            system_prompt = self._render_system_prompt(injection_ctx)

            # Step 3: 调用主力大模型，流式生成
            full_text = ""

            # 估算 token 数（中文字符 * 2 约等于 tokens）
            max_tokens = scene_plan.target_word_count * 2

            # 若未显式传入 temperature，使用 config 中 writer 角色的默认值
            if temperature is None:
                temperature = self.config.get_temperature_for_role('writer')

            async for token in self.llm_client.stream(
                role="writer",
                prompt=prompt,
                system_prompt=system_prompt,
                max_tokens=max_tokens,
                temperature=temperature
            ):
                full_text += token

                # 实时推送到前端
                if stream_callback:
                    await stream_callback(token)

            # Step 4: 校验生成结果
            if not self._validate_generated_text(full_text, scene_plan):
                raise InvalidOutputError(
                    f"生成文本校验失败：字数不足（{len(full_text)} < {scene_plan.target_word_count * 0.5}）",
                    stage="validation",
                    details={
                        "actual_length": len(full_text),
                        "expected_min_length": int(scene_plan.target_word_count * 0.5),
                        "target_word_count": scene_plan.target_word_count
                    }
                )

            # Step 5: 触发异步更新
            update_task = asyncio.create_task(
                self._trigger_update(
                    generated_text=full_text,
                    chapter_number=chapter_plan.chapter_number,
                    scene_index=scene_plan.scene_index,
                    scene_plan=scene_plan
                )
            )

            # 如果需要同步等待更新完成
            if sync_update:
                await update_task

            # Step 6: 保存场景草稿
            await self._save_scene_draft(
                chapter_number=chapter_plan.chapter_number,
                scene_index=scene_plan.scene_index,
                text=full_text,
                injection_ctx=injection_ctx,
                scene_plan=scene_plan
            )

            return full_text

        except LLMError as e:
            raise GenerationError(
                f"LLM 生成失败: {str(e)}",
                stage="generation",
                details={"provider": e.provider, "model": e.model}
            )
        except Exception as e:
            raise WriterError(
                f"场景生成失败: {str(e)}",
                stage="unknown",
                details={"error": str(e)}
            )

    async def write_scene_stream(
        self,
        scene_plan: ScenePlan,
        chapter_plan: ChapterPlan,
        sync_update: bool = False,
        temperature: float = None
    ) -> AsyncIterator[str]:
        """
        生成单个场景文本（异步迭代器版本）。

        与 write_scene() 功能完全相同，但返回 AsyncIterator 便于调用方自行处理流式输出。
        适用于调用方需要自定义流式处理逻辑（如实时字数统计、敏感词过滤等）的场景。

        Args:
            scene_plan: 场景规划。
            chapter_plan: 章节规划。
            sync_update: 是否等待 UpdateExtractor 完成。
            temperature: 生成温度。

        Yields:
            生成的文本片段（token）。

        Raises:
            InvalidOutputError: 生成文本校验失败。
            SaveError: 保存草稿失败。
        """
        self._ensure_initialized()

        # Step 1: Injection Engine 组装上下文
        injection_ctx = await self.injection_engine.build_context(
            scene_plan=scene_plan,
            chapter_plan=chapter_plan
        )

        # Step 2: 渲染 Jinja2 提示词
        prompt = self._render_prompt(injection_ctx)
        system_prompt = self._render_system_prompt(injection_ctx)

        # Step 3: 流式生成并 yield
        full_text = ""
        max_tokens = scene_plan.target_word_count * 2

        # 若未显式传入 temperature，使用 config 中 writer 角色的默认值
        if temperature is None:
            temperature = self.config.get_temperature_for_role('writer')

        async for token in self.llm_client.stream(
            role="writer",
            prompt=prompt,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            temperature=temperature
        ):
            full_text += token
            yield token

        # Step 4: 校验生成结果
        if not self._validate_generated_text(full_text, scene_plan):
            raise InvalidOutputError(
                f"生成文本校验失败：字数不足（{len(full_text)} < {scene_plan.target_word_count * 0.5}）",
                stage="validation",
                details={
                    "actual_length": len(full_text),
                    "expected_min_length": int(scene_plan.target_word_count * 0.5),
                    "target_word_count": scene_plan.target_word_count
                }
            )

        # Step 5: 触发异步更新（在生成完成后）
        update_task = asyncio.create_task(
            self._trigger_update(
                generated_text=full_text,
                chapter_number=chapter_plan.chapter_number,
                scene_index=scene_plan.scene_index,
                scene_plan=scene_plan
            )
        )

        if sync_update:
            await update_task

        # Step 6: 保存场景草稿
        await self._save_scene_draft(
            chapter_number=chapter_plan.chapter_number,
            scene_index=scene_plan.scene_index,
            text=full_text,
            injection_ctx=injection_ctx,
            scene_plan=scene_plan
        )

    def _render_prompt(self, injection_ctx: InjectionContext) -> str:
        """
        渲染 Jinja2 提示词模板。

        使用类级缓存的 Jinja2 Environment 加载 writer/prompts/writer.j2 模板，
        将 InjectionContext 对象作为 context 变量传入模板进行渲染。

        模板路径：writer/prompts/writer.j2
        模板变量：context（InjectionContext 对象）。

        Args:
            injection_ctx: InjectionEngine 组装完成的上下文数据。

        Returns:
            渲染后的完整 prompt 字符串。

        Raises:
            PromptRenderError: 模板文件未找到或渲染失败。
        """
        try:
            template = self.template_env.get_template("writer.j2")
            return template.render(context=injection_ctx)
        except TemplateNotFound as e:
            raise PromptRenderError(
                f"模板文件未找到: {e.name}",
                stage="prompt_render",
                details={"template": e.name}
            )
        except Exception as e:
            raise PromptRenderError(
                f"模板渲染失败: {str(e)}",
                stage="prompt_render",
                details={"error": str(e)}
            )

    def _render_system_prompt(self, injection_ctx: InjectionContext) -> str:
        """
        渲染系统提示词模板。

        加载 writer/prompts/system.j2 模板，将 InjectionContext 传入渲染，
        生成作为 LLM system role 的提示词。system prompt 定义了作家的
        身份、写作要求和禁止事项，与任务级的 user prompt 分离。

        模板路径：writer/prompts/system.j2
        模板变量：context（InjectionContext 对象）。

        Args:
            injection_ctx: InjectionEngine 组装完成的上下文数据。

        Returns:
            渲染后的 system prompt 字符串。

        Raises:
            PromptRenderError: 模板文件未找到或渲染失败。
        """
        try:
            template = self.template_env.get_template("system.j2")
            return template.render(context=injection_ctx)
        except TemplateNotFound:
            # 如果 system.j2 不存在，返回一个默认的系统提示词
            return (
                "你是一位中文网络小说作家，专注于场景描写。"
                "文笔流畅，节奏感强，对话自然，细节生动。"
                "严格基于提供的设定信息创作，不引入设定外内容。"
            )
        except Exception as e:
            raise PromptRenderError(
                f"系统提示词模板渲染失败: {str(e)}",
                stage="prompt_render",
                details={"error": str(e)}
            )

    def _validate_generated_text(self, text: str, scene_plan: ScenePlan) -> bool:
        """
        校验生成的文本是否有效。

        防御性编程：防止半截文本、空文本污染知识库。校验项：
            1. 空文本检查：文本不能为空或仅包含空白字符。
            2. 字数检查：文本长度至少达到目标字数的 50%。低于此阈值认为生成失败。
            3. 完整性启发式检查：文本末尾是否以常见结束标点结尾（非强制）。
               若不以标点结尾但字数达标，仅记录警告，仍允许通过。

        Args:
            text: 生成的场景文本。
            scene_plan: 场景规划（包含 target_word_count 目标字数）。

        Returns:
            True 表示文本有效；False 表示应丢弃此生成结果。
        """
        # 检查1：空文本
        if not text or not text.strip():
            logger.warning("[Writer] 生成文本为空")
            return False

        # 检查2：字数不足（至少达到目标字数的 50%）
        min_length = int(scene_plan.target_word_count * 0.5)
        if len(text) < min_length:
            logger.warning(
                f"[Writer] 生成文本字数不足：{len(text)} < {min_length} "
                f"(目标: {scene_plan.target_word_count})"
            )
            return False

        # 检查3：文本是否被截断（以标点符号结尾更可能是完整句子）
        # 注意：这只是启发式检查，不强制要求
        last_char = text.strip()[-1] if text.strip() else ""
        if last_char not in ['。', '！', '？', '”', '』', '）', '>', '\n']:
            logger.info(
                f"[Writer] 生成文本可能未完整结束（结尾字符: '{last_char}'），"
                f"但字数足够，允许通过"
            )

        return True

    async def _trigger_update(
        self,
        generated_text: str,
        chapter_number: int,
        scene_index: int,
        scene_plan: ScenePlan
    ) -> None:
        """
        触发 UpdateExtractor 进行异步状态更新。

        此方法在场景生成完成后立即被调用（通过 asyncio.create_task），
        不等待 UpdateExtractor 完成即返回。这是"异步更新"原则的实现：
        写作流程不应被状态提取和知识库更新阻塞。

        UpdateExtractor 会执行以下操作：
            1. 使用 extract 角色模型从生成文本中提取状态变更。
            2. 并发更新人物库、世界观库、伏笔库。
            3. 将场景文本向量化存储。
            4. 保存更新记录到文件（用于调试和回滚）。

        异常处理：
            更新失败仅记录错误日志，不影响主流程。

        Args:
            generated_text: 生成的场景文本。
            chapter_number: 章节编号。
            scene_index: 场景索引（在本章内的序号）。
            scene_plan: 场景规划。
        """
        try:
            await self.update_extractor.extract_and_update(
                generated_text=generated_text,
                chapter_number=chapter_number,
                scene_index=scene_index,
                scene_plan=scene_plan,
                sync=False  # 异步执行
            )
        except Exception as e:
            # 更新失败不影响主流程，记录错误
            logger.error(f"[Writer] 异步更新失败: {str(e)}")

    async def _save_scene_draft(
        self,
        chapter_number: int,
        scene_index: int,
        text: str,
        injection_ctx: InjectionContext,
        scene_plan: ScenePlan
    ) -> None:
        """
        保存场景草稿到文件。

        使用 StoryDB 的原子性 update_scene_in_draft 方法，全程通过 FileLockRegistry
        加锁，彻底消除并发提取器或前端自动保存导致的竞态覆盖问题。

        保存的数据结构：
            - scene_index: 场景索引。
            - text: 生成的场景文本。
            - word_count: 文本字数。
            - generated_at: 生成时间戳（ISO格式）。
            - injection_context_summary: 注入上下文的摘要信息（用于调试和审计）。

        Args:
            chapter_number: 章节编号。
            scene_index: 场景索引。
            text: 生成的场景文本。
            injection_ctx: 注入上下文（用于记录 token 使用等元信息）。
            scene_plan: 场景规划。

        Raises:
            SaveError: 保存失败（StoryDB 返回 False 或抛出异常）。
        """
        try:
            # 构建单场景草稿数据
            scene_data = {
                "scene_index": scene_index,
                "text": text,
                "word_count": len(text),
                "generated_at": datetime.now().isoformat(),
                "injection_context_summary": {
                    "scene_intent": scene_plan.intent,
                    "pov_character": scene_plan.pov_character,
                    "present_characters": scene_plan.present_characters,
                    "emotional_tone": scene_plan.emotional_tone,
                    "token_budget_used": injection_ctx.total_tokens_used,
                    "token_budget_remaining": injection_ctx.token_budget_remaining
                }
            }

            # 原子性更新：StoryDB 内部使用 FileLockRegistry 包裹 读-改-写
            success = await self.story_db.update_scene_in_draft(
                chapter_number=chapter_number,
                scene_data=scene_data
            )
            if not success:
                raise SaveError(
                    "StoryDB 返回保存失败",
                    stage="save_draft",
                    details={"chapter": chapter_number, "scene": scene_index}
                )

        except SaveError:
            raise
        except Exception as e:
            raise SaveError(
                f"保存场景草稿失败: {str(e)}",
                stage="save_draft",
                details={
                    "chapter": chapter_number,
                    "scene": scene_index,
                    "error": str(e)
                }
            )

    async def regenerate_scene(
        self,
        scene_plan: ScenePlan,
        chapter_plan: ChapterPlan,
        previous_attempt: str,
        feedback: str,
        stream_callback: Optional[Callable[[str], None]] = None,
        temperature: float = None
    ) -> str:
        """
        根据反馈重新生成场景。

        重写流程（与首次生成相比增加了回滚和反馈机制）：
            1. 组装上下文并渲染标准 prompt（与首次生成相同）。
            2. 在标准 prompt 基础上追加【修改要求】段落，包含用户反馈。
            3. 流式生成新文本。
            4. 校验生成结果。
            5. 回滚旧场景产生的知识库更新（防止旧状态污染）。
            6. 保存新草稿（覆盖原草稿）。
            7. 触发新文本的异步更新提取。

        回滚机制：
            调用 UpdateExtractor.rollback_scene_updates() 逆向撤销旧场景对人物状态、
            世界规则、伏笔等知识库的修改。若回滚失败（如无更新记录），仅记录警告，
            不中断重写流程。

        Args:
            scene_plan: 场景规划。
            chapter_plan: 章节规划。
            previous_attempt: 上次生成的文本（用于日志记录，当前未直接参与生成逻辑）。
            feedback: 修改意见/反馈（将直接嵌入 prompt 的【修改要求】段落）。
            stream_callback: 流式输出回调（可选）。
            temperature: 生成温度（可适当调高以增加变化）。

        Returns:
            重新生成的场景文本。

        Raises:
            InvalidOutputError: 重新生成文本校验失败。
            SaveError: 保存新草稿失败。
        """
        self._ensure_initialized()

        # 构建带有反馈的特殊 prompt
        injection_ctx = await self.injection_engine.build_context(
            scene_plan=scene_plan,
            chapter_plan=chapter_plan
        )

        # 在标准 prompt 基础上添加反馈
        base_prompt = self._render_prompt(injection_ctx)
        system_prompt = self._render_system_prompt(injection_ctx)

        feedback_prompt = f"""{base_prompt}

---
【修改要求】

之前生成的文本存在以下问题，请根据反馈修改：

{feedback}

请重新生成本场景，确保解决上述问题。
"""

        # 流式生成
        full_text = ""
        max_tokens = scene_plan.target_word_count * 2

        # 若未显式传入 temperature，使用 config 中 writer 角色的默认值
        if temperature is None:
            temperature = self.config.get_temperature_for_role('writer')

        async for token in self.llm_client.stream(
            role="writer",
            prompt=feedback_prompt,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            temperature=temperature
        ):
            full_text += token
            if stream_callback:
                await stream_callback(token)

        # 校验生成结果
        if not self._validate_generated_text(full_text, scene_plan):
            raise InvalidOutputError(
                f"重新生成文本校验失败：字数不足（{len(full_text)} < {scene_plan.target_word_count * 0.5}）",
                stage="validation",
                details={
                    "actual_length": len(full_text),
                    "expected_min_length": int(scene_plan.target_word_count * 0.5),
                    "target_word_count": scene_plan.target_word_count
                }
            )

        # 回滚旧场景产生的知识库更新，防止旧状态污染
        try:
            await self.update_extractor.rollback_scene_updates(
                chapter_number=chapter_plan.chapter_number,
                scene_index=scene_plan.scene_index
            )
        except Exception as e:
            logger.warning(f"[Writer] 回滚旧场景更新失败（可能无更新记录）: {e}")

        # 保存（覆盖原草稿）
        await self._save_scene_draft(
            chapter_number=chapter_plan.chapter_number,
            scene_index=scene_plan.scene_index,
            text=full_text,
            injection_ctx=injection_ctx,
            scene_plan=scene_plan
        )

        # 触发新文本的异步更新提取
        asyncio.create_task(
            self._trigger_update(
                generated_text=full_text,
                chapter_number=chapter_plan.chapter_number,
                scene_index=scene_plan.scene_index,
                scene_plan=scene_plan
            )
        )

        return full_text

    async def regenerate_scene_stream(
        self,
        scene_plan: ScenePlan,
        chapter_plan: ChapterPlan,
        previous_attempt: str,
        feedback: str,
        temperature: float = None
    ) -> AsyncIterator[str]:
        """
        根据反馈重新生成场景（流式迭代器版本）。

        与 regenerate_scene() 功能相同，但返回 AsyncIterator 便于调用方自行处理流式输出。
        注意：此方法不包含回滚和保存逻辑（调用方需要在生成完成后手动调用 regenerate_scene()
        或自行处理保存）。当前实现为简化版，仅负责流式生成。

        Args:
            scene_plan: 场景规划。
            chapter_plan: 章节规划。
            previous_attempt: 上次生成的文本。
            feedback: 修改意见/反馈。
            temperature: 生成温度。

        Yields:
            生成的文本片段（token）。
        """
        self._ensure_initialized()

        injection_ctx = await self.injection_engine.build_context(
            scene_plan=scene_plan,
            chapter_plan=chapter_plan
        )

        base_prompt = self._render_prompt(injection_ctx)
        system_prompt = self._render_system_prompt(injection_ctx)
        feedback_prompt = f"""{base_prompt}

---
【修改要求】

之前生成的文本存在以下问题，请根据反馈修改：

{feedback}

请重新生成本场景，确保解决上述问题。
"""

        full_text = ""
        max_tokens = scene_plan.target_word_count * 2

        # 若未显式传入 temperature，使用 config 中 writer 角色的默认值
        if temperature is None:
            temperature = self.config.get_temperature_for_role('writer')

        async for token in self.llm_client.stream(
            role="writer",
            prompt=feedback_prompt,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            temperature=temperature
        ):
            full_text += token
            yield token

        if not self._validate_generated_text(full_text, scene_plan):
            raise InvalidOutputError(
                f"重新生成文本校验失败：字数不足（{len(full_text)} < {scene_plan.target_word_count * 0.5}）",
                stage="validation",
                details={
                    "actual_length": len(full_text),
                    "expected_min_length": int(scene_plan.target_word_count * 0.5),
                    "target_word_count": scene_plan.target_word_count
                }
            )

        await self._save_scene_draft(
            chapter_number=chapter_plan.chapter_number,
            scene_index=scene_plan.scene_index,
            text=full_text,
            injection_ctx=injection_ctx,
            scene_plan=scene_plan
        )

    async def get_scene_draft(
        self,
        chapter_number: int,
        scene_index: int
    ) -> Optional[dict]:
        """
        获取场景草稿。

        从 StoryDB 中读取指定章节和场景的草稿数据。若草稿不存在或解析失败，返回 None。

        Args:
            chapter_number: 章节编号。
            scene_index: 场景索引。

        Returns:
            草稿数据字典（包含 text, word_count, generated_at 等字段），
            若不存在或读取失败返回 None。
        """
        self._ensure_initialized()

        try:
            draft = await self.story_db.get_chapter_draft(chapter_number)
            if draft and "scenes" in draft:
                for scene in draft["scenes"]:
                    if scene.get("scene_index") == scene_index:
                        return scene
            return None
        except Exception:
            return None
