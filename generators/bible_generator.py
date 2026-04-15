"""
generators/bible_generator.py
世界观 Bible 生成器

职责：基于 ProjectMeta 生成完整的世界观设定
输出：符合 schemas.CombatSystem 和 WorldRule 结构的 Bible 数据
"""

from typing import Any

from generators.base_generator import BaseGenerator, GenerationError
from core.schemas import ProjectMeta, CombatSystem, WorldRule
from knowledge_bases.bible_db import BibleDB


class BibleGenerator(BaseGenerator):
    """
    世界观 Bible 生成器
    
    生成内容：
    - 世界名称和基本设定
    - 战力体系（修炼体系、等级划分、突破条件）
    - 世界规则（物理法则、社会法则、特殊规则）
    - 地理与势力格局
    - 历史背景
    
    使用示例：
        generator = BibleGenerator(project_id="xxx")
        bible_data = await generator.generate(project_meta=project_meta)
    """
    
    def _get_generator_name(self) -> str:
        return "BibleGenerator"
    
    def _build_prompt(self, project_meta: ProjectMeta, **kwargs) -> str:
        """
        构建 Bible 生成 prompt
        
        Args:
            project_meta: 项目元信息
            
        Returns:
            完整的 prompt 字符串
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
        
        # 可选信息
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
- 确保 JSON 格式正确，所有字符串使用双引号
- 所有字段都必须填写，不能为空
"""
        return prompt
    
    def _parse_response(self, response: str) -> dict:
        """
        解析 Bible 生成响应
        
        Args:
            response: LLM 返回的 JSON 字符串
            
        Returns:
            解析后的 Bible 数据字典
        """
        return self._safe_json_parse(response)
    
    def _validate_result(self, result: dict) -> bool:
        """
        验证 Bible 数据完整性
        
        检查项：
        1. 必需字段是否存在
        2. 战力体系结构是否完整
        3. 世界规则列表是否非空
        4. 地理和势力信息是否完整
        
        Args:
            result: 解析后的 Bible 数据
            
        Returns:
            验证是否通过
            
        Raises:
            ValidationError: 验证失败时抛出详细信息
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
        保存 Bible 到知识库
        
        Args:
            result: 验证通过的 Bible 数据
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
        
        bible_db.save(bible_data)
    
    async def _vectorize_result(self, result: dict) -> None:
        """
        将 Bible 内容向量化存储
        
        向量化内容：
        - 每条世界规则
        - 战力体系描述
        - 地理区域描述
        - 势力描述
        
        Args:
            result: 已保存的 Bible 数据
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
                    "category": rule.get("category", "general"),
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
        for i, faction in enumerate(factions):
            await vector_store.upsert(
                collection="bible_rules",
                id=f"faction_{i}",
                text=f"{faction['name']}: {faction['description']}",
                metadata={
                    "category": "social",
                    "importance": faction.get("power_level", "medium"),
                    "source": "bible_generation"
                }
            )
