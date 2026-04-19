"""
generators/bible_generator.py

世界观 Bible 生成器

职责边界：
    - 基于用户提供的 ProjectMeta（项目元信息）生成完整的世界观设定（Bible）。
    - Bible 是小说世界的"宪法"，包含战力体系、世界规则、地理、势力、历史等核心设定。
    - Bible 一旦确认后进入"仅追加"模式（后续不再修改原有规则，只追加新发现的规则）。
    - 生成结果通过 BibleDB 持久化，同时通过 VectorStore 向量化存储，供后续 InjectionEngine 检索。

生成内容：
    1. 世界名称与整体描述（100-200字概括）。
    2. 战力体系（修炼体系名称、境界划分、突破条件、特殊能力、战力上限）。
    3. 世界规则列表（按 cultivation/geography/social/physics/special 分类）。
    4. 地理设定（主要区域、重要地点）。
    5. 势力格局（至少3-5个主要势力，含正邪中立不同立场）。
    6. 历史背景（为主角出现提供合理性）。

设计原则：
    - 结构化输出：通过 get_output_schema() 定义 JSON Schema，强制 LLM 返回结构化数据。
    - 闭环重试：基类 BaseGenerator 自动处理解析失败和验证失败，构造修正提示词并重试。
    - 向量化存储：将战力体系、世界规则、势力信息分别存入向量库的不同 collection，
      便于后续按语义检索（如"主角当前境界"可检索到对应修炼规则）。

验证逻辑：
    - 检查必需字段是否齐全（world_name, world_description, combat_system 等）。
    - 验证战力体系的完整性（name, realms, breakthrough_conditions 等子字段）。
    - 验证境界数量与突破条件数量匹配（n 个境界需要 n-1 个突破条件）。
    - 验证世界规则列表非空且每条规则都有 content。
    - 验证地理信息包含至少一个主要区域，势力列表非空。

典型用法：
    generator = BibleGenerator(project_id="xxx")
    bible_data = await generator.generate(project_meta=project_meta)
    # 结果自动保存到 knowledge_bases/bible_db.py 和 vector_store/store.py
"""

from typing import Any

from generators.base_generator import BaseGenerator, GenerationError
from core.schemas import ProjectMeta, CombatSystem, WorldRule
from knowledge_bases.bible_db import BibleDB
from core.logging_config import get_logger, log_exception

logger = get_logger('generators.bible_generator')


