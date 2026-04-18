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
from typing import Optional, Any
from pathlib import Path
from datetime import datetime

import aiofiles
from pydantic import ValidationError

from core.config import get_config
from core.schemas import (
    ScenePlan, ExtractedUpdates, CharacterStateUpdate,
    WorldRule, ForeshadowingItem, CharacterCard,
    WorldRuleCategory, WorldRuleImportance,
    ForeshadowingType, ForeshadowingStatus
)
from core.llm_client import LLMClient, quick_call
from core.logging_config import get_logger, log_exception

logger = get_logger('core.update_extractor')


# ============================================================
# 辅助工具函数
# ============================================================

def clean_json_response(response: str) -> str:
    """
    终极 JSON 清洗（多级防御）

    清洗策略（按优先级）：
    1. 去除前后空白字符和 BOM
    2. 去除 Markdown 代码块包裹（```json ... ```）
    3. 【强力截取】寻找文本中第一个 { 或 [ 到最后一个 } 或 ]，
       提取最可能的 JSON 主体（应对模型输出寒暄前缀/后缀）
    4. 去除尾部可能的逗号等常见 JSON 语法污染

    无论底层是否使用结构化参数，解析前都必须过此清洗。
    """
    text = response.strip()
    text = text.lstrip('\ufeff')

    # 第1层：去除 Markdown 代码块
    pattern = r'^```(?:json)?\s*\n?(.*?)\n?```\s*$'
    match = re.match(pattern, text, re.DOTALL)
    if match:
        text = match.group(1).strip()

    # 第2层：强力截取 JSON 主体
    first_brace = text.find('{')
    first_bracket = text.find('[')

    if first_brace == -1 and first_bracket == -1:
        return text

    if first_brace == -1:
        start = first_bracket
    elif first_bracket == -1:
        start = first_brace
    else:
        start = min(first_brace, first_bracket)

    if text[start] == '{':
        end = text.rfind('}')
    else:
        end = text.rfind(']')

    if end != -1 and end > start:
        text = text[start:end + 1]

    # 第3层：去除尾部可能污染 JSON 的逗号
    text = text.rstrip().rstrip(',').rstrip()

    return text


def find_key_in_dict(data: Any, target_keys: list[str]) -> Any:
    """
    递归在 JSON 树中查找任意匹配的目标键（不区分大小写）。

    支持：
    1. 任意嵌套深度搜索
    2. 多目标键同时匹配（返回第一个命中的列表/非空值）
    3. snake_case / camelCase / 小写 不敏感匹配

    Returns:
        找到的原始值（列表或标量），未找到返回 None
    """
    if not isinstance(data, (dict, list)):
        return None

    if isinstance(data, dict):
        # 先在当前层级查找（不区分大小写）
        lower_targets = [k.lower() for k in target_keys]
        for key, value in data.items():
            if key.lower() in lower_targets:
                return value

        # 递归到子节点
        for value in data.values():
            found = find_key_in_dict(value, target_keys)
            if found is not None:
                return found

    elif isinstance(data, list):
        for item in data:
            found = find_key_in_dict(item, target_keys)
            if found is not None:
                return found

    return None


def _coerce_to_list(val: Any) -> list:
    """
    强制将标量值包装为列表。

    场景：大模型在只有一条记录时直接返回字符串而非字符串列表。
    """
    if val is None:
        return []
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        return [val]
    # 其他标量类型（int, float, bool）也包装
    return [val]


