"""
generators/chapter_planner.py

章节规划器

职责边界：
    - 基于弧线规划的宏观数据，自主设计单章的详细场景序列。
    - 输出符合 core/schemas.py 中 ChapterPlan 和 ScenePlan 数据结构的完整章节规划。
    - 通过 StoryDB 持久化，同时向量化存储到向量库。
    - 是连接"弧线规划"和"正文生成"的关键桥梁。

生成内容：
    1. 章节基本信息（chapter_number, title, arc_id, chapter_goal, emotional_arc, key_events）。
    2. 场景序列（scenes）：2-6 个场景，每个场景包含：
        - scene_index: 在本章内的序号（从0开始连续）。
        - intent: 场景意图（30字以内，说明场景要达成什么）。
        - pov_character: 主视角人物姓名。
        - present_characters: 出场人物列表（必须包含 pov_character）。
        - emotional_tone: 情绪基调（如压抑/热血/温情/紧张）。
        - foreshadowing_to_plant: 要埋入的伏笔 ID 列表（可选）。
        - foreshadowing_to_trigger: 要触发的伏笔 ID 列表（可选）。
        - target_word_count: 目标字数（800-2000）。
        - special_instructions: 特殊写作指示（可选）。

设计原则：
    - 自主设计：ChapterPlanner 基于弧线里程碑"自主"设计场景，而非简单拆分里程碑。
    - 情绪节奏：相邻场景情绪应有变化，整章形成完整情绪曲线。
    - 视角统一：一个场景只用一个 POV 人物。
    - 人物控制：单个场景出场人物建议 2-4 人，避免"全员大会"导致注意力分散。
    - 结尾钩子：70% 的章节结尾应留悬念。

验证逻辑：
    - chapter_number 和 chapter_goal 必须存在。
    - 场景列表非空，且不超过 8 个。
    - scene_index 从 0 开始连续递增。
    - 每个场景必须有 intent（100字以内）和 pov_character。
    - present_characters 必须包含 pov_character。
    - target_word_count 在 300-3000 之间。

典型用法：
    planner = ChapterPlanner(project_id="xxx")
    chapter_plan = await planner.generate(
        chapter_number=5,
        arc_plan=arc_plan,
        previous_chapter_summary="上一章摘要..."
    )
"""

from typing import Any

from generators.base_generator import BaseGenerator, ValidationError
from core.schemas import ChapterPlan, ScenePlan
from knowledge_bases.story_db import StoryDB
from knowledge_bases.character_db import CharacterDB
from knowledge_bases.foreshadowing_db import ForeshadowingDB


