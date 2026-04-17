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
        构建弧线规划 prompt（缩略版）

        弧线只保留宏观设计：情绪走向、转折点、里程碑、伏笔设计、关键因果。
        具体的每章标题、场景、详细事件全部交给 ChapterPlanner。
        """
        # 提取信息
        chapter_range = act_data.get("chapter_range", [1, 20])
        start_chapter, end_chapter = chapter_range
        num_chapters = end_chapter - start_chapter + 1

        protagonist = characters_data.get("protagonist", {})
        key_directions = act_data.get("key_directions", [])

        prompt = f"""基于以下信息，为小说生成第 {arc_number} 条弧线的**宏观规划**。

## 弧线基本信息

- **弧线序号**：{arc_number}
- **章节范围**：第 {start_chapter} 章 ~ 第 {end_chapter} 章（共 {num_chapters} 章）
- **弧线主题**：{act_data.get('name', '未命名')}
- **整体描述**：{act_data.get('description', '')}

## 发展方向（来自全局大纲）

"""
        for direction in key_directions:
            prompt += f"- {direction}\n"

        prompt += f"""

## 主角信息

- **姓名**：{protagonist.get('name', '主角')}
- **当前目标**：{', '.join(protagonist.get('active_goals', ['成长']))}
- **核心矛盾**：{', '.join(protagonist.get('core_contradictions', []))}

## 输出要求

请输出严格的 JSON 格式。注意：这是**弧线层面**的宏观规划，**不要**包含具体场景、详细对话、每章的精细情节。只保留弧线的骨架设计。

```json
{{
  "arc_id": "arc_{arc_number}",
  "arc_number": {arc_number},
  "chapter_range": [{start_chapter}, {end_chapter}],
  "arc_theme": "弧线主题（一句话概括）",
  "arc_goal": "弧线目标（主角在这弧线要完成什么）",
  "protagonist_development": "主角在这弧线中的成长方向（一句话）",

  "emotional_arc": {{
    "opening": "开篇情绪（如：平静/压抑/兴奋）",
    "climax_chapter": {start_chapter + num_chapters // 2},
    "climax_emotion": "高潮情绪",
    "ending": "结尾情绪",
    "pattern": "上升|下降|波浪"
  }},

  "key_turning_points": [
    {{
      "chapter": {start_chapter + num_chapters // 3},
      "name": "转折点名称（简洁）",
      "description": "一句话描述转折内容",
      "impact": "对主角/局势的影响"
    }}
  ],

  "chapter_milestones": [
    {{
      "chapter_number": {start_chapter},
      "milestone": "本章里程碑/进展（20字以内）",
      "must_happen": "本章必须发生的核心事件（一句话）"
    }}
  ],

  "foreshadowing_design": [
    {{
      "type": "plot|character|world|emotional",
      "description": "新伏笔描述（一句话）",
      "planted_chapter": {start_chapter},
      "hint_chapters": [{start_chapter + 3}],
      "trigger_chapter": {start_chapter + num_chapters // 2},
      "resolution_chapter": {end_chapter},
      "importance": "major|minor"
    }}
  ],

  "causal_highlights": [
    {{
      "from_chapter": {start_chapter},
      "to_chapter": {start_chapter + 2},
      "cause": "前因（一句话）",
      "effect": "后果（一句话）"
    }}
  ]
}}
```

## 生成原则

### 情绪弧线（emotional_arc）
1. 只描述**整体走向**，不需要每章的情绪
2. 标明高潮大概出现在哪一章
3. pattern 只能是：上升 / 下降 / 波浪

### 转折点（key_turning_points）
1. 只保留对主角命运或局势产生**重大影响**的节点
2. 数量：**1-3个**，不要多
3. 每个转折点用一句话描述即可

### 章节里程碑（chapter_milestones）
1. 数组长度必须与章节数 {num_chapters} 一致
2. 每章只写两句话：`milestone`（进展）和 `must_happen`（必须发生的事件）
3. **严禁写场景细节、对话、心理描写**
4. milestone 控制在 20 字以内
5. must_happen 控制在 30 字以内

### 伏笔设计（foreshadowing_design）
1. 数量：**2-4条**
2. 只设计**新伏笔**，已有伏笔不需要在这里操作
3. 说明埋设、暗示、触发、解决的大致章节位置
4. 暗示章节（hint_chapters）可以留空数组

### 因果链（causal_highlights）
1. 只保留**关键因果**，不是每章都要有
2. 数量：**2-4条**
3. 连接的是弧线中的关键节点，不一定连续章节
4. 说明"因为A，所以B"的逻辑即可

## 重要提示