# 枚举值模糊归一化映射表
ENUM_NORMALIZATION_MAP: dict[str, dict[str, str]] = {
    "importance": {
        # 中文映射
        "关键": "critical", "核心": "critical", "至关重要": "critical",
        "重要": "major", "主要": "major", "重大": "major",
        "次要": "minor", "一般": "minor", "轻微": "minor",
        # 大小写容错
        "critical": "critical", "critial": "critical",
        "major": "major", "mojor": "major",
        "minor": "minor", "minior": "minor",
    },
    "category": {
        "修炼": "cultivation", "修行": "cultivation", "境界": "cultivation",
        "地理": "geography", "地图": "geography", "位置": "geography",
        "社会": "social", "势力": "social", "关系": "social",
        "物理": "physics", "规则": "physics", "法则": "physics",
        "特殊": "special", "其他": "special", "其他": "special",
        # 英文容错
        "cultivation": "cultivation", "cultivaiton": "cultivation",
        "geography": "geography", "geograpy": "geography",
        "social": "social", "socal": "social",
        "physics": "physics", "phyiscs": "physics",
        "special": "special", "specail": "special",
    },
    "fs_type": {
        "剧情": "plot", "情节": "plot",
        "人物": "character", "角色": "character",
        "世界": "world", "世界观": "world",
        "情感": "emotional", "感情": "emotional",
        "plot": "plot", "charactor": "character",
        "character": "character", "world": "world",
        "emotional": "emotional", "emotion": "emotional",
    },
    "fs_status": {
        "已埋下": "planted", "埋下": "planted", "种植": "planted",
        "已暗示": "hinted", "暗示": "hinted", "提示": "hinted",
        "已触发": "triggered", "触发": "triggered", "引爆": "triggered",
        "已解决": "resolved", "解决": "resolved", "完成": "resolved",
        "planted": "planted", "hinted": "hinted",
        "triggered": "triggered", "trigered": "triggered",
        "resolved": "resolved", "resloved": "resolved",
    },
    "urgency": {
        "高": "high", "紧急": "high", "关键": "high",
        "中": "medium", "普通": "medium", "一般": "medium",
        "低": "low", "轻微": "low", "不急": "low",
        "high": "high", "hight": "high",
        "medium": "medium", "meduim": "medium",
        "low": "low",
    },
}


def _normalize_enum(raw_value: str, field: str, valid_values: set[str]) -> str:
    """
    枚举值模糊归一化。

    策略：
    1. 精确匹配（忽略大小写）
    2. 查预定义映射表（中英文/常见拼写错误）
    3. 无法匹配时降级为默认值，并输出 Warning
    """
    if not raw_value:
        return _default_for_field(field)

    # 1. 精确匹配（忽略大小写）
    lowered = raw_value.strip().lower()
    for v in valid_values:
        if lowered == v.lower():
            return v

    # 2. 查映射表
    mapping = ENUM_NORMALIZATION_MAP.get(field, {})
    normalized = mapping.get(raw_value.strip())
    if normalized and normalized in valid_values:
        return normalized
    # 再试试小写版本
    normalized = mapping.get(lowered)
    if normalized and normalized in valid_values:
        return normalized

    # 3. 降级为默认值并警告
    default = _default_for_field(field)
    logger.warning(
        f"枚举值归一化失败: field={field}, raw_value='{raw_value}', "
        f"降级为默认值 '{default}'"
    )
    return default


