"""
generators/base_generator.py

生成器基类，定义项目初始化阶段各生成器的统一工作流程。

职责边界：
    - 抽象生成流程的通用模式：prompt 构建 → LLM 调用 → 结果解析 → 验证 → 保存 → 向量化。
    - 提供带重试和修正的生成机制，应对 LLM 输出格式错误或验证失败的情况。
    - 支持同步生成（generate）和流式生成（generate_stream）两种模式。
    - 定义生成异常体系，使错误定位更精确。

生成流程说明：
    所有子类（BibleGenerator、CharacterGenerator、OutlineGenerator 等）
    必须实现以下抽象方法，由基类编排成完整的生成流水线：
        1. _build_prompt()：根据输入数据构建发送给 LLM 的提示词。
        2. _parse_response()：将 LLM 返回的文本解析为结构化数据。
        3. _validate_result()：校验解析后的数据是否完整、合法。
        4. _save_result()：将验证通过的数据保存到对应知识库。
        5. _vectorize_result()：将结果向量化存储，供后续检索。

    generate() 方法实现了"生成 → 解析 → 验证"的闭环重试：
        - 若解析失败（如 LLM 返回了非 JSON 内容），自动构造修正提示词并重试。
        - 若验证失败（如缺少必要字段），同样自动修正并重试。
        - 重试次数耗尽后抛出异常，终止生成流程。

    generate_stream() 方法提供流式输出能力：
        - 实时 yield token 给前端展示生成进度。
        - 生成完成后执行解析、验证、保存、向量化。

json_schema 模式：
    子类可通过重写 get_output_schema() 定义输出 JSON Schema。
    基类在调用 LLM 时自动传入此 schema，利用豆包的 json_schema 结构化输出能力
    强制 LLM 返回合法 JSON，显著降低解析失败率。

典型用法（子类实现）：
    class BibleGenerator(BaseGenerator):
        def _build_prompt(self, project_meta, **kwargs):
            return f"基于以下设定生成世界观...{project_meta.core_idea}"

        def _parse_response(self, response):
            return json.loads(response)

        def _validate_result(self, result):
            return "world_rules" in result and len(result["world_rules"]) > 0

        async def _save_result(self, result):
            await self.bible_db.save("world_rules", result)

        async def _vectorize_result(self, result):
            for rule in result["world_rules"]:
                await self.vector_store.upsert("bible_rules", ...)
"""

import json
from abc import ABC, abstractmethod
from typing import Optional, Callable, Any, AsyncIterator
from pathlib import Path

from core.config import get_config
from core.llm_client import LLMClient, LLMError, LLMResponse
from core.schemas import ProjectMeta
from core.logging_config import get_logger, log_exception

logger = get_logger('generators.base_generator')


class GenerationError(Exception):
    """
    生成流程基础异常。

    所有生成异常均继承此类，便于调用方统一捕获。
    包含 stage 字段标识失败发生的环节，details 字段提供上下文信息。

    Attributes:
        stage: 失败环节标识（prompt_build / llm_call / parse / validation / save）。
        details: 包含额外上下文信息的字典。
    """

    def __init__(self, message: str, stage: str = "", details: dict = None):
        super().__init__(message)
        self.stage = stage
        self.details = details or {}


class PromptBuildError(GenerationError):
    """Prompt 构建阶段异常，通常由输入数据缺失或格式错误引起。"""
    pass


class LLMCallError(GenerationError):
    """LLM 调用阶段异常，包括网络错误、认证失败、限流等。"""
    pass


class ParseError(GenerationError):
    """结果解析阶段异常，LLM 返回了无法解析的内容（如非法 JSON）。"""
    pass


class ValidationError(GenerationError):
    """数据验证阶段异常，解析后的数据缺少必要字段或格式不合法。"""
    pass


