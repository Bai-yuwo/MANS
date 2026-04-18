"""
writer/writer.py
Writer 核心逻辑

职责：
1. 接收 Injection Engine 组装好的上下文
2. 渲染 Jinja2 提示词模板
3. 调用主力大模型，流式输出文本
4. 触发 Update Extractor 的异步更新
5. 保存场景草稿

设计原则：
- 唯一生成器：只有 Writer 调用主力大模型生成正文
- 流式输出：支持 SSE 实时推送到前端
- 异步更新：生成完成后不等待，立即触发 Update Extractor
- 错误隔离：单场景失败不影响其他场景
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
    """Writer 基础异常"""
    def __init__(self, message: str, stage: str = "", details: dict = None):
        super().__init__(message)
        self.stage = stage
        self.details = details or {}


class PromptRenderError(WriterError):
    """提示词渲染失败"""
    pass


class GenerationError(WriterError):
    """文本生成失败"""
    pass


class InvalidOutputError(WriterError):
    """输出校验失败（字数不足、格式错误等）"""
    pass


class SaveError(WriterError):
    """保存失败"""
    pass


class Writer:
    """
    Writer - 唯一正文生成器
    
    使用示例：
        writer = Writer(project_id="xxx")
        
        # 方式1：使用回调函数流式输出
        async def stream_callback(token: str):
            await send_to_frontend(token)
        
        text = await writer.write_scene(
            scene_plan=scene_plan,
            chapter_plan=chapter_plan,
            stream_callback=stream_callback
        )
        
        # 方式2：使用异步迭代器
        async for token in writer.write_scene_stream(
            scene_plan=scene_plan,
            chapter_plan=chapter_plan
        ):
            print(token, end="")
    """
    
    # 类级缓存：所有 Writer 实例共享同一个 Jinja2 Environment
    _template_env: Optional[Environment] = None

    def __init__(self, project_id: str):
        """
        初始化 Writer

        Args:
            project_id: 项目ID
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
            Writer._template_env = Environment(
                loader=FileSystemLoader("writer/prompts"),
                trim_blocks=True,
                lstrip_blocks=True
            )
        self.template_env = Writer._template_env

        # 延迟初始化依赖
        self._initialized = False
    
    def _ensure_initialized(self):
        """确保依赖组件已初始化"""
        if not self._initialized:
            self.injection_engine = InjectionEngine(self.project_id)
            self.update_extractor = UpdateExtractor(self.project_id)
            self._initialized = True
    
    @property
    def story_db(self):
        """延迟初始化故事库"""
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
        temperature: float = 0.75
    ) -> str:
        """
        生成单个场景文本
        
        标准流程：
        1. Injection Engine 组装上下文
        2. 渲染 Jinja2 提示词
        3. 调用主力大模型，流式生成
        4. 触发异步更新（Update Extractor）
        5. 保存场景草稿
        
        Args:
            scene_plan: 场景规划
            chapter_plan: 章节规划
            stream_callback: 用于将流式 token 发送到前端的回调函数
            sync_update: 是否等待更新完成（默认异步）
            
        Returns:
            生成的完整场景文本
            
        Raises:
            WriterError: 生成过程中任何环节失败
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
            
            # Step 3: 调用主力大模型，流式生成
            full_text = ""
            
            # 估算 token 数（中文字符 * 2 约等于 tokens）
            max_tokens = scene_plan.target_word_count * 2
            
            async for token in self.llm_client.stream(
                role="writer",
                prompt=prompt,
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
        temperature: float = 0.75
    ) -> AsyncIterator[str]:
        """
        生成单个场景文本（异步迭代器版本）
        
        与 write_scene 功能相同，但返回异步迭代器，
        便于调用方自行处理流式输出。
        
        Args:
            scene_plan: 场景规划
            chapter_plan: 章节规划
            sync_update: 是否等待更新完成
            
        Yields:
            生成的文本片段
        """
        self._ensure_initialized()
        
        # Step 1: Injection Engine 组装上下文
        injection_ctx = await self.injection_engine.build_context(
            scene_plan=scene_plan,
            chapter_plan=chapter_plan
        )
        
        # Step 2: 渲染 Jinja2 提示词
        prompt = self._render_prompt(injection_ctx)
        
        # Step 3: 流式生成并 yield
        full_text = ""
        max_tokens = scene_plan.target_word_count * 2
        
        async for token in self.llm_client.stream(
            role="writer",
            prompt=prompt,
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
        渲染 Jinja2 提示词模板
        
        Args:
            injection_ctx: 注入上下文
            
        Returns:
            渲染后的 prompt 字符串
            
        Raises:
            PromptRenderError: 模板渲染失败
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
    
    def _validate_generated_text(self, text: str, scene_plan: ScenePlan) -> bool:
        """
        校验生成的文本是否有效
        
        防御性编程：防止半截文本、空文本污染知识库
        
        Args:
            text: 生成的文本
            scene_plan: 场景规划（包含目标字数）
        
        Returns:
            True 如果文本有效，False 如果应该丢弃
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
        触发 Update Extractor 进行异步更新
        
        Args:
            generated_text: 生成的场景文本
            chapter_number: 章节编号
            scene_index: 场景索引
            scene_plan: 场景规划
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
        保存场景草稿到文件

        使用 StoryDB 的原子性 update_scene_in_draft，全程 FileLockRegistry
        加锁，彻底消除并发提取器或前端自动保存导致的竞态覆盖。

        Args:
            chapter_number: 章节编号
            scene_index: 场景索引
            text: 生成的场景文本
            injection_ctx: 注入上下文
            scene_plan: 场景规划

        Raises:
            SaveError: 保存失败
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
        temperature: float = 0.75
    ) -> str:
        """
        根据反馈重新生成场景
        
        Args:
            scene_plan: 场景规划
            chapter_plan: 章节规划
            previous_attempt: 上次生成的文本
            feedback: 修改意见/反馈
            stream_callback: 流式输出回调
            
        Returns:
            重新生成的场景文本
        """
        self._ensure_initialized()
        
        # 构建带有反馈的特殊 prompt
        injection_ctx = await self.injection_engine.build_context(
            scene_plan=scene_plan,
            chapter_plan=chapter_plan
        )
        
        # 在标准 prompt 基础上添加反馈
        base_prompt = self._render_prompt(injection_ctx)
        
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
        
        async for token in self.llm_client.stream(
            role="writer",
            prompt=feedback_prompt,
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
        temperature: float = 0.75
    ) -> AsyncIterator[str]:
        """
        根据反馈重新生成场景（流式迭代器版本）

        Yields:
            生成的文本片段
        """
        self._ensure_initialized()

        injection_ctx = await self.injection_engine.build_context(
            scene_plan=scene_plan,
            chapter_plan=chapter_plan
        )

        base_prompt = self._render_prompt(injection_ctx)
        feedback_prompt = f"""{base_prompt}

---
【修改要求】

之前生成的文本存在以下问题，请根据反馈修改：

{feedback}

请重新生成本场景，确保解决上述问题。
"""

        full_text = ""
        max_tokens = scene_plan.target_word_count * 2

        async for token in self.llm_client.stream(
            role="writer",
            prompt=feedback_prompt,
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
        获取场景草稿

        Args:
            chapter_number: 章节编号
            scene_index: 场景索引

        Returns:
            草稿数据，如果不存在返回 None
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
