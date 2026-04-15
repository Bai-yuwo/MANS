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
from knowledge_bases.story_db import StoryDB


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
        self.story_db: Optional[StoryDB] = None
        
        # 初始化 Jinja2 模板环境
        self.template_env = Environment(
            loader=FileSystemLoader("writer/prompts"),
            trim_blocks=True,
            lstrip_blocks=True
        )
        
        # 延迟初始化依赖
        self._initialized = False
    
    def _ensure_initialized(self):
        """确保依赖组件已初始化"""
        if not self._initialized:
            self.injection_engine = InjectionEngine(self.project_id)
            self.update_extractor = UpdateExtractor(self.project_id)
            self.story_db = StoryDB(self.project_id)
            self._initialized = True
    
    async def write_scene(
        self,
        scene_plan: ScenePlan,
        chapter_plan: ChapterPlan,
        stream_callback: Optional[Callable[[str], None]] = None,
        sync_update: bool = False
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
                temperature=0.75  # 稍微增加创造性
            ):
                full_text += token
                
                # 实时推送到前端
                if stream_callback:
                    await stream_callback(token)
            
            # Step 4: 触发异步更新
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
            
            # Step 5: 保存场景草稿
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
        sync_update: bool = False
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
            temperature=0.75
        ):
            full_text += token
            yield token
        
        # Step 4: 触发异步更新（在生成完成后）
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
        
        # Step 5: 保存场景草稿
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
            # 构建草稿数据
            draft_data = {
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
            
            # 保存到 story_db
            self.story_db.append_scene_draft(chapter_number, draft_data)
            
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
        stream_callback: Optional[Callable[[str], None]] = None
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
            temperature=0.75
        ):
            full_text += token
            if stream_callback:
                await stream_callback(token)
        
        # 保存（覆盖原草稿）
        await self._save_scene_draft(
            chapter_number=chapter_plan.chapter_number,
            scene_index=scene_plan.scene_index,
            text=full_text,
            injection_ctx=injection_ctx,
            scene_plan=scene_plan
        )
        
        return full_text
    
    def get_scene_draft(
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
            draft = self.story_db.get_chapter_draft(chapter_number)
            if draft and "scenes" in draft:
                for scene in draft["scenes"]:
                    if scene.get("scene_index") == scene_index:
                        return scene
            return None
        except Exception:
            return None