def _default_for_field(field: str) -> str:
    """返回字段的默认枚举值"""
    defaults = {
        "importance": "major",
        "category": "special",
        "fs_type": "plot",
        "fs_status": "planted",
        "urgency": "medium",
    }
    return defaults.get(field, "")


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

        # 更新记录文件锁
        self._update_record_lock = asyncio.Lock()

    @staticmethod
    def _truncate_text_for_extraction(text: str, max_chars: int = 3000, head_chars: int = 500) -> str:
        """
        为提取器截断文本，优先保留尾部（最新变化更关键）。

        策略：保留开头 head_chars（上下文衔接）+ 尾部剩余部分（最新变化）。
        如果文本长度在限制内，返回全文。
        """
        if len(text) <= max_chars:
            return text
        head = text[:head_chars]
        tail = text[-(max_chars - head_chars):]
        return f"{head}\n\n...[中间省略 {len(text) - max_chars} 字]...\n\n{tail}"

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
        await self._apply_updates(updates, chapter_number=chapter_number)
        
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
            # 默认异步：包装为 asyncio.Task 真正后台执行
            return asyncio.create_task(
                self._do_extract_and_update(
                    generated_text=generated_text,
                    chapter_number=chapter_number,
                    scene_index=scene_index,
                    scene_plan=scene_plan
                )
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
            char = await self.character_db.get_character(name)
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
计划出场人物：{', '.join(scene_plan.present_characters)}
情绪基调：{scene_plan.emotional_tone}

当前人物状态（用于对比，仅限计划出场人物）：
{json.dumps(current_characters, ensure_ascii=False, indent=2)}

场景文本：
{self._truncate_text_for_extraction(generated_text)}  # 优先保留尾部（最新变化更关键）

请提取以下信息：
1. 人物状态变化（位置/修为/情绪/目标/关系）—— 重要：如果文本中出现了不在"计划出场人物"列表中的角色（例如路人、新登场的 NPC、意外出现的角色），也请一并提取其状态变化。
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
                temperature=0.1,
                response_format="json_schema",
                json_schema=extraction_schema
            )
            
            # 无条件 JSON 清洗 + 解析
            cleaned_content = clean_json_response(response_obj.content)
            data = json.loads(cleaned_content)

            # ============================================================
            # 1. 扁平化模糊搜索：在 JSON 树任意深度查找目标字段
            # ============================================================
            raw_character_updates = _coerce_to_list(
                find_key_in_dict(data, ["character_updates", "characters", "character_updates_list", "updates"])
            )
            raw_new_world_rules = _coerce_to_list(
                find_key_in_dict(data, ["new_world_rules", "world_rules", "rules", "newRules", "worldRules"])
            )
            raw_foreshadowing_status_changes = _coerce_to_list(
                find_key_in_dict(data, [
                    "foreshadowing_status_changes", "foreshadowing_changes",
                    "status_changes", "fs_changes", "foreshadowing_updates"
                ])
            )
            raw_new_foreshadowing = _coerce_to_list(
                find_key_in_dict(data, [
                    "new_foreshadowing", "new_foreshadowing_items",
                    "foreshadowing_items", "newFs", "fs_items"
                ])
            )
            raw_implicit_issues = _coerce_to_list(
                find_key_in_dict(data, [
                    "implicit_issues", "issues", "problems",
                    "potential_issues", "detected_issues"
                ])
            )

            # ============================================================
            # 2. 枚举值模糊归一化 + Pydantic 防御性验证
            # ============================================================
            valid_categories = {e.value for e in WorldRuleCategory}
            valid_importances = {e.value for e in WorldRuleImportance}
            valid_fs_types = {e.value for e in ForeshadowingType}
            valid_fs_statuses = {e.value for e in ForeshadowingStatus}

            sanitized_world_rules = []
            for wr in raw_new_world_rules:
                if not isinstance(wr, dict):
                    continue
                cat = _normalize_enum(
                    wr.get("category", ""), "category", valid_categories
                )
                imp = _normalize_enum(
                    wr.get("importance", ""), "importance", valid_importances
                )
                wr["category"] = cat
                wr["importance"] = imp
                try:
                    sanitized_world_rules.append(
                        WorldRule(source_chapter=chapter_number, **wr)
                    )
                except ValidationError as ve:
                    logger.warning(f"跳过非法 world_rule: {ve}")
                except Exception as e:
                    logger.warning(f"跳过非法 world_rule: {e}")

            sanitized_foreshadowing = []
            for nf in raw_new_foreshadowing:
                if not isinstance(nf, dict):
                    continue
                fs_type = _normalize_enum(
                    nf.get("type", ""), "fs_type", valid_fs_types
                )
                nf["type"] = fs_type
                status = _normalize_enum(
                    nf.get("status", ""), "fs_status", valid_fs_statuses
                )
                nf["status"] = status
                # urgency 也归一化
                nf["urgency"] = _normalize_enum(
                    nf.get("urgency", ""), "urgency", {"low", "medium", "high"}
                )
                try:
                    sanitized_foreshadowing.append(
                        ForeshadowingItem(planted_chapter=chapter_number, **nf)
                    )
                except ValidationError as ve:
                    logger.warning(f"跳过非法 foreshadowing: {ve}")
                except Exception as e:
                    logger.warning(f"跳过非法 foreshadowing: {e}")

            # 人物更新：先模糊搜索 + 再 Pydantic 校验
            valid_character_updates = []
            for cu in raw_character_updates:
                if not isinstance(cu, dict):
                    continue
                try:
                    valid_character_updates.append(CharacterStateUpdate.model_validate(cu))
                except ValidationError as ve:
                    logger.warning(f"跳过非法 character_update: {ve}")

            # 构建最终对象（Pydantic 的 AliasChoices 会自动兜底字段名别名）
            updates = ExtractedUpdates(
                source_chapter=chapter_number,
                source_scene_index=scene_index,
                character_updates=valid_character_updates,
                new_world_rules=sanitized_world_rules,
                foreshadowing_status_changes=raw_foreshadowing_status_changes,
                new_foreshadowing=sanitized_foreshadowing,
                implicit_issues=raw_implicit_issues
            )

            return updates

        except json.JSONDecodeError as e:
            logger.error(f"提取结果解析失败: {e}")
            return ExtractedUpdates(
                source_chapter=chapter_number,
                source_scene_index=scene_index
            )
        except ValidationError as ve:
            logger.error(f"提取结果 Pydantic 校验失败: {ve}")
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
    
    async def _apply_updates(self, updates: ExtractedUpdates, chapter_number: int = 0) -> None:
        """并发应用更新到各知识库"""
        tasks = []

        # 人物更新
        if updates.character_updates:
            tasks.append(self._update_characters(updates.character_updates, chapter_number=chapter_number))

        # 世界观规则更新
        if updates.new_world_rules:
            tasks.append(self._update_bible(updates.new_world_rules))

        # 伏笔更新
        if updates.foreshadowing_status_changes or updates.new_foreshadowing:
            tasks.append(self._update_foreshadowing(
                updates.foreshadowing_status_changes,
                updates.new_foreshadowing,
                chapter_number=chapter_number
            ))
        
        # 并发执行所有更新（错误隔离）
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # 记录失败的更新
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(f"更新任务 {i} 失败: {result}")
    
    async def _update_characters(self, updates: list[CharacterStateUpdate], chapter_number: int = 0) -> None:
        """更新人物库"""
        try:
            for update in updates:
                char = await self.character_db.get_character(update.character_name)
                if not char:
                    # 新登场角色：自动初始化临时人物卡
                    logger.info(
                        f"检测到新登场角色 '{update.character_name}'，"
                        f"自动创建临时人物卡（章节 {chapter_number}）"
                    )
                    char = CharacterCard(
                        name=update.character_name,
                        appearance="待补充",
                        personality_core="待补充",
                        background="待补充",
                        current_location=update.location_change or "未知",
                        current_emotion=update.emotion_change or "",
                        first_appeared_chapter=chapter_number,
                        last_updated_chapter=chapter_number
                    )
                    # 如果有修为变化，也记录上
                    if update.cultivation_change:
                        from core.schemas import CultivationLevel
                        char.cultivation = CultivationLevel(
                            realm=update.cultivation_change,
                            stage="",
                            combat_power_estimate="未知"
                        )
                    await self.character_db.save_character(char)
                    continue
                await self.character_db.apply_update(update, chapter=chapter_number, scene_index=scene_index)
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
        new_items: list[ForeshadowingItem],
        chapter_number: int = 0
    ) -> None:
        """更新伏笔库"""
        try:
            # 应用状态变更
            for change in status_changes:
                await self.foreshadowing_db.update_status(
                    fs_id=change["id"],
                    new_status=change["new_status"],
                    notes=change.get("notes", ""),
                    triggered_chapter=chapter_number
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

            async with self._update_record_lock:
                # 读取现有记录
                records = []
                if record_path.exists():
                    async with aiofiles.open(record_path, 'r', encoding='utf-8') as f:
                        content = await f.read()
                        records = json.loads(content)

                # 添加新记录（绑定 scene_id 以便回滚）
                record_data = updates.model_dump()
                record_data["recorded_at"] = datetime.now().isoformat()
                records.append(record_data)

                # 保存
                async with aiofiles.open(record_path, 'w', encoding='utf-8') as f:
                    await f.write(json.dumps(records, ensure_ascii=False, indent=2))

        except Exception as e:
            logger.error(f"保存更新记录失败: {e}")
            # 记录失败不影响主流程

    async def rollback_scene_updates(
        self,
        chapter_number: int,
        scene_index: int
    ) -> dict:
        """
        回滚指定场景产生的知识库更新

        通过读取更新记录，逆向撤销该场景对人物状态、世界规则、
        伏笔等知识库的修改。

        Returns:
            回滚结果统计
        """
        from pathlib import Path

        result = {
            "characters_rolled_back": 0,
            "rules_removed": 0,
            "foreshadowing_reverted": 0,
            "foreshadowing_removed": 0,
            "errors": []
        }

        try:
            record_path = (
                self.project_path / "chapters" /
                f"chapter_{chapter_number}_updates.json"
            )
            if not record_path.exists():
                return {**result, "message": "该场景无更新记录，无需回滚"}

            # 读取更新记录
            async with aiofiles.open(record_path, 'r', encoding='utf-8') as f:
                records = json.loads(await f.read())

            # 找到对应场景的更新记录
            scene_records = [
                r for r in records
                if r.get("source_scene_index") == scene_index
            ]

            if not scene_records:
                return {**result, "message": "该场景无更新记录，无需回滚"}

            # 取最新的一条记录进行回滚（通常只有一条）
            latest_record = scene_records[-1]

            # ── 1. 回滚人物状态 ──
            for char_update in latest_record.get("character_updates", []):
                char_name = char_update.get("character_name")
                if not char_name:
                    continue
                try:
                    char = await self.character_db.get_character(char_name)
                    if not char:
                        continue

                    # 精确移除该场景添加的状态历史快照（按 scene_index 匹配）
                    original_len = len(char.state_history)
                    char.state_history = [
                        s for s in char.state_history
                        if not (s.get("chapter") == chapter_number and s.get("scene_index") == scene_index)
                    ]

                    # 如果移除了快照，需要重新计算当前状态
                    if len(char.state_history) < original_len:
                        # 从 state_history 重建当前状态
                        self._rebuild_character_state(char)
                        await self.character_db.save_character(char)
                        result["characters_rolled_back"] += 1
                except Exception as e:
                    result["errors"].append(f"回滚人物 {char_name} 失败: {e}")

            # ── 2. 回滚世界规则 ──
            for rule_data in latest_record.get("new_world_rules", []):
                try:
                    rule_content = rule_data.get("content", "")
                    if not rule_content:
                        continue
                    # 尝试从 bible 中移除匹配的规则
                    removed = await self.bible_db.remove_rule_by_content(rule_content)
                    if removed:
                        result["rules_removed"] += 1
                except Exception as e:
                    result["errors"].append(f"回滚规则失败: {e}")

            # ── 3. 回滚伏笔状态变更 ──
            for fs_change in latest_record.get("foreshadowing_status_changes", []):
                try:
                    fs_id = fs_change.get("id")
                    # 回退到上一状态（简单回退到 hinted/planted）
                    if fs_id:
                        await self.foreshadowing_db.revert_status(fs_id)
                        result["foreshadowing_reverted"] += 1
                except Exception as e:
                    result["errors"].append(f"回滚伏笔状态失败: {e}")

            # ── 4. 回滚新伏笔 ──
            for fs_item in latest_record.get("new_foreshadowing", []):
                try:
                    fs_desc = fs_item.get("description", "")
                    if fs_desc:
                        removed = await self.foreshadowing_db.remove_by_description(
                            fs_desc, chapter_number
                        )
                        if removed:
                            result["foreshadowing_removed"] += 1
                except Exception as e:
                    result["errors"].append(f"移除新伏笔失败: {e}")

            # ── 5. 标记该记录为已回滚 ──
            for r in records:
                if r.get("source_scene_index") == scene_index:
                    r["rolled_back"] = True
                    r["rolled_back_at"] = datetime.now().isoformat()

            async with aiofiles.open(record_path, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(records, ensure_ascii=False, indent=2))

            return result

        except Exception as e:
            logger.error(f"回滚场景更新失败: {e}")
            return {**result, "message": f"回滚失败: {e}"}

    @staticmethod
    def _rebuild_character_state(char: CharacterCard) -> None:
        """
        从 state_history 重新构建人物的当前状态。
        回滚时移除某条快照后，需要按顺序重放剩余快照。
        """
        # 重置为初始默认值
        char.current_location = ""
        char.current_emotion = ""
        char.active_goals = []
        char.cultivation = None

        # 按顺序重放所有状态快照
        for snapshot in char.state_history:
            updates = snapshot.get("updates", {})
            for key, value in updates.items():
                if key == "location":
                    char.current_location = value
                elif key == "emotion":
                    char.current_emotion = value
                elif key == "goals":
                    if isinstance(value, list):
                        for g in value:
                            if g not in char.active_goals:
                                char.active_goals.append(g)
                elif key == "cultivation":
                    if char.cultivation is None:
                        from core.schemas import CultivationLevel
                        char.cultivation = CultivationLevel(
                            realm=value, stage="", combat_power_estimate="未知"
                        )
                    else:
                        char.cultivation.realm = value


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