class ChapterPlanner(BaseGenerator):
    """
    章节规划器

    章节规划是 MANS 写作流程中的关键调度单元。Writer 每次只生成一个场景（Scene），
    而 ChapterPlanner 负责定义一个章节内所有场景的"序列"和"意图"。
    可以说，ChapterPlanner 定义了"写什么"，而 Writer 负责"怎么写"。

    自主设计原则：
        ChapterPlanner 不是简单地将弧线里程碑"机械拆分"为场景。
        相反，它基于以下信息"自主"设计场景序列：
            1. 本章的里程碑和必须发生的事件。
            2. 前一章和后一章的里程碑（把握上下文衔接）。
            3. 弧线情绪走向（确保场景情绪符合整体趋势）。
            4. 本章涉及的转折点（如有）。
            5. 上一章的摘要（避免重复或遗漏）。

    场景类型参考：
        - 开场场景：建立情境、引入人物、设定基调（300-500字）。
        - 对话场景：推进关系、揭示信息、制造冲突（800-1500字）。
        - 行动场景：战斗、追逐、探索、突破（1000-2000字）。
        - 内心场景：人物思考、情感变化、决策时刻（500-800字）。
        - 过渡场景：时间跳跃、地点转换、节奏调整（300-500字）。
        - 结尾场景：制造悬念、情感升华、铺垫下文（300-800字）。

    继承自 BaseGenerator，复用带重试的生成流程和闭环修正机制。
    """

    def _get_generator_name(self) -> str:
        """返回生成器名称，用于日志和进度报告中标识当前环节。"""
        return "ChapterPlanner"

    def _build_prompt(self,
                      chapter_number: int,
                      arc_plan: dict,
                      previous_chapter_summary: str = "",
                      **kwargs) -> str:
        """
        构建章节规划 prompt。

        Prompt 设计要点：
            1. 提供弧线宏观信息（主题、目标、主角成长方向、情绪走向）。
            2. 精确定位本章在弧线中的位置（当前里程碑、前一章进展、后一章铺垫）。
            3. 列出本章涉及的转折点（如有）。
            4. 提供上一章摘要，确保上下文衔接。
            5. 提供详细的场景规划原则（数量、意图设计、情绪节奏、人物出场、伏笔处理、字数分配）。
            6. 使用 JSON 示例引导 LLM 输出正确的数据结构。

        Args:
            chapter_number: 章节编号。
            arc_plan: 弧线规划数据，包含 arc_theme/chapter_range/emotional_arc/milestones/turning_points。
            previous_chapter_summary: 上一章的摘要（可选，用于上下文衔接）。
            **kwargs: 预留扩展参数。

        Returns:
            完整的 prompt 字符串。
        """
        arc_id = arc_plan.get("arc_id", "unknown")
        arc_theme = arc_plan.get("arc_theme", "")
        chapter_range = arc_plan.get("chapter_range", [0, 0])

        # 本章里程碑
        milestones = arc_plan.get("chapter_milestones", [])
        current_milestone = None
        for ms in milestones:
            if ms.get("chapter_number") == chapter_number:
                current_milestone = ms
                break
        if not current_milestone:
            current_milestone = {
                "chapter_number": chapter_number,
                "milestone": "推进剧情",
                "must_happen": "本章需完成的核心事件"
            }

        # 本章涉及的转折点
        turning_points = arc_plan.get("key_turning_points", [])
        current_turning_points = [
            tp for tp in turning_points
            if tp.get("chapter") == chapter_number
        ]

        # 情绪弧线
        emotional_arc = arc_plan.get("emotional_arc", {})

        # 相邻章里程碑（用于把握上下文）
        prev_milestone = None
        next_milestone = None
        for ms in milestones:
            if ms.get("chapter_number") == chapter_number - 1:
                prev_milestone = ms
            if ms.get("chapter_number") == chapter_number + 1:
                next_milestone = ms

        prompt = f"""基于以下弧线宏观规划，自主设计第 {chapter_number} 章的详细场景序列。

## 弧线信息

- **弧线ID**：{arc_id}
- **弧线主题**：{arc_theme}
- **弧线目标**：{arc_plan.get('arc_goal', '')}
- **主角成长方向**：{arc_plan.get('protagonist_development', '')}
- **弧线章节范围**：第 {chapter_range[0]} 章 ~ 第 {chapter_range[1]} 章

## 弧线情绪走向

- **整体模式**：{emotional_arc.get('pattern', '波浪')}
- **开篇情绪**：{emotional_arc.get('opening', '')}
- **高潮情绪**：{emotional_arc.get('climax_emotion', '')}（约第 {emotional_arc.get('climax_chapter', 0)} 章）
- **结尾情绪**：{emotional_arc.get('ending', '')}

## 本章定位

- **章节编号**：{chapter_number}
- **里程碑**：{current_milestone.get('milestone', '')}
- **必须发生**：{current_milestone.get('must_happen', '')}
"""

        if prev_milestone:
            prompt += f"- **前一章进展**：{prev_milestone.get('milestone', '')}\n"
        if next_milestone:
            prompt += f"- **后一章铺垫**：{next_milestone.get('milestone', '')}\n"

        if current_turning_points:
            prompt += "\n## 本章转折点\n\n"
            for tp in current_turning_points:
                prompt += f"- **{tp.get('name', '')}**：{tp.get('description', '')}（影响：{tp.get('impact', '')}）\n"

        if previous_chapter_summary:
            prompt += f"""

## 上一章摘要

{previous_chapter_summary}
"""

        prompt += f"""

## 输出要求

请输出严格的 JSON 格式，包含完整的章节规划：

```json
{{
  "chapter_number": {chapter_number},
  "title": "章节标题",
  "arc_id": "{arc_id}",
  "chapter_goal": "本章对主线的推进目标（一句话）",
  "emotional_arc": "本章的情绪走向描述，如：紧张期待 → 险象环生 → 意外惊喜",
  "key_events": ["关键事件1", "关键事件2"],

  "scenes": [
    {{
      "scene_index": 0,
      "intent": "场景意图（30字以内，说明场景要达成什么）",
      "pov_character": "主视角人物姓名",
      "present_characters": ["出场人物1", "出场人物2", "出场人物3"],
      "emotional_tone": "情绪基调（如：压抑/热血/温情/紧张）",
      "foreshadowing_to_plant": ["要埋入的伏笔ID（可选）"],
      "foreshadowing_to_trigger": ["要触发的伏笔ID（可选）"],
      "target_word_count": 1200,
      "special_instructions": "特殊写作指示（可选，如：注意节奏控制、突出某个细节等）"
    }}
  ]
}}
```

## 场景规划原则

### 场景数量
- **短章**（过渡/铺垫）：2-3个场景
- **正常章**（标准推进）：3-5个场景
- **长章**（战斗/高潮）：4-6个场景

### 场景意图设计
1. **简洁明确**：控制在30字以内
2. **可执行性**：Writer 能理解并执行
3. **留有余地**：不要过度指定细节，给 AI 创作空间
4. **聚焦单一**：每个场景只完成一个主要任务

### 场景类型参考
1. **开场场景**：建立情境、引入人物、设定基调
2. **对话场景**：推进关系、揭示信息、制造冲突
3. **行动场景**：战斗、追逐、探索、突破
4. **内心场景**：人物思考、情感变化、决策时刻
5. **过渡场景**：时间跳跃、地点转换、节奏调整
6. **结尾场景**：制造悬念、情感升华、铺垫下文

### 情绪节奏
1. **起伏变化**：相邻场景情绪应有变化，避免单调
2. **情绪弧线**：整章形成完整的情绪曲线
3. **高潮位置**：高潮场景通常放在章节后1/3处
4. **结尾钩子**：70%的章节结尾应留悬念

### 人物出场
1. **控制数量**：单个场景出场人物建议 2-4 人
2. **主次分明**：明确主角和配角
3. **视角统一**：一个场景只用一个 POV 人物
4. **轮换平衡**：避免某个人物长期不出场

### 伏笔处理
1. **埋设场景**：选择信息自然的场景埋入伏笔
2. **触发场景**：确保伏笔触发有合理铺垫
3. **避免堆砌**：单场景不要处理过多伏笔

### 字数分配
1. **开场场景**：300-500字
2. **对话场景**：800-1500字
3. **行动场景**：1000-2000字
4. **高潮场景**：1500-2500字
5. **结尾场景**：300-800字

## 重要提示

- 只输出 JSON，不要输出任何其他内容
- 不要使用 markdown 代码块包裹
- 确保 JSON 格式正确，所有字符串最外层使用双引号
- **JSON 字符串值内部如需引号，必须使用英文单引号（'），严禁使用双引号或中文引号**
- scene_index 从 0 开始连续编号
- present_characters 必须包含 pov_character
- target_word_count 建议 800-2000 之间
"""
        return prompt

    def _parse_response(self, response: str) -> dict:
        """
        解析章节规划响应。

        使用基类提供的 _safe_json_parse() 进行安全解析。
        若解析失败，基类会自动构造修正提示词并重试。

        Args:
            response: LLM 返回的原始 JSON 字符串。

        Returns:
            解析后的章节规划数据字典。
        """
        return self._safe_json_parse(response)

    def _validate_result(self, result: dict) -> bool:
        """
        验证章节规划数据完整性。

        验证项：
            1. 基本信息：必须有 chapter_number 和 chapter_goal。
            2. 场景列表非空：scenes 不能为空列表。
            3. 场景数量上限：不超过 8 个（过多会导致单场景过短，影响阅读体验）。
            4. 场景索引连续性：scene_index 必须从 0 开始连续递增（0, 1, 2, ...）。
            5. 场景意图：每个场景必须有 intent，且不超过 100 字（意图过长说明不够聚焦）。
            6. POV 人物：每个场景必须有 pov_character。
            7. 出场人物列表：必须非空，且必须包含 pov_character。
            8. 字数目标：target_word_count 必须在 300-3000 之间（合理范围）。

        Args:
            result: 解析后的章节规划数据字典。

        Returns:
            True 表示验证通过。

        Raises:
            ValidationError: 验证失败时抛出，包含具体错误信息。
        """
        # 验证基本信息
        if "chapter_number" not in result:
            raise ValidationError(
                "缺少 chapter_number",
                stage="validation"
            )

        if "chapter_goal" not in result:
            raise ValidationError(
                "缺少 chapter_goal",
                stage="validation"
            )

        # 验证场景列表
        scenes = result.get("scenes", [])
        if not scenes:
            raise ValidationError(
                "场景列表不能为空",
                stage="validation"
            )

        if len(scenes) > 8:
            raise ValidationError(
                f"场景数量过多: {len(scenes)} 个，建议不超过 8 个",
                stage="validation"
            )

        # 验证每个场景
        for i, scene in enumerate(scenes):
            if scene.get("scene_index") != i:
                raise ValidationError(
                    f"第 {i+1} 个场景索引错误: {scene.get('scene_index')}，应为 {i}",
                    stage="validation"
                )

            if "intent" not in scene:
                raise ValidationError(
                    f"第 {i+1} 个场景缺少 intent",
                    stage="validation"
                )

            if len(scene.get("intent", "")) > 100:
                raise ValidationError(
                    f"第 {i+1} 个场景意图过长: {len(scene.get('intent', ''))} 字，建议30字以内",
                    stage="validation"
                )

            if "pov_character" not in scene:
                raise ValidationError(
                    f"第 {i+1} 个场景缺少 pov_character",
                    stage="validation"
                )

            present_chars = scene.get("present_characters", [])
            if not present_chars:
                raise ValidationError(
                    f"第 {i+1} 个场景出场人物不能为空",
                    stage="validation"
                )

            if scene["pov_character"] not in present_chars:
                raise ValidationError(
                    f"第 {i+1} 个场景 POV 人物不在出场人物列表中",
                    stage="validation"
                )

            # 验证字数目标
            word_count = scene.get("target_word_count", 1200)
            if not 300 <= word_count <= 3000:
                raise ValidationError(
                    f"第 {i+1} 个场景字数目标不合理: {word_count}，建议在 300-3000 之间",
                    stage="validation"
                )

        return True

    async def _save_result(self, result: dict) -> None:
        """
        保存章节规划到知识库。

        保存流程：
            1. 将 scenes 数组中的每个场景数据转换为 ScenePlan Pydantic 对象。
            2. 构建 ChapterPlan Pydantic 对象，包含所有 ScenePlan。
            3. 调用 StoryDB.save_chapter_plan() 进行原子写入。

        数据转换：
            原始 JSON 中的字段直接映射到 Pydantic 模型字段，缺失字段使用默认值。
            例如：emotional_tone 默认为"平静"，target_word_count 默认为 1200。

        Args:
            result: 验证通过的章节规划数据字典。
        """
        story_db = StoryDB(self.project_id)

        # 构建 ChapterPlan 对象
        scenes_data = result.get("scenes", [])
        scene_plans = []

        for scene_data in scenes_data:
            scene_plan = ScenePlan(
                scene_index=scene_data.get("scene_index", 0),
                intent=scene_data.get("intent", ""),
                pov_character=scene_data.get("pov_character", ""),
                present_characters=scene_data.get("present_characters", []),
                emotional_tone=scene_data.get("emotional_tone", "平静"),
                foreshadowing_to_plant=scene_data.get("foreshadowing_to_plant", []),
                foreshadowing_to_trigger=scene_data.get("foreshadowing_to_trigger", []),
                target_word_count=scene_data.get("target_word_count", 1200),
                special_instructions=scene_data.get("special_instructions", "")
            )
            scene_plans.append(scene_plan)

        chapter_plan = ChapterPlan(
            chapter_number=result.get("chapter_number", 0),
            title=result.get("title", ""),
            arc_id=result.get("arc_id", ""),
            chapter_goal=result.get("chapter_goal", ""),
            emotional_arc=result.get("emotional_arc", ""),
            key_events=result.get("key_events", []),
            scenes=scene_plans
        )

        # 保存到 story_db
        await story_db.save_chapter_plan(
            result.get("chapter_number", 0),
            chapter_plan.model_dump()
        )

    async def _vectorize_result(self, result: dict) -> None:
        """
        将章节规划向量化存储，供后续语义检索。

        向量化策略：
            1. 章节整体：将 chapter_number, title, chapter_goal, emotional_arc, key_events 组合为文本，
               存入 outlines collection，type=chapter_plan。
            2. 每个场景：将 scene_index, intent, pov_character, present_characters, emotional_tone 组合为文本，
               存入同一 collection，type=scene_plan。

        检索场景示例：
            - "第5章讲了什么" → 检索到章节规划的向量。
            - "哪些场景涉及战斗" → 检索到 scene_plan 类型的向量。
            - "主角视角的场景" → 通过 metadata 中的 pov_character 过滤检索。

        Args:
            result: 已保存的章节规划数据字典。
        """
        from vector_store.store import VectorStore

        vector_store = VectorStore(self.project_id)
        chapter_number = result.get("chapter_number", 0)

        # 向量化章节整体
        chapter_text = f"""第{chapter_number}章：{result.get('title', '')}
目标：{result.get('chapter_goal', '')}
情绪：{result.get('emotional_arc', '')}
关键事件：{'；'.join(result.get('key_events', []))}
"""
        await vector_store.upsert(
            collection="outlines",
            id=f"chapter_{chapter_number}_plan",
            text=chapter_text,
            metadata={
                "type": "chapter_plan",
                "chapter_number": chapter_number,
                "arc_id": result.get("arc_id", ""),
                "source": "chapter_planning"
            }
        )

        # 向量化每个场景
        scenes = result.get("scenes", [])
        for scene in scenes:
            scene_index = scene.get("scene_index", 0)
            scene_text = f"""第{chapter_number}章 场景{scene_index}：{scene.get('intent', '')}
视角：{scene.get('pov_character', '')}
出场人物：{'、'.join(scene.get('present_characters', []))}
情绪：{scene.get('emotional_tone', '')}
"""
            await vector_store.upsert(
                collection="outlines",
                id=f"chapter_{chapter_number}_scene_{scene_index}_plan",
                text=scene_text,
                metadata={
                    "type": "scene_plan",
                    "chapter_number": chapter_number,
                    "scene_index": scene_index,
                    "source": "chapter_planning"
                }
            )
