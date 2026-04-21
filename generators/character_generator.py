"""
generators/character_generator.py

人物设定生成器

职责边界：
    - 基于 Bible（世界观设定）和 protagonist_seed（主角起点描述）生成完整的人物卡。
    - 输出包含主角、主要配角的人物卡，以及人物之间的关系网。
    - 生成结果符合 core/schemas.py 中 CharacterCard 和 Relationship 的数据结构。
    - 通过 CharacterDB 持久化，同时向量化存储到向量库。

生成内容：
    1. 主角完整人物卡（外貌、性格核心、声线关键词、背景、修为、情绪、目标、矛盾、弱点）。
    2. 3-5 个主要配角人物卡（字段结构与主角一致，但描述可适当精简）。
    3. 人物关系网（source → target 的有向关系，包含关系类型、当前态度、描述）。

设计原则：
    - 角色一致性：主角的初始修为必须与 Bible 中的最低/次低境界匹配。
    - 关系张力：人物关系避免非黑即白，预留发展空间。
    - 声线可执行：voice_keywords 必须是具体可操作的写作指导（如"愤怒时沉默"）。
    - 核心矛盾：每个角色设计 3 个"核心矛盾"（对立特质间的张力），这是角色深度的来源。

验证逻辑：
    - 主角必须有 name, appearance, personality_core, background, current_location, cultivation。
    - 配角至少 2 个，每个配角必须有 name。
    - 关系网不能为空，且 source/target 必须是已知人物。

典型用法：
    generator = CharacterGenerator(project_id="xxx")
    characters = await generator.generate(
        project_meta=project_meta,
        bible_data=bible_data
    )
"""

import uuid
from typing import Any

from generators.base_generator import BaseGenerator, ValidationError
from core.schemas import ProjectMeta, CharacterCard, CultivationLevel, Relationship
from knowledge_bases.character_db import CharacterDB
from knowledge_bases.bible_db import BibleDB


