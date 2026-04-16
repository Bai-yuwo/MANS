"""
generators/character_generator.py
人物设定生成器

职责：基于 Bible 和 protagonist_seed 生成主角和主要配角的人物卡
输出：符合 schemas.CharacterCard 结构的人物数据
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
    
    生成内容：
    - 主角完整人物卡（外貌、性格、背景、初始状态）
    - 3-5 个主要配角人物卡
    - 人物关系网
    
    使用示例：
        generator = CharacterGenerator(project_id="xxx")
        characters = await generator.generate(
            project_meta=project_meta,
            bible_data=bible_data
        )
    """
    
    def _get_generator_name(self) -> str:
        return "CharacterGenerator"
    
    def _build_prompt(self, project_meta: ProjectMeta, bible_data: dict, **kwargs) -> str:
        """
        构建人物生成 prompt
        
        Args:
            project_meta: 项目元信息
            bible_data: Bible 数据（包含世界观、战力体系等）
            
        Returns:
            完整的 prompt 字符串
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
        解析人物生成响应
        
        Args:
            response: LLM 返回的 JSON 字符串
            
        Returns:
            解析后的人物数据字典
        """
        return self._safe_json_parse(response)
    
    def _validate_result(self, result: dict) -> bool:
        """
        验证人物数据完整性
        
        Args:
            result: 解析后的人物数据
            
        Returns:
            验证是否通过
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
        保存人物设定到知识库
        
        Args:
            result: 验证通过的人物数据
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
            relationships=[],
            first_appeared_chapter=0,
            last_updated_chapter=0
        )
        
        character_db.save_character(protagonist_card)
        
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
            
            character_db.save_character(char_card)
        
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
                character_db.add_relationship(source_id, relationship)
    
    async def _vectorize_result(self, result: dict) -> None:
        """
        将人物设定向量化存储
        
        Args:
            result: 已保存的人物数据
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