class BaseGenerator(ABC):
    """
    生成器抽象基类。

    MANS 的项目初始化流程包含多个生成步骤（Bible、人物、大纲、弧线、章节规划），
    每个步骤都遵循相同的模式：构造提示词 → 调用 LLM → 处理结果 → 持久化。
    BaseGenerator 将此模式抽象为可复用的框架，子类只需关注业务逻辑。

    生成角色：
        所有生成操作使用 generator 角色对应的模型（通过 Config 配置）。
        与 writer 角色不同，generator 角色侧重结构化输出和逻辑一致性，
        而非创意和文笔。

    进度报告：
        可通过 set_progress_callback() 设置进度回调函数，
        在生成各阶段（构建 prompt、调用 LLM、解析、验证、保存）触发回调，
        便于前端展示实时进度。
    """

    def __init__(self, project_id: str):
        self.project_id = project_id
        self.config = get_config()
        self.llm_client = LLMClient()
        self._progress_callback: Optional[Callable[[str], None]] = None

    def set_progress_callback(self, callback: Callable[[str], None]):
        """
        设置进度回调函数。

        在生成流程的各关键节点，基类会调用此回调并传入状态描述字符串。

        Args:
            callback: 接收状态描述字符串的回调函数。
        """
        self._progress_callback = callback

    def _report_progress(self, message: str):
        """
        向已注册的回调函数报告进度。

        若未设置回调，此调用无任何副作用。
        """
        if self._progress_callback:
            self._progress_callback(message)

    @abstractmethod
    def _get_generator_name(self) -> str:
        """
        返回生成器名称，用于日志和错误信息中的标识。

        Returns:
            生成器名称字符串（如 "BibleGenerator"）。
        """
        pass

    def get_output_schema(self) -> Optional[dict]:
        """
        返回输出的 JSON Schema 定义。

        子类可重写此方法以启用 json_schema 结构化输出模式。
        当返回非 None 时，基类会在 LLM 调用中传入此 schema，
        强制 LLM 返回严格符合 schema 的 JSON，大幅降低解析失败率。

        Returns:
            JSON Schema 字典，格式为 {"name": "...", "schema": {...}}，
            或 None 表示不使用结构化输出。
        """
        return None

    def get_system_prompt(self) -> str:
        """
        返回生成器的系统提示词。

        子类可重写此方法以提供针对特定生成任务的 system prompt。
        默认返回一个通用的结构化输出指导，要求 LLM 严格按 JSON 格式返回。
        """
        return (
            "你是一个专业的中文网络小说创作助手，擅长根据给定信息生成结构化的创作素材。"
            "你必须严格按照要求的 JSON 格式输出，不要输出任何其他内容（如 markdown 代码块、解释说明等）。"
            "JSON 字符串值内部如需引号，必须使用英文单引号（'），严禁使用双引号或中文引号。"
        )

    @abstractmethod
    def _build_prompt(self, **kwargs) -> str:
        """
        构建发送给 LLM 的提示词。

        Args:
            **kwargs: 生成所需的输入数据，由子类定义具体参数。

        Returns:
            完整的 prompt 字符串。
        """
        pass

    @abstractmethod
    def _parse_response(self, response: str) -> Any:
        """
        将 LLM 返回的原始文本解析为结构化数据。

        Args:
            response: LLM 返回的原始文本（通常期望为 JSON 格式）。

        Returns:
            解析后的数据结构（类型由子类决定）。

        Raises:
            ParseError: 解析失败时抛出，触发基类的自动重试机制。
        """
        pass

    @abstractmethod
    def _validate_result(self, result: Any) -> bool:
        """
        验证解析后的结果是否完整、合法。

        Args:
            result: _parse_response() 返回的数据结构。

        Returns:
            True 表示验证通过，False 表示验证失败（触发重试）。

        Raises:
            ValidationError: 验证失败时可选择抛出此异常，提供具体错误信息。
        """
        pass

    @abstractmethod
    async def _save_result(self, result: Any) -> None:
        """
        将验证通过的结果保存到对应知识库。

        Args:
            result: 验证通过的数据结构。
        """
        pass

    @abstractmethod
    async def _vectorize_result(self, result: Any) -> None:
        """
        将结果向量化存储到向量数据库。

        向量化失败不应影响主流程，子类应在异常时记录警告日志而非抛出异常。

        Args:
            result: 已保存的结果数据结构。
        """
        pass

    async def generate(self, **kwargs) -> Any:
        """
        执行完整的生成流程（同步模式）。

        标准流水线：
            1. 构建 prompt（_build_prompt）。
            2. 调用 LLM（call_with_retry），自动处理限流、超时等异常并重试。
            3. 解析响应（_parse_response）。
            4. 验证结果（_validate_result）。
            5. 保存到知识库（_save_result）。
            6. 向量化存储（_vectorize_result）。

        闭环重试：
            步骤 3 或 4 失败时，基类会自动构造修正提示词并重新调用 LLM。
            修正提示词包含原始 prompt 加上前次失败的错误信息，指导 LLM 修正输出格式。
            重试次数为 max_retries + 1（默认 4 次）。

        Args:
            **kwargs: 传递给 _build_prompt() 的输入数据，
                      以及可选的生成参数（temperature、max_retries、max_tokens 等）。

        Returns:
            生成的结果数据。

        Raises:
            GenerationError: 生成流程中任何环节失败且重试耗尽时抛出。
        """
        generator_name = self._get_generator_name()
        self._report_progress(f"[{generator_name}] 开始生成...")

        # 从 config 获取 generator 角色的默认值
        temperature = kwargs.pop('temperature', self.config.get_temperature_for_role('generator'))
        max_retries = kwargs.pop('max_retries', 3)
        max_tokens = kwargs.pop('max_tokens', self.config.get_max_tokens_for_role('generator'))
        connect_timeout = kwargs.pop('connect_timeout', 30)
        sock_read_timeout = kwargs.pop('sock_read_timeout', 60)
        total_timeout = kwargs.pop('total_timeout', 600)

        try:
            self._report_progress(f"[{generator_name}] 构建 prompt...")
            prompt = self._build_prompt(**kwargs)
        except Exception as e:
            raise PromptBuildError(
                f"构建 prompt 失败: {str(e)}",
                stage="prompt_build",
                details={"error": str(e)}
            )

        current_prompt = prompt
        last_error = None
        validation_retries = max(1, max_retries + 1)

        for attempt in range(validation_retries):
            try:
                self._report_progress(f"[{generator_name}] 调用大模型... (尝试 {attempt + 1}/{validation_retries})")

                json_schema = self.get_output_schema()
                response_format = "json_schema" if json_schema else None

                system_prompt = self.get_system_prompt()
                response: LLMResponse = await self.llm_client.call_with_retry(
                    role="generator",
                    prompt=current_prompt,
                    system_prompt=system_prompt,
                    response_format=response_format,
                    json_schema=json_schema,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    max_retries=max_retries,
                    connect_timeout=connect_timeout,
                    sock_read_timeout=sock_read_timeout,
                    total_timeout=total_timeout
                )
            except LLMError as e:
                raise LLMCallError(
                    f"LLM 调用失败: {str(e)}",
                    stage="llm_call",
                    details={"provider": e.provider, "model": e.model}
                )

            try:
                self._report_progress(f"[{generator_name}] 解析响应...")
                result = self._parse_response(response.content)
            except Exception as e:
                if attempt < validation_retries - 1:
                    last_error = f"JSON 解析失败: {str(e)}"
                    self._report_progress(f"[{generator_name}] {last_error}，正在重试...")
                    current_prompt = prompt + f"\n\n【修正要求】之前输出存在格式错误：{last_error}。请确保输出严格的 JSON 格式，不要包含 markdown 代码块或其他说明文字。"
                    continue
                raise ParseError(
                    f"解析响应失败: {str(e)}",
                    stage="parse",
                    details={"raw_response": response.content[:500]}
                )

            try:
                self._report_progress(f"[{generator_name}] 验证结果...")
                is_valid = self._validate_result(result)
                if not is_valid:
                    raise ValidationError(
                        "结果验证未通过",
                        stage="validation",
                        details={"result": str(result)[:500]}
                    )
                break
            except ValidationError as e:
                if attempt < validation_retries - 1:
                    last_error = str(e)
                    self._report_progress(f"[{generator_name}] 验证失败: {last_error}，正在重试...")
                    current_prompt = prompt + f"\n\n【修正要求】之前输出未通过数据验证：{last_error}。请根据要求修正后重新输出严格的 JSON。"
                    continue
                raise
            except Exception as e:
                if attempt < validation_retries - 1:
                    last_error = f"验证过程出错: {str(e)}"
                    self._report_progress(f"[{generator_name}] {last_error}，正在重试...")
                    current_prompt = prompt + f"\n\n【修正要求】之前输出在验证时发生错误：{last_error}。请修正后重新输出。"
                    continue
                raise ValidationError(
                    f"验证过程出错: {str(e)}",
                    stage="validation",
                    details={"error": str(e)}
                )

        try:
            self._report_progress(f"[{generator_name}] 保存到知识库...")
            await self._save_result(result)
        except Exception as e:
            raise GenerationError(
                f"保存结果失败: {str(e)}",
                stage="save",
                details={"error": str(e)}
            )

        try:
            self._report_progress(f"[{generator_name}] 向量化存储...")
            await self._vectorize_result(result)
        except Exception as e:
            self._report_progress(f"[{generator_name}] 警告: 向量化失败 - {str(e)}")

        self._report_progress(f"[{generator_name}] 生成完成！")
        return result

    async def generate_stream(self, **kwargs) -> AsyncIterator[dict]:
        """
        流式生成方法，逐块 yield 进度和 token 事件。

        实现策略：
            由于 json_schema 结构化输出模式下，底层 API 不会通过
            response.output_text.delta 事件逐 token 返回文本，因此
            本方法改用 call() 非流式调用获取完整响应，然后将响应文本
            拆分为小块逐块 yield，模拟打字机效果。

            这样既能保证 json_schema 的解析成功率，又能让前端看到
            实时的生成过程。

        事件类型：
            - progress：生成阶段状态更新（如"构建 prompt..."）。
            - token：LLM 生成的文本片段（模拟流式）。
            - complete：生成流程全部完成，附带最终数据结构。
            - error：生成过程中发生错误。

        Args:
            **kwargs: 与 generate() 相同的输入数据和生成参数。

        Yields:
            事件字典，包含 type 字段标识事件类型及相关数据。

        Raises:
            流式生成中的异常会被包装为 error 事件 yield 后重新抛出。
        """
        import asyncio

        generator_name = self._get_generator_name()

        # 从 config 获取 generator 角色的默认值
        temperature = kwargs.pop('temperature', self.config.get_temperature_for_role('generator'))
        max_tokens = kwargs.pop('max_tokens', self.config.get_max_tokens_for_role('generator'))
        connect_timeout = kwargs.pop('connect_timeout', 30)
        sock_read_timeout = kwargs.pop('sock_read_timeout', 60)
        total_timeout = kwargs.pop('total_timeout', 600)

        try:
            self._report_progress(f"[{generator_name}] 构建 prompt...")
            yield {"type": "progress", "message": f"[{generator_name}] 构建 prompt..."}
            prompt = self._build_prompt(**kwargs)

            # 获取模型信息，用于 start 事件元数据
            model_name, _ = self.llm_client.config.get_model_for_role("generator")

            # 推送 start 事件，包含调用元数据
            yield {
                "type": "start",
                "message": f"[{generator_name}] 开始生成...",
                "prompt_length": len(prompt),
                "model": model_name,
                "role": "generator",
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
            # 强制让出控制权，确保 start 事件被 flush 到客户端
            await asyncio.sleep(0)

            self._report_progress(f"[{generator_name}] 调用大模型...")
            yield {"type": "progress", "message": f"[{generator_name}] 调用大模型..."}
            await asyncio.sleep(0)

            json_schema = self.get_output_schema()
            system_prompt = self.get_system_prompt()

            # 使用 call() 而非 stream()：json_schema 模式下 stream 不返回逐 token 事件
            response = await self.llm_client.call_with_retry(
                role="generator",
                prompt=prompt,
                system_prompt=system_prompt,
                json_schema=json_schema,
                max_tokens=max_tokens,
                temperature=temperature,
                connect_timeout=connect_timeout,
                sock_read_timeout=sock_read_timeout,
                total_timeout=total_timeout
            )

            full_content = response.content
            token_count = 0
            chunk_size = 4  # 每 4 个字符 yield 一次，模拟中文打字机效果

            # 模拟流式输出：逐字符/逐词 yield
            for i in range(0, len(full_content), chunk_size):
                chunk = full_content[i:i + chunk_size]
                token_count += 1
                yield {"type": "token", "content": chunk}
                # 极小延迟模拟打字机效果（每 10 个 chunk 暂停一次，避免阻塞事件循环）
                if token_count % 10 == 0:
                    await asyncio.sleep(0)

            self._report_progress(f"[{generator_name}] 收到 {len(full_content)} 字符")
            yield {"type": "progress", "message": f"[{generator_name}] 解析响应..."}
            result = self._parse_response(full_content)

            yield {"type": "progress", "message": f"[{generator_name}] 验证结果..."}
            is_valid = self._validate_result(result)
            if not is_valid:
                raise ValidationError("结果验证未通过", stage="validation")

            yield {"type": "progress", "message": f"[{generator_name}] 保存到知识库..."}
            await self._save_result(result)

            try:
                await self._vectorize_result(result)
            except Exception as e:
                self._report_progress(f"[{generator_name}] 警告: 向量化失败 - {str(e)}")

            yield {
                "type": "complete",
                "message": f"[{generator_name}] 生成完成！",
                "data": result
            }

        except Exception as e:
            yield {"type": "error", "error": str(e)}
            # 不再重新抛出，由外层 event_generator 统一处理流关闭

    def _clean_json_response(self, response: str) -> str:
        """
        清理 LLM 返回的 JSON 响应文本。

        处理常见问题：
            1. 去除首尾空白字符。
            2. 去除 markdown 代码块标记（```json ... ```）。
            3. 去除 UTF-8 BOM 标记。

        Args:
            response: 原始响应文本。

        Returns:
            清理后的字符串，更适合直接传入 json.loads()。
        """
        cleaned = response.strip()

        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        elif cleaned.startswith("```"):
            cleaned = cleaned[3:]

        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]

        cleaned = cleaned.strip()

        if cleaned.startswith("\ufeff"):
            cleaned = cleaned[1:]

        return cleaned

    def _safe_json_parse(self, response: str) -> Any:
        """
        安全解析 JSON，带自动修复和详细错误信息。

        修复策略：
            1. 先调用 _clean_json_response() 清理格式污染。
            2. 尝试标准 json.loads() 解析。
            3. 若失败且检测到括号不匹配（如缺少闭合 } 或 ]），自动补全后重试。
            4. 若仍失败，抛出 ParseError 并包含清理后的响应片段，便于调试。

        Args:
            response: JSON 字符串。

        Returns:
            解析后的 Python 对象。

        Raises:
            ParseError: 所有修复尝试均失败时抛出。
        """
        cleaned = self._clean_json_response(response)

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            if cleaned.count("{") > cleaned.count("}"):
                cleaned += "}"
            elif cleaned.count("[") > cleaned.count("]"):
                cleaned += "]"

            try:
                return json.loads(cleaned)
            except json.JSONDecodeError:
                raise ParseError(
                    f"JSON 解析失败: {str(e)}",
                    stage="parse",
                    details={
                        "error": str(e),
                        "cleaned_response": cleaned[:500]
                    }
                )
