"""
generators/outline_generator.py
全局大纲生成器

职责：基于 Bible 和人物设定生成三幕式全局大纲
输出：包含三幕结构、关键转折点、全局伏笔的大纲数据
"""

from typing import Any

from generators.base_generator import BaseGenerator, ValidationError
from core.schemas import ProjectMeta
from knowledge_bases.story_db import StoryDB
from knowledge_bases.foreshadowing_db import ForeshadowingDB


class OutlineGenerator(BaseGenerator):
    """
    全局大纲生成器
    
    生成内容：
    - 三幕结构划分（幕1/幕2a/幕2b/幕3）
    - 主线矛盾与核心冲突
    - 关键转折点（5-10个）
    - 结局方向
    - 初版全局伏笔清单（5-8条主线伏笔）
    
    使用示例：
        generator = OutlineGenerator(project_id="xxx")
        outline = await generator.generate(
            project_meta=project_meta,
            bible_data=bible_data,
            characters_data=characters_data
        )
    """
    
    def _get_generator_name(self) -> str:
        return "OutlineGenerator"
    
    def _build_prompt(self, 
                      project_meta: ProjectMeta, 
                      bible_data: dict, 
                      characters_data: dict,
                      **kwargs) -> str:
        """
        构建大纲生成 prompt
        
        Args:
            project_meta: 项目元信息
            bible_data: Bible 数据
            characters_data: 人物数据（包含主角和配角）
            
        Returns:
            完整的 prompt 字符串
        """
        # 提取关键信息
        protagonist = characters_data.get("protagonist", {})
        supporting = characters_data.get("supporting_characters", [])
        combat_system = bible_data.get("combat_system", {})
        
        # 估算章节数
        target_chapters = self._estimate_chapter_count(project_meta.target_length)
        
        prompt = f"""基于以下世界观和人物设定，为小说《{project_meta.name}》生成全局大纲。

## 作品信息

- **核心 Idea**：{project_meta.core_idea}
- **目标篇幅**：{project_meta.target_length}（约 {target_chapters} 章）
- **基调**：{project_meta.tone}

## 世界观概要

- **世界**：{bible_data.get('world_name', '')}
- **战力体系**：{combat_system.get('name', '')}
- **境界划分**：{' → '.join(combat_system.get('realms', [])[:5])}

## 主角信息

- **姓名**：{protagonist.get('name', '主角')}
- **起点**：{protagonist.get('background', '')[:100]}...
- **初始目标**：{', '.join(protagonist.get('active_goals', ['成长变强']))}
- **核心矛盾**：{', '.join(protagonist.get('core_contradictions', []))}

## 主要配角

"""
        
        # 添加配角信息
        for char in supporting[:4]:
            prompt += f"- **{char.get('name', '未知')}**：{char.get('role_in_story', '配角')}，{char.get('relationship_to_protagonist', '')}\n"
        
        prompt += f"""

## 输出要求

请输出严格的 JSON 格式，包含完整的三幕式大纲：

```json
{{
  "three_act_structure": {{
    "act1": {{
      "name": "第一幕名称",
      "chapter_range": [1, {max(1, target_chapters // 4)}],
      "description": "第一幕整体描述（50-100字）",
      "key_events": ["事件1", "事件2", "事件3"]
    }},
    "act2a": {{
      "name": "第二幕上名称",
      "chapter_range": [{max(1, target_chapters // 4) + 1}, {target_chapters // 2}],
      "description": "第二幕上描述",
      "key_events": ["事件1", "事件2", "事件3"]
    }},
    "act2b": {{
      "name": "第二幕下名称",
      "chapter_range": [{target_chapters // 2 + 1}, {target_chapters * 3 // 4}],
      "description": "第二幕下描述",
      "key_events": ["事件1", "事件2", "事件3"]
    }},
    "act3": {{
      "name": "第三幕名称",
      "chapter_range": [{target_chapters * 3 // 4 + 1}, {target_chapters}],
      "description": "第三幕描述",
      "key_events": ["事件1", "事件2", "事件3"]
    }}
  }},
  "main_conflict": {{
    "central_conflict": "核心冲突描述（主角 vs 什么/谁）",
    "protagonist_goal": "主角的核心目标",
    "antagonist_force": "对抗力量描述",
    "stakes": "失败的代价/风险"
  }},
  "turning_points": [
    {{
      "name": "转折点名称",
      "chapter": 章节数,
      "description": "转折点描述",
      "impact": "对故事的影响"
    }}
  ],
  "ending": {{
    "direction": "结局方向描述（可模糊）",
    "protagonist_arc": "主角的成长弧线",
    "resolution_type": "胜利/悲剧/ bittersweet/开放式"
  }},
  "foreshadowing_list": [
    {{
      "type": "plot|character|world|emotional",
      "description": "伏笔内容描述",
      "planted_act": "埋设幕次（act1/act2a/act2b）",
      "trigger_act": "触发幕次",
      "resolution_act": "解决幕次",
      "importance": "critical|major|minor"
    }}
  ]
}}
```

## 生成原则

### 三幕结构原则
1. **第一幕（约25%）**：建立世界、介绍人物、触发事件、主角踏上旅程
2. **第二幕上（约25%）**：上升行动、遇到盟友和敌人、第一次重大挫折
3. **第二幕下（约25%）**：深入对抗、揭示真相、最低点、转折准备
4. **第三幕（约25%）**：最终对抗、高潮、结局、人物归宿

### 转折点设计原则
1. **数量**：5-10个关键转折点
2. **分布**：每个幕至少1-2个，高潮前必须有重大转折
3. **类型**：包含正向转折（突破、发现）和负向转折（挫折、背叛）
4. **影响**：每个转折点必须显著改变故事走向或人物关系

### 伏笔设计原则
1. **数量**：5-8条主线伏笔
2. **类型分布**：
   - 剧情伏笔（plot）：3-4条，主线相关
   - 人物伏笔（character）：1-2条，人物命运相关
   - 世界伏笔（world）：1-2条，世界观揭秘相关
   - 情感伏笔（emotional）：0-1条，情感线相关
3. **时间跨度**：早期埋设的伏笔应在后期解决
4. **重要性**：至少2条 critical 级别的重要伏笔

### 核心冲突原则
1. **多层次**：外在冲突（对抗势力）+ 内在冲突（主角内心）
2. **升级**：冲突强度随章节推进而升级
3. **个人化**：冲突必须与主角的个人目标和恐惧相关

## 重要提示

- 只输出 JSON，不要输出任何其他内容
- 不要使用 markdown 代码块包裹
- 确保 JSON 格式正确，所有字符串使用双引号
- 章节范围必须连续且不重叠
- 转折点章节必须在对应幕的范围内
"""
        return prompt
    
    def _estimate_chapter_count(self, target_length: str) -> int:
        """
        根据目标篇幅估算章节数
        
        Args:
            target_length: 目标篇幅描述
            
        Returns:
            估算的章节数
        """
        length_map = {
            "短篇(<10万)": 30,
            "中篇(10-50万)": 100,
            "长篇(50-200万)": 300,
            "超长篇(200万+)": 500
        }
        return length_map.get(target_length, 100)
    
    def _parse_response(self, response: str) -> dict:
        """
        解析大纲生成响应
        
        Args:
            response: LLM 返回的 JSON 字符串
            
        Returns:
            解析后的大纲数据字典
        """
        return self._safe_json_parse(response)
    
    def _validate_result(self, result: dict) -> bool:
        """
        验证大纲数据完整性
        
        Args:
            result: 解析后的大纲数据
            
        Returns:
            验证是否通过
        """
        # 验证三幕结构
        three_act = result.get("three_act_structure", {})
        required_acts = ["act1", "act2a", "act2b", "act3"]
        for act in required_acts:
            if act not in three_act:
                raise ValidationError(
                    f"缺少 {act} 结构",
                    stage="validation"
                )
            
            act_data = three_act[act]
            if "chapter_range" not in act_data:
                raise ValidationError(
                    f"{act} 缺少 chapter_range",
                    stage="validation"
                )
            
            if "key_events" not in act_data or not act_data["key_events"]:
                raise ValidationError(
                    f"{act} 缺少关键事件",
                    stage="validation"
                )
        
        # 验证章节范围连续性
        act1_end = three_act["act1"]["chapter_range"][1]
        act2a_start = three_act["act2a"]["chapter_range"][0]
        act2a_end = three_act["act2a"]["chapter_range"][1]
        act2b_start = three_act["act2b"]["chapter_range"][0]
        act2b_end = three_act["act2b"]["chapter_range"][1]
        act3_start = three_act["act3"]["chapter_range"][0]
        
        if act2a_start != act1_end + 1:
            raise ValidationError(
                f"幕间不连续: act1 结束于 {act1_end}，act2a 开始于 {act2a_start}",
                stage="validation"
            )
        
        if act2b_start != act2a_end + 1:
            raise ValidationError(
                f"幕间不连续: act2a 结束于 {act2a_end}，act2b 开始于 {act2b_start}",
                stage="validation"
            )
        
        if act3_start != act2b_end + 1:
            raise ValidationError(
                f"幕间不连续: act2b 结束于 {act2b_end}，act3 开始于 {act3_start}",
                stage="validation"
            )
        
        # 验证核心冲突
        main_conflict = result.get("main_conflict", {})
        if "central_conflict" not in main_conflict:
            raise ValidationError(
                "缺少核心冲突描述",
                stage="validation"
            )
        
        # 验证转折点
        turning_points = result.get("turning_points", [])
        if len(turning_points) < 3:
            raise ValidationError(
                f"转折点数量不足: 只有 {len(turning_points)} 个，至少需要 3 个",
                stage="validation"
            )
        
        # 验证伏笔
        foreshadowing = result.get("foreshadowing_list", [])
        if len(foreshadowing) < 3:
            raise ValidationError(
                f"伏笔数量不足: 只有 {len(foreshadowing)} 个，至少需要 3 个",
                stage="validation"
            )
        
        return True
    
    async def _save_result(self, result: dict) -> None:
        """
        保存大纲到知识库
        
        Args:
            result: 验证通过的大纲数据
        """
        story_db = StoryDB(self.project_id)
        
        # 保存大纲
        story_db.save_outline(result)
        
        # 保存伏笔到伏笔库
        foreshadowing_db = ForeshadowingDB(self.project_id)
        foreshadowing_list = result.get("foreshadowing_list", [])
        
        for i, fs_data in enumerate(foreshadowing_list):
            # 计算触发范围
            planted_act = fs_data.get("planted_act", "act1")
            resolution_act = fs_data.get("resolution_act", "act3")
            
            # 从幕次映射到章节范围（简化处理）
            act_chapter_map = {
                "act1": (1, 25),
                "act2a": (26, 50),
                "act2b": (51, 75),
                "act3": (76, 100)
            }
            
            trigger_start = act_chapter_map.get(planted_act, (1, 25))[0]
            trigger_end = act_chapter_map.get(resolution_act, (76, 100))[1]
            
            foreshadowing_db.add_foreshadowing(
                fs_type=fs_data.get("type", "plot"),
                description=fs_data.get("description", ""),
                trigger_range=(trigger_start, trigger_end),
                urgency=fs_data.get("importance", "medium")
            )
    
    async def _vectorize_result(self, result: dict) -> None:
        """
        将大纲内容向量化存储
        
        Args:
            result: 已保存的大纲数据
        """
        from vector_store.store import VectorStore
        
        vector_store = VectorStore(self.project_id)
        
        # 向量化三幕结构
        three_act = result.get("three_act_structure", {})
        for act_name, act_data in three_act.items():
            act_text = f"""{act_data.get('name', act_name)}
{act_data.get('description', '')}
关键事件：{'；'.join(act_data.get('key_events', []))}
"""
            await vector_store.upsert(
                collection="outlines",
                id=f"act_{act_name}",
                text=act_text,
                metadata={
                    "type": "act",
                    "act_name": act_name,
                    "chapter_range": act_data.get("chapter_range", [0, 0]),
                    "source": "outline_generation"
                }
            )
        
        # 向量化转折点
        turning_points = result.get("turning_points", [])
        for i, tp in enumerate(turning_points):
            tp_text = f"""转折点：{tp.get('name', '')}
章节：{tp.get('chapter', 0)}
描述：{tp.get('description', '')}
影响：{tp.get('impact', '')}
"""
            await vector_store.upsert(
                collection="outlines",
                id=f"turning_point_{i}",
                text=tp_text,
                metadata={
                    "type": "turning_point",
                    "chapter": tp.get("chapter", 0),
                    "source": "outline_generation"
                }
            )
        
        # 向量化核心冲突
        main_conflict = result.get("main_conflict", {})
        conflict_text = f"""核心冲突：{main_conflict.get('central_conflict', '')}
主角目标：{main_conflict.get('protagonist_goal', '')}
对抗力量：{main_conflict.get('antagonist_force', '')}
风险：{main_conflict.get('stakes', '')}
"""
        await vector_store.upsert(
            collection="outlines",
            id="main_conflict",
            text=conflict_text,
            metadata={
                "type": "conflict",
                "source": "outline_generation"
            }
        )
