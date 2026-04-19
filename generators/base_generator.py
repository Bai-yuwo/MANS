"""
generators/base_generator.py
生成器基类

设计原则：
1. 统一接口：所有生成器继承 BaseGenerator，实现 generate() 方法
2. 模板化流程：prompt构建 → LLM调用 → 结果解析 → 验证 → 写入 → 向量化
3. 错误处理：每个环节有明确的异常类型和重试机制
4. 进度回调：支持传入回调函数报告生成进度
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
    """生成过程基础异常"""
    def __init__(self, message: str, stage: str = "", details: dict = None):
        super().__init__(message)
        self.stage = stage
        self.details = details or {}


class PromptBuildError(GenerationError):
    """Prompt构建失败"""
    pass


class LLMCallError(GenerationError):
    """LLM调用失败"""
    pass


class ParseError(GenerationError):
    """结果解析失败"""
    pass


class ValidationError(GenerationError):
    """数据验证失败"""
    pass


class BaseGenerator(ABC):
    """
    生成器基类
    
    子类必须实现：
    - _build_prompt()：构建生成 prompt
    - _parse_response()：解析 LLM 响应
    - _validate_result()：验证结果完整性
    - _save_result()：保存到知识库
    
    使用示例：
        generator = BibleGenerator(project_id="xxx")
        result = await generator.generate(
            project_meta=project_meta,
            progress_callback=lambda msg: print(msg)
        )
    """
    
    def __init__(self, project_id: str):
        """
        初始化生成器
        
        Args:
            project_id: 项目ID
        """
        self.project_id = project_id
        self.config = get_config()
        self.llm_client = LLMClient()
        self._progress_callback: Optional[Callable[[str], None]] = None
    
    def set_progress_callback(self, callback: Callable[[str], None]):
        """设置进度回调函数"""
        self._progress_callback = callback
    
    def _report_progress(self, message: str):
        """报告进度"""
        if self._progress_callback:
            self._progress_callback(message)
    
    @abstractmethod
    def _get_generator_name(self) -> str:
        """返回生成器名称（用于日志和错误信息）"""
        pass
    
    def get_output_schema(self) -> Optional[dict]:
        """
        返回输出 JSON Schema（用于 json_schema 模式）
        
        子类可以重写此方法来定义严格的结构校验。
        返回 None 则不使用 json_schema 模式。
        
        Returns:
            JSON Schema 字典，格式：
            {
                "name": "output_schema",
                "schema": { ... }
            }
            或 None
        """
        return None
    
    @abstractmethod
    def _build_prompt(self, **kwargs) -> str:
        """
        构建生成 prompt
        
        Args:
            **kwargs: 生成所需的输入数据
            
        Returns:
            完整的 prompt 字符串
        """
        pass
    
    @abstractmethod
    def _parse_response(self, response: str) -> Any:
        """
        解析 LLM 响应
        
        Args:
            response: LLM 返回的原始文本
            
        Returns:
            解析后的数据结构
            
        Raises:
            ParseError: 解析失败时抛出
        """
        pass
    
    @abstractmethod
    def _validate_result(self, result: Any) -> bool:
        """
        验证结果完整性
        
        Args:
            result: 解析后的结果
            
        Returns:
            验证是否通过
            
        Raises:
            ValidationError: 验证失败时抛出，包含具体错误信息
        """
        pass
    
    @abstractmethod
    async def _save_result(self, result: Any) -> None:
        """
        保存结果到知识库
        
        Args:
            result: 验证通过的结果
        """
        pass
    
    @abstractmethod
    async def _vectorize_result(self, result: Any) -> None:
        """
        将结果向量化存储
        
        Args:
            result: 已保存的结果
        """
        pass
    
    async def generate(self, **kwargs) -> Any:
        """
        执行生成流程

        标准流程：
        1. 构建 prompt
        2. 调用 LLM（带重试）
        3. 解析响应
        4. 验证结果
        5. 保存到知识库
        6. 触发向量化

        Args:
            **kwargs: 生成所需的输入数据

        Returns:
            生成的结果数据

        Raises:
            GenerationError: 生成过程中任何环节失败
        """
        generator_name = self._get_generator_name()
        self._report_progress(f"[{generator_name}] 开始生成...")

        # 提取生成参数
        temperature = kwargs.pop('temperature', 0.7)
        max_retries = kwargs.pop('max_retries', 3)
        max_tokens = kwargs.pop('max_tokens', 4000)
        connect_timeout = kwargs.pop('connect_timeout', 30)
        sock_read_timeout = kwargs.pop('sock_read_timeout', 60)
        total_timeout = kwargs.pop('total_timeout', 600)

        # Step 1: 构建 prompt
        try:
            self._report_progress(f"[{generator_name}] 构建 prompt...")
            prompt = self._build_prompt(**kwargs)
        except Exception as e:
            raise PromptBuildError(
                f"构建 prompt 失败: {str(e)}",
                stage="prompt_build",
                details={"error": str(e)}
            )

        # Step 2-4: 调用 LLM、解析、验证（带重试）
        current_prompt = prompt
        last_error = None
        validation_retries = max(1, max_retries + 1)

        for attempt in range(validation_retries):
            try:
                self._report_progress(f"[{generator_name}] 调用大模型... (尝试 {attempt + 1}/{validation_retries})")

                # 获取 JSON Schema（如果子类定义了）
                json_schema = self.get_output_schema()

                # 使用 json_schema 模式（豆包官方推荐）
                response_format = "json_schema" if json_schema else None

                response: LLMResponse = await self.llm_client.call_with_retry(
                    role="generator",
                    prompt=current_prompt,
                    system_prompt="",
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

            # Step 3: 解析响应
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

            # Step 4: 验证结果
            try:
                self._report_progress(f"[{generator_name}] 验证结果...")
                is_valid = self._validate_result(result)
                if not is_valid:
                    raise ValidationError(
                        "结果验证未通过",
                        stage="validation",
                        details={"result": str(result)[:500]}
                    )
                # 验证通过，跳出重试循环
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
        
        # Step 5: 保存到知识库
        try:
            self._report_progress(f"[{generator_name}] 保存到知识库...")
            await self._save_result(result)
        except Exception as e:
            raise GenerationError(
                f"保存结果失败: {str(e)}",
                stage="save",
                details={"error": str(e)}
            )
        
        # Step 6: 向量化
        try:
            self._report_progress(f"[{generator_name}] 向量化存储...")
            await self._vectorize_result(result)
        except Exception as e:
            # 向量化失败不影响主流程，记录警告
            self._report_progress(f"[{generator_name}] 警告: 向量化失败 - {str(e)}")
        
        self._report_progress(f"[{generator_name}] 生成完成！")
        return result
    
    async def generate_stream(self, **kwargs) -> AsyncIterator[dict]:
        """
        流式生成方法，逐块yield进度和token事件
        
        使用示例：
            async for event in generator.generate_stream(project_meta=meta):
                if event['type'] == 'token':
                    print(event['content'], end='')
        
        Yields:
            dict: 事件数据
                - type: "progress" | "token" | "complete" | "error"
                - message/data/content: 事件内容
        """
        generator_name = self._get_generator_name()

        # 提取生成参数
        temperature = kwargs.pop('temperature', 0.7)
        max_tokens = kwargs.pop('max_tokens', 4000)
        connect_timeout = kwargs.pop('connect_timeout', 30)
        sock_read_timeout = kwargs.pop('sock_read_timeout', 60)
        total_timeout = kwargs.pop('total_timeout', 600)

        try:
            # Step 1: 构建 prompt
            self._report_progress(f"[{generator_name}] 构建 prompt...")
            yield {"type": "progress", "message": f"[{generator_name}] 构建 prompt..."}
            prompt = self._build_prompt(**kwargs)

            # Step 2: 流式调用 LLM
            self._report_progress(f"[{generator_name}] 调用大模型...")
            yield {"type": "progress", "message": f"[{generator_name}] 调用大模型..."}

            full_content = ""
            token_count = 0

            # 获取 JSON Schema（如果子类定义了）
            json_schema = self.get_output_schema()

            # 使用分离的超时策略：
            # - connect_timeout: 30s（快速判定连接是否成功）
            # - sock_read_timeout: 60s（token之间的最大间隔）
            # - total_timeout: 600s（支持长生成，10分钟）
            async for token in self.llm_client.stream(
                role="generator",
                prompt=prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                json_schema=json_schema,
                connect_timeout=connect_timeout,
                sock_read_timeout=sock_read_timeout,
                total_timeout=total_timeout
            ):
                full_content += token
                token_count += 1

                # yield token事件
                yield {
                    "type": "token",
                    "content": token
                }
            
            self._report_progress(f"[{generator_name}] 收到 {token_count} 个token")
            
            # Step 3: 解析响应
            yield {"type": "progress", "message": f"[{generator_name}] 解析响应..."}
            result = self._parse_response(full_content)
            
            # Step 4: 验证结果
            yield {"type": "progress", "message": f"[{generator_name}] 验证结果..."}
            is_valid = self._validate_result(result)
            if not is_valid:
                raise ValidationError("结果验证未通过", stage="validation")
            
            # Step 5: 保存到知识库
            yield {"type": "progress", "message": f"[{generator_name}] 保存到知识库..."}
            await self._save_result(result)
            
            # Step 6: 向量化
            try:
                await self._vectorize_result(result)
            except Exception as e:
                self._report_progress(f"[{generator_name}] 警告: 向量化失败 - {str(e)}")
            
            # 完成事件
            yield {
                "type": "complete",
                "message": f"[{generator_name}] 生成完成！",
                "data": result
            }
            
        except Exception as e:
            # 错误事件
            yield {
                "type": "error",
                "error": str(e)
            }
            raise
    
    def _clean_json_response(self, response: str) -> str:
        """
        清理 LLM 返回的 JSON 响应
        
        处理常见格式问题：
        - 去除 markdown 代码块标记
        - 去除首尾空白
        - 处理可能的 BOM 标记
        
        Args:
            response: 原始响应文本
            
        Returns:
            清理后的 JSON 字符串
        """
        # 去除首尾空白
        cleaned = response.strip()
        
        # 去除 markdown 代码块标记
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        elif cleaned.startswith("```"):
            cleaned = cleaned[3:]
        
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        
        # 去除首尾空白（再次）
        cleaned = cleaned.strip()
        
        # 去除 BOM 标记
        if cleaned.startswith("\ufeff"):
            cleaned = cleaned[1:]
        
        return cleaned
    
    def _safe_json_parse(self, response: str) -> Any:
        """
        安全解析 JSON，带错误处理
        
        Args:
            response: JSON 字符串
            
        Returns:
            解析后的数据
            
        Raises:
            ParseError: 解析失败时抛出
        """
        cleaned = self._clean_json_response(response)
        
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            # 尝试修复常见问题后重试
            # 问题1：末尾缺少逗号
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
