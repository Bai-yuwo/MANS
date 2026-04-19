"""
generators/arc_planner.py

弧线规划器

职责边界：
    - 基于全局大纲中某一幕的宏观数据，生成该幕内单条弧线（10-20章）的详细规划。
    - 输出包含弧线主题、情绪曲线、章节里程碑、转折点、伏笔设计、因果链等内容。
    - 弧线规划只保留宏观设计，不生成具体场景、详细对话或每章的精细情节。
    - 通过 StoryDB 持久化，同时向量化存储到向量库。
    - 弧线中的新伏笔设计直接写入 ForeshadowingDB。

生成内容：
    1. 弧线基本信息（arc_id, arc_number, chapter_range, arc_theme, arc_goal, protagonist_development）。
    2. 情绪弧线（emotional_arc）：开篇情绪、高潮位置、高潮情绪、结尾情绪、整体模式（上升/下降/波浪）。
    3. 关键转折点（key_turning_points）：1-3个，每个包含 chapter/name/description/impact。
    4. 章节里程碑（chapter_milestones）：数组长度等于章节数，每章包含 milestone（进展）和 must_happen（核心事件）。
    5. 伏笔设计（foreshadowing_design）：2-4条新伏笔，包含类型、描述、埋设/暗示/触发/解决章节。
    6. 因果链（causal_highlights）：2-4条关键因果，说明"因为A，所以B"的逻辑。

设计原则：
    - 宏观视角：严禁写场景细节、对话、心理描写，只保留弧线的骨架设计。
    - 里程碑精简：每章只写两句话（milestone 20字以内 + must_happen 30字以内）。
    - 伏笔预留：只设计新伏笔，已有伏笔不需要在这里操作。
    - 章节范围约束：所有 chapter 数值必须在弧线章节范围内。

与 ChapterPlanner 的分工：
    - ArcPlanner 输出的是"弧线层面的宏观设计"（情绪走向、里程碑、转折点）。
    - ChapterPlanner 基于里程碑自主设计"单章的场景序列"（场景意图、POV、出场人物等）。
    - 两者界限明确：ArcPlanner 不预生成 ChapterPlan，ChapterPlanner 不修改弧线设计。

典型用法：
    planner = ArcPlanner(project_id="xxx")
    arc_plan = await planner.generate(
        arc_number=1,
        act_data=act1_data,
        bible_data=bible_data,
        characters_data=characters_data
    )
"""

from typing import Any

from generators.base_generator import BaseGenerator, ValidationError
from knowledge_bases.story_db import StoryDB
from knowledge_bases.foreshadowing_db import ForeshadowingDB


class ArcPlanner(BaseGenerator):
    """
    弧线规划器

    弧线（Arc）是介于"幕"和"章"之间的中间层规划单元。一条弧线通常包含 10-20 章，
    对应全局大纲中的一幕（act）。弧线规划的核心任务是：在保持宏观视角的前提下，
    为每一章定义"必须发生什么"和"情绪如何变化"。

    设计哲学：
        - "方向感"优于"详细感"：弧线规划应该给 ChapterPlanner 足够的创作自由度，
          同时确保不偏离全局大纲的方向。
        - 情绪先行：先确定整体情绪走向（上升/下降/波浪），再填充具体事件。
        - 伏笔预埋：在弧线阶段就确定新伏笔的埋设和触发节点，让伏笔分布更自然。

    继承自 BaseGenerator，复用带重试的生成流程和闭环修正机制。
    """

    def _get_generator_name(self) -> str:
        """返回生成器名称，用于日志和进度报告中标识当前环节。"""
        return "ArcPlanner"

    def _build_prompt(self,
                      arc_number: int,
                      act_data: dict,
                      bible_data: dict,
                      characters_data: dict,
                      existing_foreshadowing: list = None,
                      **kwargs) -> str:
        """
        构建弧线规划 prompt。

        Prompt 设计要点：
            1. 明确告知 LLM 这是"弧线层面的宏观规划"，严禁包含具体场景或对话。
            2. 提供弧线基本信息（序号、章节范围、主题、描述、发展方向）。
            3. 提供主角信息，确保弧线围绕主角成长展开。
            4. 提供详细的字段说明和数量约束（转折点1-3个、伏笔2-4条等）。
            5. 里程碑数组长度必须等于章节数，这是一个强约束。

        Args:
            arc_number: 弧线序号（从1开始）。
            act_data: 全局大纲中对应幕的数据，包含 name/description/chapter_range/key_directions。
            bible_data: Bible 数据。
            characters_data: 人物数据。
            existing_foreshadowing: 已有伏笔列表（可选，当前未使用但预留）。
            **kwargs: 预留扩展参数。

        Returns:
            完整的 prompt 字符串。
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
        解析弧线规划响应。

        使用基类提供的 _safe_json_parse() 进行安全解析。
        若解析失败，基类会自动构造修正提示词并重试。

        Args:
            response: LLM 返回的原始 JSON 字符串。

        Returns:
            解析后的弧线规划数据字典。
        """
        return self._safe_json_parse(response)

    def _validate_result(self, result: dict) -> bool:
        """
        验证弧线规划数据完整性。

        验证项：
            1. arc_id 存在性：必须有 arc_id 字段。
            2. 章节范围有效性：start_chapter <= end_chapter，且至少包含1章。
            3. 情绪弧线：必须有 emotional_arc，且 pattern 只能是"上升"、"下降"、"波浪"。
            4. 转折点：至少1个，最多3个，每个转折点 chapter 必须在弧线范围内。
            5. 章节里程碑：数组长度必须等于章节数，且 chapter_number 必须连续递增。
            6. 伏笔设计：至少2条，最多4条，每个伏笔的 planted_chapter/trigger_chapter/resolution_chapter
               必须在弧线范围内。

        Args:
            result: 解析后的弧线规划数据字典。

        Returns:
            True 表示验证通过。

        Raises:
            ValidationError: 验证失败时抛出，包含具体错误信息。
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
        保存弧线规划到知识库，并将新伏笔写入伏笔库。

        保存流程：
            1. 规范化 arc_id：去掉重复的 "arc_" 前缀，确保保存为 arcs/arc_1.json。
            2. 调用 StoryDB.save_arc_plan() 保存弧线规划。
            3. 提取 foreshadowing_design，将每条新伏笔转换为 ForeshadowingItem 存入 ForeshadowingDB。

        与 ChapterPlanner 的分工明确：
            ArcPlanner 只保存弧线规划和伏笔设计，不预生成 ChapterPlan。
            详细的单章规划完全交给 ChapterPlanner 负责。

        Args:
            result: 验证通过的弧线规划数据字典。
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
        将弧线规划向量化存储，供后续语义检索。

        向量化策略：
            1. 弧线主题：将 arc_theme, arc_goal, protagonist_development 和情绪走向组合为文本，
               存入 outlines collection，type=arc。
            2. 转折点：每个转折点单独向量化，metadata 包含 arc_id 和 chapter_number，type=turning_point。
            3. 章节里程碑：每个里程碑单独向量化，metadata 包含 arc_id 和 chapter_number，type=chapter_milestone。

        检索场景示例：
            - "第一弧线的高潮在哪里" → 检索到对应弧线的向量。
            - "第15章需要发生什么" → 检索到对应里程碑的向量。

        Args:
            result: 已保存的弧线规划数据字典。
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
