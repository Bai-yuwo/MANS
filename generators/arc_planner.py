"""
generators/arc_planner.py
弧线规划器

职责：基于全局大纲生成单条弧线的详细规划（10-20章）
输出：包含情绪曲线、伏笔布局、章节因果链的弧线规划
"""

from typing import Any

from generators.base_generator import BaseGenerator, ValidationError
from knowledge_bases.story_db import StoryDB
from knowledge_bases.foreshadowing_db import ForeshadowingDB


class ArcPlanner(BaseGenerator):
    """
    弧线规划器
    
    生成内容：
    - 弧线整体目标和主题
    - 情绪曲线节点（高潮/低谷分布）
    - 每章的关键事件
    - 伏笔布局（埋设/触发/解决节点）
    - 章节间因果链
    
    使用示例：
        planner = ArcPlanner(project_id="xxx")
        arc_plan = await planner.generate(
            arc_number=1,
            act_data=act1_data,
            bible_data=bible_data,
            characters_data=characters_data
        )
    """
    
    def _get_generator_name(self) -> str:
        return "ArcPlanner"
    
    def _build_prompt(self,
                      arc_number: int,
                      act_data: dict,
                      bible_data: dict,
                      characters_data: dict,
                      existing_foreshadowing: list = None,
                      **kwargs) -> str:
        """
        构建弧线规划 prompt
        
        Args:
            arc_number: 弧线序号（从1开始）
            act_data: 对应幕的结构数据
            bible_data: Bible 数据
            characters_data: 人物数据
            existing_foreshadowing: 已存在的伏笔列表
            
        Returns:
            完整的 prompt 字符串
        """
        # 提取信息
        chapter_range = act_data.get("chapter_range", [1, 20])
        start_chapter, end_chapter = chapter_range
        num_chapters = end_chapter - start_chapter + 1
        
        protagonist = characters_data.get("protagonist", {})
        
        prompt = f"""基于以下信息，为小说生成第 {arc_number} 条弧线的详细规划。

## 弧线基本信息

- **弧线序号**：{arc_number}
- **章节范围**：第 {start_chapter} 章 ~ 第 {end_chapter} 章（共 {num_chapters} 章）
- **弧线主题**：{act_data.get('name', '未命名')}
- **整体描述**：{act_data.get('description', '')}

## 关键事件（来自全局大纲）

"""
        
        for event in act_data.get("key_events", []):
            prompt += f"- {event}\n"
        
        prompt += f"""

## 主角信息

- **姓名**：{protagonist.get('name', '主角')}
- **当前目标**：{', '.join(protagonist.get('active_goals', ['成长']))}
- **核心矛盾**：{', '.join(protagonist.get('core_contradictions', []))}

## 输出要求

请输出严格的 JSON 格式，包含详细的弧线规划：

```json
{{
  "arc_id": "arc_{arc_number}",
  "arc_number": {arc_number},
  "chapter_range": [{start_chapter}, {end_chapter}],
  "arc_theme": "弧线主题（一句话概括）",
  "arc_goal": "弧线目标（主角在这弧线要完成什么）",
  "protagonist_development": "主角在这弧线中的成长",
  
  "emotional_curve": [
    {{
      "chapter": {start_chapter},
      "emotion": "情绪标签（如：平静/紧张/兴奋/绝望）",
      "intensity": 5,
      "description": "该章节的情绪状态描述"
    }}
  ],
  
  "chapter_plans": [
    {{
      "chapter_number": {start_chapter},
      "title": "章节标题（可暂定）",
      "key_event": "本章关键事件",
      "protagonist_action": "主角在本章的行动",
      "obstacle": "本章遇到的阻碍",
      "outcome": "本章结果",
      "cliffhanger": "本章结尾钩子（可选）",
      "present_characters": ["出场人物1", "出场人物2"],
      "location": "主要场景地点",
      "emotional_tone": "情绪基调"
    }}
  ],
  
  "foreshadowing_layout": [
    {{
      "foreshadowing_id": "使用已有伏笔ID或留空表示新建",
      "description": "伏笔内容",
      "action": "plant|hint|trigger|resolve",
      "chapter": {start_chapter},
      "notes": "处理方式的简要说明"
    }}
  ],
  
  "causal_chain": [
    {{
      "from_chapter": {start_chapter},
      "to_chapter": {start_chapter + 1},
      "cause": "原因",
      "effect": "结果",
      "description": "因果描述"
    }}
  ],
  
  "new_foreshadowing": [
    {{
      "type": "plot|character|world|emotional",
      "description": "新伏笔描述",
      "planted_chapter": {start_chapter},
      "trigger_chapter": {start_chapter + 5},
      "resolution_chapter": {end_chapter},
      "importance": "major|minor"
    }}
  ]
}}
```

## 生成原则

### 情绪曲线设计
1. **起伏节奏**：避免平铺直叙，每2-3章要有情绪变化
2. **高潮分布**：弧线中至少1-2个小高潮，结尾留钩子
3. **强度范围**：1-10分，日常/铺垫章节3-5分，冲突章节6-8分，高潮章节9-10分
4. **曲线类型**：
   - 上升型：从低到高，适合成长/突破弧线
   - 下降型：从高到低，适合挫折/低谷弧线
   - 波浪型：起伏波动，适合复杂情节弧线

### 章节规划原则
1. **每章必须有**：关键事件 + 主角行动 + 结果
2. **章节钩子**：70%的章节结尾应留悬念或钩子
3. **人物轮换**：避免同一批人物连续出场，保持新鲜感
4. **场景变化**：每2-3章更换主要场景

### 伏笔布局原则
1. **埋设（plant）**：早期章节埋入新伏笔
2. **暗示（hint）**：中期章节对已埋伏笔进行暗示/呼应
3. **触发（trigger）**：后期章节触发伏笔产生效果
4. **解决（resolve）**：弧线结尾解决部分伏笔

### 因果链设计
1. **连续性**：每章结果应成为下一章的原因
2. **累积效应**：小因果累积成大因果
3. **意外转折**：允许有外部因素打破预期因果
4. **清晰逻辑**：读者能理解"因为A所以B"

### 新伏笔设计
1. **数量**：每条弧线埋设2-4个新伏笔
2. **跨度**：新伏笔可跨越多条弧线
3. **类型**：与弧线主题相关的伏笔类型

## 重要提示

- 只输出 JSON，不要输出任何其他内容
- 不要使用 markdown 代码块包裹
- 确保 JSON 格式正确，所有字符串使用双引号
- emotional_curve 和 chapter_plans 数组长度应与章节数匹配
- 伏笔的 chapter 必须在弧线范围内
- 因果链应连接弧线内的连续章节
"""
        return prompt
    
    def _parse_response(self, response: str) -> dict:
        """
        解析弧线规划响应
        
        Args:
            response: LLM 返回的 JSON 字符串
            
        Returns:
            解析后的弧线规划数据
        """
        return self._safe_json_parse(response)
    
    def _validate_result(self, result: dict) -> bool:
        """
        验证弧线规划数据完整性
        
        Args:
            result: 解析后的弧线规划数据
            
        Returns:
            验证是否通过
        """
        # 验证基本信息
        if "arc_id" not in result:
            raise ValidationError(
                "缺少 arc_id",
                stage="validation"
            )
        
        chapter_range = result.get("chapter_range", [0, 0])
        start_chapter, end_chapter = chapter_range
        num_chapters = end_chapter - start_chapter + 1
        
        if num_chapters < 1:
            raise ValidationError(
                f"无效的章节范围: {chapter_range}",
                stage="validation"
            )
        
        # 验证情绪曲线
        emotional_curve = result.get("emotional_curve", [])
        if len(emotional_curve) != num_chapters:
            raise ValidationError(
                f"情绪曲线数量不匹配: 有 {len(emotional_curve)} 个，应有 {num_chapters} 个",
                stage="validation"
            )
        
        for i, ec in enumerate(emotional_curve):
            if ec.get("chapter") != start_chapter + i:
                raise ValidationError(
                    f"第 {i+1} 个情绪曲线章节号错误: {ec.get('chapter')}，应为 {start_chapter + i}",
                    stage="validation"
                )
            
            intensity = ec.get("intensity", 0)
            if not 1 <= intensity <= 10:
                raise ValidationError(
                    f"第 {i+1} 个情绪强度无效: {intensity}，应在 1-10 之间",
                    stage="validation"
                )
        
        # 验证章节规划
        chapter_plans = result.get("chapter_plans", [])
        if len(chapter_plans) != num_chapters:
            raise ValidationError(
                f"章节规划数量不匹配: 有 {len(chapter_plans)} 个，应有 {num_chapters} 个",
                stage="validation"
            )
        
        for i, cp in enumerate(chapter_plans):
            if cp.get("chapter_number") != start_chapter + i:
                raise ValidationError(
                    f"第 {i+1} 个章节规划编号错误",
                    stage="validation"
                )
            
            if "key_event" not in cp:
                raise ValidationError(
                    f"第 {i+1} 章缺少 key_event",
                    stage="validation"
                )
        
        # 验证伏笔布局
        foreshadowing_layout = result.get("foreshadowing_layout", [])
        for fs in foreshadowing_layout:
            chapter = fs.get("chapter", 0)
            if not start_chapter <= chapter <= end_chapter:
                raise ValidationError(
                    f"伏笔章节 {chapter} 超出弧线范围 [{start_chapter}, {end_chapter}]",
                    stage="validation"
                )
            
            action = fs.get("action", "")
            if action not in ["plant", "hint", "trigger", "resolve"]:
                raise ValidationError(
                    f"无效的伏笔动作: {action}",
                    stage="validation"
                )
        
        return True
    
    async def _save_result(self, result: dict) -> None:
        """
        保存弧线规划到知识库
        
        Args:
            result: 验证通过的弧线规划数据
        """
        story_db = StoryDB(self.project_id)
        foreshadowing_db = ForeshadowingDB(self.project_id)
        
        # 保存弧线规划
        arc_id = result.get("arc_id", f"arc_{result.get('arc_number', 1)}")
        story_db.save_arc_plan(arc_id, result)
        
        # 为每章创建简版 ChapterPlan
        chapter_plans = result.get("chapter_plans", [])
        for cp in chapter_plans:
            chapter_number = cp.get("chapter_number", 0)
            
            # 构建简版章节规划
            simple_plan = {
                "chapter_number": chapter_number,
                "title": cp.get("title", f"第{chapter_number}章"),
                "arc_id": arc_id,
                "chapter_goal": cp.get("key_event", ""),
                "emotional_arc": cp.get("emotional_tone", "平静"),
                "key_events": [cp.get("key_event", "")],
                "scenes": [],  # 将在 chapter_planner 中填充
                "previous_chapter_summary": ""
            }
            
            story_db.save_chapter_plan(chapter_number, simple_plan)
        
        # 保存新伏笔
        new_foreshadowing = result.get("new_foreshadowing", [])
        for fs in new_foreshadowing:
            foreshadowing_db.add_foreshadowing(
                fs_type=fs.get("type", "plot"),
                description=fs.get("description", ""),
                trigger_range=(
                    fs.get("trigger_chapter", 1),
                    fs.get("resolution_chapter", 100)
                ),
                urgency=fs.get("importance", "medium")
            )
    
    async def _vectorize_result(self, result: dict) -> None:
        """
        将弧线规划向量化存储
        
        Args:
            result: 已保存的弧线规划数据
        """
        from vector_store.store import VectorStore
        
        vector_store = VectorStore(self.project_id)
        arc_id = result.get("arc_id", "arc_unknown")
        
        # 向量化弧线主题
        arc_text = f"""{result.get('arc_theme', '')}
目标：{result.get('arc_goal', '')}
主角成长：{result.get('protagonist_development', '')}
"""
        await vector_store.upsert(
            collection="outlines",
            id=arc_id,
            text=arc_text,
            metadata={
                "type": "arc",
                "arc_number": result.get("arc_number", 0),
                "chapter_range": result.get("chapter_range", [0, 0]),
                "source": "arc_planning"
            }
        )
        
        # 向量化每章规划
        chapter_plans = result.get("chapter_plans", [])
        for cp in chapter_plans:
            chapter_number = cp.get("chapter_number", 0)
            chapter_text = f"""第{chapter_number}章：{cp.get('title', '')}
关键事件：{cp.get('key_event', '')}
主角行动：{cp.get('protagonist_action', '')}
结果：{cp.get('outcome', '')}
"""
            await vector_store.upsert(
                collection="outlines",
                id=f"{arc_id}_ch{chapter_number}",
                text=chapter_text,
                metadata={
                    "type": "chapter_plan",
                    "arc_id": arc_id,
                    "chapter_number": chapter_number,
                    "source": "arc_planning"
                }
            )