- 只输出 JSON，不要输出任何其他内容
- 不要使用 markdown 代码块包裹
- 确保 JSON 格式正确，所有字符串最外层使用双引号
- **JSON 字符串值内部如需引号，必须使用英文单引号（'），严禁使用双引号或中文引号**
- chapter_milestones 数组长度**必须等于 {num_chapters}**
- 所有 chapter 数值必须在 [{start_chapter}, {end_chapter}] 范围内
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
        验证弧线规划数据完整性（缩略版）
        """
        # 验证基本信息
        if "arc_id" not in result:
            raise ValidationError("缺少 arc_id", stage="validation")

        chapter_range = result.get("chapter_range", [0, 0])
        start_chapter, end_chapter = chapter_range
        num_chapters = end_chapter - start_chapter + 1

        if num_chapters < 1:
            raise ValidationError(
                f"无效的章节范围: {chapter_range}",
                stage="validation"
            )

        # 验证情绪弧线
        emotional_arc = result.get("emotional_arc", {})
        if not emotional_arc:
            raise ValidationError("缺少 emotional_arc", stage="validation")
        if emotional_arc.get("pattern") not in ["上升", "下降", "波浪"]:
            raise ValidationError(
                f"情绪模式无效: {emotional_arc.get('pattern')}，应为 上升/下降/波浪",
                stage="validation"
            )

        # 验证转折点
        turning_points = result.get("key_turning_points", [])
        if len(turning_points) < 1:
            raise ValidationError(
                "转折点数量不足，至少 1 个",
                stage="validation"
            )
        if len(turning_points) > 3:
            raise ValidationError(
                f"转折点数量过多: {len(turning_points)} 个，最多 3 个",
                stage="validation"
            )
        for tp in turning_points:
            chapter = tp.get("chapter", 0)
            if not start_chapter <= chapter <= end_chapter:
                raise ValidationError(
                    f"转折点章节 {chapter} 超出弧线范围",
                    stage="validation"
                )

        # 验证章节里程碑
        milestones = result.get("chapter_milestones", [])
        if len(milestones) != num_chapters:
            raise ValidationError(
                f"里程碑数量不匹配: 有 {len(milestones)} 个，应有 {num_chapters} 个",
                stage="validation"
            )
        for i, ms in enumerate(milestones):
            if ms.get("chapter_number") != start_chapter + i:
                raise ValidationError(
                    f"第 {i+1} 个里程碑章节号错误: {ms.get('chapter_number')}，应为 {start_chapter + i}",
                    stage="validation"
                )

        # 验证伏笔设计
        foreshadowing = result.get("foreshadowing_design", [])
        if len(foreshadowing) < 2:
            raise ValidationError(
                f"伏笔数量不足: 只有 {len(foreshadowing)} 个，至少 2 个",
                stage="validation"
            )
        if len(foreshadowing) > 4:
            raise ValidationError(
                f"伏笔数量过多: 有 {len(foreshadowing)} 个，最多 4 个",
                stage="validation"
            )
        for fs in foreshadowing:
            for key in ["planted_chapter", "trigger_chapter", "resolution_chapter"]:
                chapter = fs.get(key, 0)
                if chapter < start_chapter or chapter > end_chapter:
                    raise ValidationError(
                        f"伏笔 {key}={chapter} 超出弧线范围",
                        stage="validation"
                    )

        return True
    
    async def _save_result(self, result: dict) -> None:
        """
        保存弧线规划到知识库（异步）

        只保存弧线规划和伏笔设计，不再预生成每章的 ChapterPlan。
        详细的单章规划完全交给 ChapterPlanner 负责。
        """
        story_db = StoryDB(self.project_id)
        foreshadowing_db = ForeshadowingDB(self.project_id)

        arc_id = result.get("arc_id", f"arc_{result.get('arc_number', 1)}")
        # 统一文件名：去掉重复的 arc_ 前缀，确保保存为 arcs/arc_1.json
        if arc_id.startswith("arc_"):
            arc_id = arc_id[4:]
        await story_db.save_arc_plan(arc_id, result)

        # 保存新伏笔设计到伏笔库
        foreshadowing_design = result.get("foreshadowing_design", [])
        for fs in foreshadowing_design:
            await foreshadowing_db.add_foreshadowing(
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
        将弧线规划向量化存储（缩略版）
        """
        from vector_store.store import VectorStore

        vector_store = VectorStore(self.project_id)
        arc_id = result.get("arc_id", "arc_unknown")

        # 向量化弧线主题
        emotional_arc = result.get("emotional_arc", {})
        arc_text = f"""{result.get('arc_theme', '')}
目标：{result.get('arc_goal', '')}
主角成长：{result.get('protagonist_development', '')}
情绪走向：{emotional_arc.get('opening', '')} → {emotional_arc.get('climax_emotion', '')} → {emotional_arc.get('ending', '')}
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

        # 向量化转折点
        turning_points = result.get("key_turning_points", [])
        for tp in turning_points:
            chapter_number = tp.get("chapter", 0)
            tp_text = f"""转折点：{tp.get('name', '')}
第{chapter_number}章：{tp.get('description', '')}
影响：{tp.get('impact', '')}
"""
            await vector_store.upsert(
                collection="outlines",
                id=f"{arc_id}_tp{chapter_number}",
                text=tp_text,
                metadata={
                    "type": "turning_point",
                    "arc_id": arc_id,
                    "chapter_number": chapter_number,
                    "source": "arc_planning"
                }
            )

        # 向量化章节里程碑
        milestones = result.get("chapter_milestones", [])
        for ms in milestones:
            chapter_number = ms.get("chapter_number", 0)
            ms_text = f"""第{chapter_number}章里程碑：{ms.get('milestone', '')}
核心事件：{ms.get('must_happen', '')}
"""
            await vector_store.upsert(
                collection="outlines",
                id=f"{arc_id}_ch{chapter_number}",
                text=ms_text,
                metadata={
                    "type": "chapter_milestone",
                    "arc_id": arc_id,
                    "chapter_number": chapter_number,
                    "source": "arc_planning"
                }
            )