class CharacterGenerator(BaseGenerator):
    """
    人物设定生成器

    人物卡是 Writer 生成正文时的核心上下文之一。InjectionEngine 会根据场景规划中
    的 present_characters 字段，检索对应的人物卡并注入 Writer 的 prompt 中。
    人物卡的质量（尤其是 voice_keywords 和 personality_core）直接影响角色在
    正文中的表现一致性。

    生成策略：
        1. 从 Bible 中提取战力体系信息，引导 LLM 为主角分配合理的初始境界。
        2. 从 Bible 中提取主要势力，引导 LLM 设计有势力背景的人物。
        3. 提供详细的 JSON Schema 和字段说明，确保输出可解析。
        4. Prompt 中明确区分"主角设计原则"和"配角设计原则"，确保主角更详细。

    保存策略：
        1. 为主角和每个配角分配唯一的 UUID（前8位）。
        2. 构建 CharacterCard Pydantic 对象，通过 CharacterDB.save_character() 持久化。
        3. 关系网通过 CharacterDB.add_relationship() 逐条保存。
    """

    def _get_generator_name(self) -> str:
        """返回生成器名称，用于日志和进度报告中标识当前环节。"""
        return "CharacterGenerator"

    def get_output_schema(self) -> dict:
        """
        返回人物生成的 JSON Schema 定义。

        Schema 结构：
            - protagonist: 主角对象，包含完整人物卡字段。
            - supporting_characters: 配角数组，结构与主角一致，但不含 core_contradictions 和 weaknesses_and_fears。
            - relationships: 关系数组，每项包含 source/target/relation_type/current_sentiment/description。
        """
        return {
            "name": "character_output",
            "schema": {
                "type": "object",
                "properties": {
                    "protagonist": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "aliases": {
                                "type": "array",
                                "items": {"type": "string"}
                            },
                            "appearance": {"type": "string"},
                            "personality_core": {"type": "string"},
                            "voice_keywords": {
                                "type": "array",
                                "items": {"type": "string"}
                            },
                            "background": {"type": "string"},
                            "current_location": {"type": "string"},
                            "cultivation": {
                                "type": "object",
                                "properties": {
                                    "realm": {"type": "string"},
                                    "stage": {"type": "string"},
                                    "combat_power_estimate": {"type": "string"}
                                },
                                "required": ["realm", "stage", "combat_power_estimate"]
                            },
                            "current_emotion": {"type": "string"},
                            "active_goals": {
                                "type": "array",
                                "items": {"type": "string"}
                            },
                            "core_contradictions": {
                                "type": "array",
                                "items": {"type": "string"}
                            },
                            "weaknesses_and_fears": {"type": "string"}
                        },
                        "required": ["name", "appearance", "personality_core", "background", "current_location", "cultivation"]
                    },
                    "supporting_characters": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "aliases": {
                                    "type": "array",
                                    "items": {"type": "string"}
                                },
                                "appearance": {"type": "string"},
                                "personality_core": {"type": "string"},
                                "voice_keywords": {
                                    "type": "array",
                                    "items": {"type": "string"}
                                },
                                "background": {"type": "string"},
                                "current_location": {"type": "string"},
                                "cultivation": {
                                    "type": "object",
                                    "properties": {
                                        "realm": {"type": "string"},
                                        "stage": {"type": "string"},
                                        "combat_power_estimate": {"type": "string"}
                                    },
                                    "required": ["realm", "stage", "combat_power_estimate"]
                                },
                                "current_emotion": {"type": "string"},
                                "active_goals": {
                                    "type": "array",
                                    "items": {"type": "string"}
                                },
                                "relationship_to_protagonist": {"type": "string"},
                                "role_in_story": {"type": "string"}
                            },
                            "required": ["name", "appearance", "personality_core", "background", "current_location", "cultivation"]
                        }
                    },
                    "relationships": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "source": {"type": "string"},
                                "target": {"type": "string"},
                                "relation_type": {"type": "string"},
                                "current_sentiment": {"type": "string"},
                                "description": {"type": "string"}
                            },
                            "required": ["source", "target", "relation_type", "description"]
                        }
                    }
                },
                "required": ["protagonist", "supporting_characters", "relationships"]
            }
        }

    def _build_prompt(self, project_meta: ProjectMeta, bible_data: dict, **kwargs) -> str:
        """
        构建人物生成 prompt。

        Prompt 设计要点：
            1. 提取 Bible 中的战力体系信息，确保主角初始境界与最低境界匹配。
            2. 提取 Bible 中的主要势力，引导 LLM 设计有势力归属的人物。
            3. 主角种子信息（protagonist_seed）是人物性格设计的核心输入。
            4. 详细说明主角与配角的设计原则差异（主角更详细，配角功能性更强）。
            5. 声线关键词的设计原则：必须是具体可操作的写作指导。

        Args:
            project_meta: 项目元信息，包含 protagonist_seed。
            bible_data: Bible 数据，包含世界观、战力体系、势力等。
            **kwargs: 预留扩展参数。

        Returns:
            完整的 prompt 字符串。
        """
        # 提取战力体系信息
        combat_system = bible_data.get("combat_system", {})
        realms = combat_system.get("realms", [])
        lowest_realm = realms[0] if realms else "入门"

        # 提取势力信息
        factions = bible_data.get("factions", [])
        faction_names = [f["name"] for f in factions[:3]] if factions else ["主要势力"]

        prompt = f"""基于以下世界观设定，为小说《{project_meta.name}》生成人物设定。

## 世界观信息

- **世界名称**：{bible_data.get('world_name', '未知世界')}
- **世界描述**：{bible_data.get('world_description', '')}
- **战力体系**：{combat_system.get('name', '修炼体系')}
- **境界划分**：{' → '.join(realms[:5])}{'...' if len(realms) > 5 else ''}
- **主要势力**：{', '.join(faction_names)}

## 主角种子信息

- **起点描述**：{project_meta.protagonist_seed}
- **核心 Idea**：{project_meta.core_idea}

## 输出要求

请输出严格的 JSON 格式，包含主角和 3-5 个主要配角：

```json
{{
  "protagonist": {{
    "name": "主角姓名",
    "aliases": ["别名1", "别名2"],
    "appearance": "外貌描述（50-100字）",
    "personality_core": "性格核心关键词（3-5个词，用顿号分隔）",
    "voice_keywords": ["说话特征1", "说话特征2", "说话特征3"],
    "background": "背景故事（100-200字）",
    "current_location": "初始位置",
    "cultivation": {{
      "realm": "{lowest_realm}",
      "stage": "初期",
      "combat_power_estimate": "战力描述"
    }},
    "current_emotion": "初始情绪状态",
    "active_goals": ["初始目标1", "初始目标2"],
    "core_contradictions": ["核心矛盾1", "核心矛盾2", "核心矛盾3"],
    "weaknesses_and_fears": "弱点和恐惧描述"
  }},
  "supporting_characters": [
    {{
      "name": "配角姓名",
      "aliases": [],
      "appearance": "外貌描述",
      "personality_core": "性格关键词",
      "voice_keywords": ["说话特征"],
      "background": "背景故事",
      "current_location": "位置",
      "cultivation": {{
        "realm": "境界",
        "stage": "阶段",
        "combat_power_estimate": "战力"
      }},
      "current_emotion": "情绪",
      "active_goals": ["目标"],
      "relationship_to_protagonist": "与主角的关系描述",
      "role_in_story": "在故事中的作用（导师/对手/盟友/恋人等）"
    }}
  ],
  "relationships": [
    {{
      "source": "人物A姓名",
      "target": "人物B姓名",
      "relation_type": "关系类型",
      "current_sentiment": "当前态度",
      "description": "关系描述"
    }}
  ]
}}
```

## 生成原则

### 主角设计原则
1. **核心矛盾**：设计3个"核心矛盾"（两种对立特质之间的张力），这是角色深度的来源
2. **声线关键词**：必须是具体可操作的写作指导，例如：
   - "说话简短、爱用反问"
   - "愤怒时沉默，开心时话多"
   - "对强者恭敬，对弱者温和"
3. **弱点和恐惧**：这是角色成长的驱动力，必须具体
4. **初始修为**：根据战力体系，主角初始应为最低或次低境界

### 配角设计原则
1. **多样性**：包含不同性别、年龄、立场、势力的角色
2. **功能性**：每个配角在故事中要有明确作用
3. **关系张力**：与主角的关系要有潜在冲突或发展空间
4. **修为梯度**：配角的修为应分布在主角上下，形成参照

### 关系网设计原则
1. **至少包含**：1个导师型、1个对手/敌人型、1个盟友型关系
2. **关系复杂性**：避免非黑即白，允许复杂情感
3. **发展潜力**：关系要有随故事发展的空间

## 重要提示

- 只输出 JSON，不要输出任何其他内容
- 不要使用 markdown 代码块包裹
- 确保 JSON 格式正确，所有字符串最外层使用双引号
- **JSON 字符串值内部如需引号，必须使用英文单引号（'），严禁使用双引号或中文引号**
- 人物姓名要符合小说类型风格
- 主角必须比配角描述更详细
"""
        return prompt

    def _parse_response(self, response: str) -> dict:
        """
        解析人物生成响应。

        使用基类提供的 _safe_json_parse() 方法进行安全 JSON 解析。
        若解析失败，基类会自动构造修正提示词并重试。

        Args:
            response: LLM 返回的原始 JSON 字符串。

        Returns:
            解析后的人物数据字典，包含 protagonist, supporting_characters, relationships 三个顶层字段。
        """
        return self._safe_json_parse(response)

    def _validate_result(self, result: dict) -> bool:
        """
        验证人物数据完整性。

        验证项：
            1. 主角存在性：必须有 protagonist 字段且非空。
            2. 主角必需字段：name, appearance, personality_core, background, current_location, cultivation。
            3. 配角数量：至少 2 个配角（过少会导致故事缺乏互动）。
            4. 配角名称：每个配角必须有 name 字段。
            5. 关系网非空：relationships 不能为空列表。
            6. 关系端点有效性：每条关系的 source 和 target 必须是已知人物名。

        Args:
            result: 解析后的人物数据字典。

        Returns:
            True 表示验证通过。

        Raises:
            ValidationError: 验证失败时抛出，包含具体错误信息。
        """
        # 验证主角
        protagonist = result.get("protagonist")
        if not protagonist:
            raise ValidationError(
                "缺少主角信息",
                stage="validation"
            )

        protagonist_required = [
            "name", "appearance", "personality_core",
            "background", "current_location", "cultivation"
        ]
        for field in protagonist_required:
            if field not in protagonist:
                raise ValidationError(
                    f"主角缺少必需字段: {field}",
                    stage="validation"
                )

        # 验证配角
        supporting = result.get("supporting_characters", [])
        if len(supporting) < 2:
            raise ValidationError(
                f"配角数量不足: 只有 {len(supporting)} 个，至少需要 2 个",
                stage="validation"
            )

        for i, char in enumerate(supporting):
            if "name" not in char:
                raise ValidationError(
                    f"第 {i+1} 个配角缺少 name 字段",
                    stage="validation"
                )

        # 验证关系网
        relationships = result.get("relationships", [])
        if not relationships:
            raise ValidationError(
                "人物关系网不能为空",
                stage="validation"
            )

        # 验证关系涉及的人物都存在
        all_names = {protagonist["name"]}
        all_names.update(char["name"] for char in supporting)

        for i, rel in enumerate(relationships):
            source = rel.get("source")
            target = rel.get("target")
            if source not in all_names:
                raise ValidationError(
                    f"第 {i+1} 个关系的 source '{source}' 不是已知人物",
                    stage="validation"
                )
            if target not in all_names:
                raise ValidationError(
                    f"第 {i+1} 个关系的 target '{target}' 不是已知人物",
                    stage="validation"
                )

        return True

    async def _save_result(self, result: dict) -> None:
        """
        保存人物设定到知识库。

        保存流程：
            1. 为主角分配 UUID，构建 CharacterCard 对象，保存到 CharacterDB。
            2. 为每个配角分配 UUID，构建 CharacterCard 对象，保存到 CharacterDB。
            3. 构建 name → id 映射表，用于关系网保存。
            4. 逐条解析 relationships，构建 Relationship 对象，通过 CharacterDB.add_relationship() 保存。

        注意：
            主角和配角初始的 first_appeared_chapter 和 last_updated_chapter 均设为 0，
            待正文生成后由 UpdateExtractor 更新为实际章节号。

        Args:
            result: 验证通过的人物数据字典。
        """
        character_db = CharacterDB(self.project_id)

        # 保存主角
        protagonist_data = result["protagonist"]
        protagonist_id = str(uuid.uuid4())[:8]

        # 构建主角 CharacterCard
        cultivation = protagonist_data.get("cultivation", {})
        protagonist_card = CharacterCard(
            id=protagonist_id,
            name=protagonist_data["name"],
            aliases=protagonist_data.get("aliases", []),
            appearance=protagonist_data["appearance"],
            personality_core=protagonist_data["personality_core"],
            voice_keywords=protagonist_data.get("voice_keywords", []),
            background=protagonist_data["background"],
            current_location=protagonist_data["current_location"],
            cultivation=CultivationLevel(
                realm=cultivation.get("realm", "未知"),
                stage=cultivation.get("stage", "初期"),
                combat_power_estimate=cultivation.get("combat_power_estimate", "")
            ),
            current_emotion=protagonist_data.get("current_emotion", "平静"),
            active_goals=protagonist_data.get("active_goals", []),
            is_protagonist=True,
            relationships=[],
            first_appeared_chapter=0,
            last_updated_chapter=0
        )

        await character_db.save_character(protagonist_card)

        # 保存配角
        supporting_chars = result.get("supporting_characters", [])
        char_id_map = {protagonist_data["name"]: protagonist_id}

        for char_data in supporting_chars:
            char_id = str(uuid.uuid4())[:8]
            char_id_map[char_data["name"]] = char_id

            cultivation = char_data.get("cultivation", {})
            char_card = CharacterCard(
                id=char_id,
                name=char_data["name"],
                aliases=char_data.get("aliases", []),
                appearance=char_data["appearance"],
                personality_core=char_data["personality_core"],
                voice_keywords=char_data.get("voice_keywords", []),
                background=char_data["background"],
                current_location=char_data["current_location"],
                cultivation=CultivationLevel(
                    realm=cultivation.get("realm", "未知"),
                    stage=cultivation.get("stage", "初期"),
                    combat_power_estimate=cultivation.get("combat_power_estimate", "")
                ),
                current_emotion=char_data.get("current_emotion", "平静"),
                active_goals=char_data.get("active_goals", []),
                relationships=[],
                first_appeared_chapter=0,
                last_updated_chapter=0
            )

            await character_db.save_character(char_card)

        # 保存关系网
        relationships = result.get("relationships", [])
        for rel_data in relationships:
            source_name = rel_data["source"]
            target_name = rel_data["target"]
            source_id = char_id_map.get(source_name)

            if source_id:
                relationship = Relationship(
                    target_character_id=char_id_map.get(target_name, ""),
                    target_name=target_name,
                    relation_type=rel_data.get("relation_type", "未知"),
                    current_sentiment=rel_data.get("current_sentiment", "中立"),
                    history_notes=[rel_data.get("description", "")]
                )
                await character_db.add_relationship(source_id, relationship)

    async def _vectorize_result(self, result: dict) -> None:
        """
        将人物设定向量化存储，供后续语义检索。

        向量化策略：
            1. 主角：将 name, appearance, personality_core, background 组合为文本，
               存入 character_cards collection，type=protagonist。
            2. 配角：同样组合关键信息，额外包含 relationship_to_protagonist，
               存入同一 collection，type=supporting。

        检索场景示例：
            - "主角的性格特点" → 检索到主角的人物卡向量。
            - "与主角有师徒关系的角色" → 检索到配角的向量（因为文本中包含"与主角关系"）。
            - "会炼丹的角色" → 检索到包含"炼丹"关键词的人物卡。

        Args:
            result: 已保存的人物数据字典。
        """
        from vector_store.store import VectorStore

        vector_store = VectorStore(self.project_id)

        # 向量化主角
        protagonist = result.get("protagonist", {})
        protagonist_text = f"""{protagonist.get('name', '')}
外貌：{protagonist.get('appearance', '')}
性格：{protagonist.get('personality_core', '')}
背景：{protagonist.get('background', '')}
"""
        await vector_store.upsert(
            collection="character_cards",
            id="protagonist",
            text=protagonist_text,
            metadata={
                "name": protagonist.get("name", ""),
                "type": "protagonist",
                "source": "character_generation"
            }
        )

        # 向量化配角
        supporting = result.get("supporting_characters", [])
        for i, char in enumerate(supporting):
            char_text = f"""{char.get('name', '')}
外貌：{char.get('appearance', '')}
性格：{char.get('personality_core', '')}
背景：{char.get('background', '')}
与主角关系：{char.get('relationship_to_protagonist', '')}
"""
            await vector_store.upsert(
                collection="character_cards",
                id=f"supporting_{i}",
                text=char_text,
                metadata={
                    "name": char.get("name", ""),
                    "type": "supporting",
                    "role": char.get("role_in_story", ""),
                    "source": "character_generation"
                }
            )
