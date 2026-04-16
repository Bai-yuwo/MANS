"""
core/update_extractor.py
异步更新器 - 从生成文本中提取状态变更

设计原则：
1. 异步执行：不阻塞写作流程，与 Injection Engine 并发
2. 结构化提取：使用 LLM 从文本提取结构化更新
3. 并发写入：同时更新多个知识库
4. 向量化存储：将生成文本存入向量库供后续检索
5. 错误隔离：单知识库失败不影响其他更新
"""

import asyncio
import json
import re
from typing import Optional
from pathlib import Path
from datetime import datetime

import aiofiles

from core.config import get_config
from core.schemas import (
    ScenePlan, ExtractedUpdates, CharacterStateUpdate,
    WorldRule, ForeshadowingItem, CharacterCard
)
from core.llm_client import LLMClient, quick_call
from core.logging_config import get_logger, log_exception

logger = get_logger('core.update_extractor')


# ============================================================
# JSON 清洗工具函数
# ============================================================

def clean_json_response(response: str) -> str:
    """
    清洗 LLM 返回的 JSON 字符串
    
    处理以下常见问题：
    1. Markdown 代码块包裹：```json { ... } ```
    2. 前后空白字符
    3. BOM 字符
    
    Args:
        response: LLM 原始响应
    
    Returns:
        清洗后的纯 JSON 字符串
    """
    # 去除前后空白
    text = response.strip()
    
    # 去除 BOM 字符
    text = text.lstrip('\ufeff')
    
    # 去除 Markdown 代码块包裹
    # 匹配 ```json 或 ``` 开头，到 ``` 结尾
    pattern = r'^```(?:json)?\s*\n?(.*?)\n?```\s*$'
    match = re.match(pattern, text, re.DOTALL)
    if match:
        text = match.group(1).strip()
    
    return text