class BibleGenerator(BaseGenerator):
    """
    世界观 Bible 生成器

    Bible 是 MANS 系统的世界观核心数据源，所有后续的章节生成、人物行为、战斗描写
    都必须遵循 Bible 中定义的设定。Bible 的质量直接决定了小说的内部一致性。

    生成策略：
        1. 从 ProjectMeta 中提取类型、基调、主角起点等信息，引导 LLM 生成匹配的风格。
        2. 提供详细的 JSON Schema，强制 LLM 输出结构化数据，避免自由文本导致解析困难。
        3. 在 prompt 中明确说明"只输出 JSON"，并提供引号使用的正确/错误示例。

    继承自 BaseGenerator，复用以下能力：
        - 带重试的 LLM 调用（call_with_retry）。
        - 自动修正的解析-验证闭环。
        - 进度报告（通过 set_progress_callback 传递给前端）。
    """

    def _get_generator_name(self) -> str:
        """返回生成器名称，用于日志和进度报告中标识当前环节。"""
        return "BibleGenerator"

    def get_output_schema(self) -> dict:
        """
        返回 Bible 生成的 JSON Schema 定义。

        此 schema 描述了 LLM 输出数据的完整结构，基类在调用 LLM 时将其传入
        response_format="json_schema"，强制模型返回严格符合 schema 的 JSON。
        这是降低解析失败率的关键机制。

        Schema 结构：
            - world_name: 世界名称（2-5字）。
            - world_description: 世界整体描述（100-200字）。
            - combat_system: 战力体系对象，包含 name/realms/breakthrough_conditions/special_abilities/power_ceiling。
            - world_rules: 规则列表，每项包含 category/content/importance。
            - geography: 地理对象，包含 major_regions 数组。
            - factions: 势力列表，每项包含 name/type/power_level/description。
            - history_notes: 历史事件字符串数组。
        """
        return {
            "name": "bible_output",
            "schema": {
                "type": "object",
                "properties": {
                    "world_name": {"type": "string"},
                    "world_description": {"type": "string"},
                    "combat_system": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "realms": {
                                "type": "array",
                                "items": {"type": "string"}
                            },
                            "breakthrough_conditions": {
                                "type": "object",
                                "additionalProperties": {"type": "string"}
                            },
                            "special_abilities": {
                                "type": "array",
                                "items": {"type": "string"}
                            },
                            "power_ceiling": {"type": "string"}
                        },
                        "required": ["name", "realms", "breakthrough_conditions", "special_abilities", "power_ceiling"]
                    },
                    "world_rules": {
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
                    "geography": {
                        "type": "object",
                        "properties": {
                            "major_regions": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "description": {"type": "string"},
                                        "important_locations": {
                                            "type": "array",
                                            "items": {"type": "string"}
                                        }
                                    },
                                    "required": ["name", "description"]
                                }
                            }
                        },
                        "required": ["major_regions"]
                    },
                    "factions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "type": {"type": "string"},
                                "power_level": {"type": "string"},
                                "description": {"type": "string"}
                            },
                            "required": ["name", "description"]
                        }
                    },
                    "history_notes": {
                        "type": "array",
                        "items": {"type": "string"}
                    }
                },
                "required": ["world_name", "world_description", "combat_system", "world_rules", "geography", "factions"]
            }
        }

    def _build_prompt(self, project_meta: ProjectMeta, **kwargs) -> str:
        """
        构建 Bible 生成 prompt。

        Prompt 设计要点：
            1. 提供完整的创作信息（作品名称、核心 Idea、类型、基调、目标篇幅、主角起点）。
            2. 可选信息（文风参考、禁忌元素）仅在存在时附加，避免污染 prompt。
            3. 输出要求包含完整的 JSON 示例和字段说明，降低 LLM 的理解成本。
            4. 生成原则给出明确的数量和质量约束（境界数8-12个、势力3-5个等）。
            5. 引号使用规范：明确说明字符串值内部必须使用英文单引号，避免 JSON 解析失败。

        Args:
            project_meta: 项目元信息，包含用户填写的世界观种子信息。
            **kwargs: 预留扩展参数。

        Returns:
            完整的 prompt 字符串，直接发送给 LLM。
        """
        prompt = f"""基于以下创作信息，为这部{project_meta.genre}小说构建完整的世界观设定。

## 创作信息

- **作品名称**：{project_meta.name}
- **核心 Idea**：{project_meta.core_idea}
- **类型**：{project_meta.genre}
- **基调**：{project_meta.tone}
- **目标篇幅**：{project_meta.target_length}
- **主角起点**：{project_meta.protagonist_seed}
"""

        # 可选信息仅在存在时附加
        if project_meta.style_reference:
            prompt += f"\n- **文风参考**：{project_meta.style_reference}"

        if project_meta.forbidden_elements:
            prompt += f"\n- **禁忌元素**：{', '.join(project_meta.forbidden_elements)}"

        prompt += """

## 输出要求

请输出严格的 JSON 格式，包含以下字段：

```json
{
  "world_name": "世界名称（2-5字）",
  "world_description": "世界的整体描述（100-200字）",
  "combat_system": {
    "name": "战力体系名称",
    "realms": ["境界1", "境界2", "境界3", ...],
    "breakthrough_conditions": {
      "境界1": "突破到境界2的条件描述",
      "境界2": "突破到境界3的条件描述"
    },
    "special_abilities": ["特殊能力类型1", "特殊能力类型2"],
    "power_ceiling": "当前故事中的战力上限说明"
  },
  "world_rules": [
    {
      "category": "cultivation|geography|social|physics|special",
      "content": "规则描述",
      "importance": "critical|major|minor"
    }
  ],
  "geography": {
    "major_regions": [
      {
        "name": "区域名称",
        "description": "区域描述",
        "important_locations": ["地点1", "地点2"]
      }
    ]
  },
  "factions": [
    {
      "name": "势力名称",
      "type": "cultivation_sect|family|kingdom|organization",
      "power_level": "major|medium|minor",
      "description": "势力描述"
    }
  ],
  "history_notes": ["历史事件1", "历史事件2"]
}
```

## 生成原则

1. **战力体系**：建议设置 8-12 个大境界，每个境界要有明确的突破条件和特征
2. **世界规则**：每条规则都要有明确的"违反代价"描述
3. **势力格局**：至少设计 3-5 个主要势力，包含正邪中立不同立场
4. **地理设定**：与势力分布和故事主线相匹配
5. **历史背景**：为主角的出现和故事发展提供合理性

## 重要提示

- 只输出 JSON，不要输出任何其他内容
- 不要使用 markdown 代码块包裹
- 确保 JSON 格式正确，所有字符串最外层使用双引号
- **JSON 字符串值内部如需引号，必须使用英文单引号（'），严禁使用双引号或中文引号**
  - 正确示例：\"世界实为'天道'所控制\"
  - 错误示例：\"世界实为\"天道\"所控制\"（会导致 JSON 解析失败）
  - 错误示例：\"世界实为\u201c天道\u201d所控制\"（中文引号也会导致解析失败）
- 所有字段都必须填写，不能为空
"""
        return prompt

    def _parse_response(self, response: str) -> dict:
        """
        解析 Bible 生成响应。

        使用基类提供的 _safe_json_parse() 方法，该方法包含多层清洗和修复策略：
            1. 去除 Markdown 代码块包裹。
            2. 尝试标准 json.loads() 解析。
            3. 若失败且检测到括号不匹配，自动补全后重试。
            4. 若仍失败，抛出 ParseError 触发基类的自动重试机制。

        Args:
            response: LLM 返回的原始 JSON 字符串。

        Returns:
            解析后的 Bible 数据字典。
        """
        return self._safe_json_parse(response)

    def _validate_result(self, result: dict) -> bool:
        """
        验证 Bible 数据完整性。

        验证项（逐项检查，任一失败即抛出 ValidationError 触发重试）：
            1. 必需字段检查：world_name, world_description, combat_system, world_rules, geography, factions。
            2. 战力体系子字段：name, realms, breakthrough_conditions, special_abilities, power_ceiling 必须全部存在。
            3. 境界与突破条件匹配：若 realms 有 n 个境界，breakthrough_conditions 至少应有 n-1 个。
            4. 世界规则列表非空，且每条规则都有 content 字段。
            5. 地理信息包含至少一个 major_regions 条目。
            6. 势力列表非空。

        Args:
            result: _parse_response() 解析后的 Bible 数据字典。

        Returns:
            True 表示验证通过。

        Raises:
            ValidationError: 验证失败时抛出，包含具体缺失字段信息，基类会自动构造修正提示词重试。
        """
        from generators.base_generator import ValidationError

        required_fields = [
            "world_name",
            "world_description",
            "combat_system",
            "world_rules",
            "geography",
            "factions"
        ]

        missing_fields = []
        for field in required_fields:
            if field not in result:
                missing_fields.append(field)

        if missing_fields:
            raise ValidationError(
                f"缺少必需字段: {', '.join(missing_fields)}",
                stage="validation",
                details={"missing_fields": missing_fields}
            )

        # 验证战力体系
        combat_system = result.get("combat_system", {})
        combat_required = ["name", "realms", "breakthrough_conditions", "special_abilities", "power_ceiling"]
        combat_missing = [f for f in combat_required if f not in combat_system]
        if combat_missing:
            raise ValidationError(
                f"战力体系缺少字段: {', '.join(combat_missing)}",
                stage="validation",
                details={"combat_missing": combat_missing}
            )

        # 验证境界数量和突破条件数量匹配
        realms = combat_system.get("realms", [])
        breakthroughs = combat_system.get("breakthrough_conditions", {})
        if len(realms) > 1 and len(breakthroughs) < len(realms) - 1:
            raise ValidationError(
                f"突破条件数量不足: 有 {len(realms)} 个境界，但只有 {len(breakthroughs)} 个突破条件",
                stage="validation"
            )

        # 验证世界规则
        world_rules = result.get("world_rules", [])
        if not world_rules:
            raise ValidationError(
                "世界规则列表不能为空",
                stage="validation"
            )

        for i, rule in enumerate(world_rules):
            if "content" not in rule:
                raise ValidationError(
                    f"第 {i+1} 条世界规则缺少 content 字段",
                    stage="validation"
                )

        # 验证地理信息
        geography = result.get("geography", {})
        if "major_regions" not in geography or not geography["major_regions"]:
            raise ValidationError(
                "地理信息必须包含至少一个主要区域",
                stage="validation"
            )

        # 验证势力信息
        factions = result.get("factions", [])
        if not factions:
            raise ValidationError(
                "必须包含至少一个势力",
                stage="validation"
            )

        return True

    async def _save_result(self, result: dict) -> None:
        """
        保存 Bible 到知识库。

        保存流程：
            1. 构建 Bible 数据结构，统一添加 version=1 标记。
            2. 调用 BibleDB.save("bible", data) 进行原子写入。
            3. 记录保存成功的日志。

        数据版本控制：
            version 字段用于未来支持 Bible 的版本升级。当前所有初始生成的 Bible 标记为 v1。

        Args:
            result: 验证通过的 Bible 数据字典。
        """
        bible_db = BibleDB(self.project_id)

        # 构建 Bible 数据结构
        bible_data = {
            "version": 1,
            "world_name": result["world_name"],
            "world_description": result["world_description"],
            "combat_system": result["combat_system"],
            "world_rules": result["world_rules"],
            "geography": result["geography"],
            "factions": result["factions"],
            "history_notes": result.get("history_notes", [])
        }

        # 原子写入 Bible 数据
        await bible_db.save("bible", bible_data)
        logger.info(f"Bible 已保存到知识库 - 项目: {self.project_id}")

    async def _vectorize_result(self, result: dict) -> None:
        """
        将 Bible 内容向量化存储，供后续语义检索。

        向量化策略（按内容类型分 collection 存储）：
            1. world_rules: 每条世界规则单独向量化，metadata 包含 category 和 importance，
               便于后续按类别或重要性过滤检索。
            2. bible_rules (combat_system): 战力体系整体向量化，作为"修炼规则"的核心条目，
               标记 importance=critical（因为战力体系是大多数玄幻小说的核心设定）。
            3. bible_rules (factions): 每个势力单独向量化，metadata 包含原始 power_level，
               经过映射后统一为 minor/major/critical。

        检索场景示例：
            - "主角突破到筑基期需要什么条件" → 检索到 combat_system 的向量。
            - "青云宗是什么立场" → 检索到对应势力的向量。
            - "违反天道规则有什么后果" → 检索到 category=special 的世界规则。

        Args:
            result: 已保存的 Bible 数据字典。
        """
        from vector_store.store import VectorStore

        vector_store = VectorStore(self.project_id)

        # 向量化世界规则
        world_rules = result.get("world_rules", [])
        for i, rule in enumerate(world_rules):
            await vector_store.upsert(
                collection="bible_rules",
                id=f"rule_{i}",
                text=rule["content"],
                metadata={
                    "category": rule.get("category", "special"),
                    "importance": rule.get("importance", "major"),
                    "source": "bible_generation"
                }
            )

        # 向量化战力体系
        combat_system = result.get("combat_system", {})
        combat_text = f"""战力体系：{combat_system.get('name', '')}
境界划分：{' → '.join(combat_system.get('realms', []))}
战力上限：{combat_system.get('power_ceiling', '')}
特殊能力：{', '.join(combat_system.get('special_abilities', []))}
"""
        await vector_store.upsert(
            collection="bible_rules",
            id="combat_system",
            text=combat_text,
            metadata={
                "category": "cultivation",
                "importance": "critical",
                "source": "bible_generation"
            }
        )

        # 向量化势力信息
        factions = result.get("factions", [])
        power_level_map = {
            "low": "minor",
            "medium": "major",
            "high": "critical",
            "minor": "minor",
            "major": "major",
            "critical": "critical"
        }
        for i, faction in enumerate(factions):
            raw_power = faction.get("power_level", "medium")
            mapped_importance = power_level_map.get(raw_power, "major")
            await vector_store.upsert(
                collection="bible_rules",
                id=f"faction_{i}",
                text=f"{faction['name']}: {faction['description']}",
                metadata={
                    "category": "social",
                    "importance": mapped_importance,
                    "source": "bible_generation"
                }
            )