class UpdateExtractor:
    """
    异步更新提取器
    
    职责：Writer 生成完成后，异步提取状态变更并更新知识库
    
    使用示例：
        extractor = UpdateExtractor(project_id="xxx")
        
        # 异步触发（不等待）
        asyncio.create_task(
            extractor.extract_and_update(
                generated_text=text,
                chapter_number=5,
                scene_index=0,
                scene_plan=scene_plan
            )
        )
    """
    
    def __init__(self, project_id: str):
        self.project_id = project_id
        self.config = get_config()
        self.llm_client = LLMClient()
        
        # 项目路径
        self.project_path = Path(self.config.WORKSPACE_PATH) / project_id
        
        # 知识库引用（延迟初始化）
        self._character_db = None
        self._bible_db = None
        self._foreshadowing_db = None
        self._vector_store = None
    
    @property
    def character_db(self):
        """延迟初始化人物库"""
        if self._character_db is None:
            from knowledge_bases.character_db import CharacterDB
            self._character_db = CharacterDB(self.project_id)
        return self._character_db
    
    @property
    def bible_db(self):
        """延迟初始化世界观库"""
        if self._bible_db is None:
            from knowledge_bases.bible_db import BibleDB
            self._bible_db = BibleDB(self.project_id)
        return self._bible_db
    
    @property
    def foreshadowing_db(self):
        """延迟初始化伏笔库"""
        if self._foreshadowing_db is None:
            from knowledge_bases.foreshadowing_db import ForeshadowingDB
            self._foreshadowing_db = ForeshadowingDB(self.project_id)
        return self._foreshadowing_db
    
    @property
    def vector_store(self):
        """延迟初始化向量存储"""
        if self._vector_store is None:
            from vector_store.store import VectorStore
            self._vector_store = VectorStore(self.project_id)
        return self._vector_store
    
    async def _do_extract_and_update(
        self,
        generated_text: str,
        chapter_number: int,
        scene_index: int,
        scene_plan: ScenePlan
    ) -> ExtractedUpdates:
        """实际执行提取和更新的内部方法"""
        # 步骤 1：提取结构化更新
        updates = await self._extract_updates(
            generated_text=generated_text,
            chapter_number=chapter_number,
            scene_index=scene_index,
            scene_plan=scene_plan
        )
        
        # 步骤 2：并发写入各知识库
        await self._apply_updates(updates)
        
        # 步骤 3：向量化存储（如果启用）
        if self.config.ENABLE_VECTOR_SEARCH:
            await self._vectorize_scene(
                generated_text=generated_text,
                chapter_number=chapter_number,
                scene_index=scene_index,
                scene_plan=scene_plan
            )
        
        # 步骤 4：保存更新记录
        await self._save_update_record(updates)
        
        return updates

    async def extract_and_update(
        self,
        generated_text: str,
        chapter_number: int,
        scene_index: int,
        scene_plan: ScenePlan,
        sync: bool = False
    ) -> ExtractedUpdates:
        """
        从生成的场景文本中提取状态变更并更新知识库
        
        Args:
            generated_text: 生成的场景文本
            chapter_number: 章节号
            scene_index: 场景序号
            scene_plan: 场景规划
            sync: 是否同步执行（默认异步，强一致性场景可设为 True）
        
        Returns:
            ExtractedUpdates 对象
        """
        if sync:
            # 强一致性：直接等待完成
            return await self._do_extract_and_update(
                generated_text=generated_text,
                chapter_number=chapter_number,
                scene_index=scene_index,
                scene_plan=scene_plan
            )
        else:
            # 默认异步：创建后台任务，不阻塞调用者
            asyncio.create_task(
                self._do_extract_and_update(
                    generated_text=generated_text,
                    chapter_number=chapter_number,
                    scene_index=scene_index,
                    scene_plan=scene_plan
                )
            )
            # 返回轻量标记（实际结果在后台处理）
            return ExtractedUpdates(
                source_chapter=chapter_number,
                source_scene_index=scene_index
            )
    
    async def _extract_updates(
        self,
        generated_text: str,
        chapter_number: int,
        scene_index: int,
        scene_plan: ScenePlan
    ) -> ExtractedUpdates:
        """使用 LLM 从文本中提取结构化更新"""
        
        # 获取当前人物状态（用于对比）
        current_characters = {}
        for name in scene_plan.present_characters:
            char = self.character_db.get_character(name)
            if char:
                current_characters[name] = {
                    "location": char.current_location,
                    "cultivation": char.cultivation.realm if char.cultivation else "",
                    "emotion": char.current_emotion,
                    "goals": char.active_goals
                }
        
        # 构建提取提示词
        extraction_prompt = f"""分析以下小说场景文本，提取所有对故事状态的变更。
输出严格的 JSON 格式，不要输出任何其他内容。

场景背景：{scene_plan.intent}
出场人物：{', '.join(scene_plan.present_characters)}
情绪基调：{scene_plan.emotional_tone}

当前人物状态（用于对比）：
{json.dumps(current_characters, ensure_ascii=False, indent=2)}

场景文本：
{generated_text[:3000]}  # 限制长度避免超出 token 限制

请提取以下信息：
1. 人物状态变化（位置/修为/情绪/目标/关系）
2. 新发现或确认的世界规则
3. 伏笔状态变化（planted→hinted 或 triggered 或 resolved）
4. 新埋入的伏笔（如有）
5. 发现的潜在矛盾或问题

输出 JSON 格式：
{{
  "character_updates": [
    {{
      "character_id": "人物ID",
      "character_name": "人物名",
      "location_change": "新位置（如有变化）",
      "cultivation_change": "修为变化（如有）",
      "emotion_change": "情绪变化（如有）",
      "goal_updates": ["新增目标", "完成的目标"],
      "relationship_updates": [{{"target": "目标人物", "change": "关系变化"}}]
    }}
  ],
  "new_world_rules": [
    {{
      "category": "cultivation/geography/social/physics/special",
      "content": "规则描述",
      "importance": "critical/major/minor"
    }}
  ],
  "foreshadowing_status_changes": [
    {{
      "id": "伏笔ID",
      "new_status": "hinted/triggered/resolved",
      "notes": "变化说明"
    }}
  ],
  "new_foreshadowing": [
    {{
      "type": "plot/character/world/emotional",
      "description": "伏笔描述",
      "trigger_range": [开始章节, 结束章节],
      "urgency": "low/medium/high"
    }}
  ],
  "implicit_issues": ["发现的矛盾或问题"]
}}

如果没有某类变更，返回空数组。"""
        
        try:
            # 调用 Extract 模型
            from core.llm_client import LLMClient
            client = LLMClient()
            
            # 定义 JSON Schema
            extraction_schema = {
                "name": "extraction_output",
                "schema": {
                    "type": "object",
                    "properties": {
                        "character_updates": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "character_id": {"type": "string"},
                                    "character_name": {"type": "string"},
                                    "location_change": {"type": "string"},
                                    "cultivation_change": {"type": "string"},
                                    "emotion_change": {"type": "string"},
                                    "goal_updates": {
                                        "type": "array",
                                        "items": {"type": "string"}
                                    },
                                    "relationship_updates": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "target": {"type": "string"},
                                                "change": {"type": "string"}
                                            },
                                            "required": ["target", "change"]
                                        }
                                    }
                                },
                                "required": ["character_id", "character_name"]
                            }
                        },
                        "new_world_rules": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "category": {"type": "string"},
                                    "content": {"type": "string"},
                                    "importance": {"type": "string"}
                                },
                                "required": ["content"]
                            }
                        },
                        "foreshadowing_status_changes": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "new_status": {"type": "string"},
                                    "notes": {"type": "string"}
                                },
                                "required": ["id", "new_status"]
                            }
                        },
                        "new_foreshadowing": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "type": {"type": "string"},
                                    "description": {"type": "string"},
                                    "trigger_range": {
                                        "type": "array",
                                        "items": {"type": "integer"}
                                    },
                                    "urgency": {"type": "string"}
                                },
                                "required": ["type", "description"]
                            }
                        },
                        "implicit_issues": {
                            "type": "array",
                            "items": {"type": "string"}
                        }
                    },
                    "required": ["character_updates", "new_world_rules", "foreshadowing_status_changes", "new_foreshadowing", "implicit_issues"]
                }
            }
            
            response_obj = await client.call_with_retry(
                role="extract",
                prompt=extraction_prompt,
                max_tokens=2000,
                response_format="json_schema",
                json_schema=extraction_schema
            )
            
            # 解析 JSON 响应
            data = json.loads(response_obj.content)
            
            # 构建 ExtractedUpdates
            updates = ExtractedUpdates(
                source_chapter=chapter_number,
                source_scene_index=scene_index,
                character_updates=[
                    CharacterStateUpdate(**cu) 
                    for cu in data.get("character_updates", [])
                ],
                new_world_rules=[
                    WorldRule(
                        source_chapter=chapter_number,
                        **wr
                    )
                    for wr in data.get("new_world_rules", [])
                ],
                foreshadowing_status_changes=data.get("foreshadowing_status_changes", []),
                new_foreshadowing=[
                    ForeshadowingItem(
                        planted_chapter=chapter_number,
                        **nf
                    )
                    for nf in data.get("new_foreshadowing", [])
                ],
                implicit_issues=data.get("implicit_issues", [])
            )
            
            return updates
            
        except json.JSONDecodeError as e:
            logger.error(f"提取结果解析失败: {e}")
            # 返回空更新
            return ExtractedUpdates(
                source_chapter=chapter_number,
                source_scene_index=scene_index
            )
        except Exception as e:
            logger.error(f"提取更新失败: {e}")
            return ExtractedUpdates(
                source_chapter=chapter_number,
                source_scene_index=scene_index
            )
    
    async def _apply_updates(self, updates: ExtractedUpdates) -> None:
        """并发应用更新到各知识库"""
        tasks = []
        
        # 人物更新
        if updates.character_updates:
            tasks.append(self._update_characters(updates.character_updates))
        
        # 世界观规则更新
        if updates.new_world_rules:
            tasks.append(self._update_bible(updates.new_world_rules))
        
        # 伏笔更新
        if updates.foreshadowing_status_changes or updates.new_foreshadowing:
            tasks.append(self._update_foreshadowing(
                updates.foreshadowing_status_changes,
                updates.new_foreshadowing
            ))
        
        # 并发执行所有更新（错误隔离）
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # 记录失败的更新
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(f"更新任务 {i} 失败: {result}")
    
    async def _update_characters(self, updates: list[CharacterStateUpdate]) -> None:
        """更新人物库"""
        try:
            for update in updates:
                await self.character_db.apply_update(update)
        except Exception as e:
            logger.error(f"人物库更新失败: {e}")
            raise
    
    async def _update_bible(self, rules: list[WorldRule]) -> None:
        """更新世界观库"""
        try:
            for rule in rules:
                await self.bible_db.append_rule(rule)
        except Exception as e:
            logger.error(f"世界观库更新失败: {e}")
            raise
    
    async def _update_foreshadowing(
        self,
        status_changes: list[dict],
        new_items: list[ForeshadowingItem]
    ) -> None:
        """更新伏笔库"""
        try:
            # 应用状态变更
            for change in status_changes:
                await self.foreshadowing_db.update_status(
                    fs_id=change["id"],
                    new_status=change["new_status"],
                    notes=change.get("notes", "")
                )
            
            # 添加新伏笔
            for item in new_items:
                await self.foreshadowing_db.add_item(item)
                
        except Exception as e:
            logger.error(f"伏笔库更新失败: {e}")
            raise
    
    async def _vectorize_scene(
        self,
        generated_text: str,
        chapter_number: int,
        scene_index: int,
        scene_plan: ScenePlan
    ) -> None:
        """将场景文本向量化存储"""
        try:
            await self.vector_store.upsert(
                collection="chapter_scenes",
                id=f"ch{chapter_number}_sc{scene_index}",
                text=generated_text,
                metadata={
                    "chapter": chapter_number,
                    "scene": scene_index,
                    "emotional_tone": scene_plan.emotional_tone,
                    "pov_character": scene_plan.pov_character,
                    "present_characters": scene_plan.present_characters,
                    "created_at": datetime.now().isoformat()
                }
            )
        except Exception as e:
            logger.error(f"场景向量化失败: {e}")
            # 向量化失败不影响主流程
    
    async def _save_update_record(self, updates: ExtractedUpdates) -> None:
        """保存更新记录到文件（用于调试和审计）"""
        try:
            record_path = (
                self.project_path / "chapters" / 
                f"chapter_{updates.source_chapter}_updates.json"
            )
            record_path.parent.mkdir(parents=True, exist_ok=True)
            
            # 读取现有记录
            records = []
            if record_path.exists():
                async with aiofiles.open(record_path, 'r', encoding='utf-8') as f:
                    content = await f.read()
                    records = json.loads(content)
            
            # 添加新记录
            records.append(updates.model_dump())
            
            # 保存
            async with aiofiles.open(record_path, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(records, ensure_ascii=False, indent=2))
                
        except Exception as e:
            logger.error(f"保存更新记录失败: {e}")
            # 记录失败不影响主流程


# ============================================================
# 便捷函数
# ============================================================

async def quick_extract(
    project_id: str,
    generated_text: str,
    chapter_number: int,
    scene_index: int,
    scene_plan: ScenePlan
) -> ExtractedUpdates:
    """
    快速提取更新（不等待结果）
    
    使用示例：
        asyncio.create_task(quick_extract(...))
    """
    extractor = UpdateExtractor(project_id)
    return await extractor.extract_and_update(
        generated_text=generated_text,
        chapter_number=chapter_number,
        scene_index=scene_index,
        scene_plan=scene_plan
    )
