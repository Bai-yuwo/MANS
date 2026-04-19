"""
generators/outline_generator.py

全局大纲生成器

职责边界：
    - 基于 Bible（世界观）和人物设定生成三幕式全局大纲。
    - 输出包含三幕结构、核心冲突、剧情风格、关键转折点、结局方向和全局伏笔。
    - 全局大纲只描述"方向"而非"具体情节"，保持宏观视角。
    - 通过 StoryDB 持久化，同时向量化存储到向量库。
    - 全局伏笔直接写入 ForeshadowingDB，供后续章节规划使用。

生成内容：
    1. 三幕结构（act1/act2a/act2b/act3），每幕包含名称、章节范围、描述、关键方向。
    2. 核心冲突（central_conflict, protagonist_goal, antagonist_force, stakes）。
    3. 剧情风格（growth_curve, rhythm_mode, highlight_density, description）。
    4. 关键转折点（3-5个），每个包含名称、所在章节、一句话描述。
    5. 结局方向（direction, resolution_type）。
    6. 全局伏笔清单（3-5条主线伏笔）。

设计原则：
    - 宏观视角：描述"这一阶段主角要做什么/面对什么"，而非"具体发生什么事"。
    - 基调适配：根据 ProjectMeta.tone 选择对应的节奏模式（如热血→扮猪吃虎）。
    - 章节连续性：验证四幕的章节范围必须连续且不重叠。
    - 伏笔预埋：在大纲阶段就设计好主线伏笔的埋设幕次和解决幕次。

典型用法：
    generator = OutlineGenerator(project_id="xxx")
    outline = await generator.generate(
        project_meta=project_meta,
        bible_data=bible_data,
        characters_data=characters_data
    )
"""

from typing import Any

from core.schemas import ProjectMeta
from generators.base_generator import BaseGenerator, ValidationError
from knowledge_bases.foreshadowing_db import ForeshadowingDB
from knowledge_bases.story_db import StoryDB


class OutlineGenerator(BaseGenerator):
    """
    全局大纲生成器

    全局大纲是小说叙事的顶层蓝图，定义了故事的整体走向、节奏模式和关键节点。
    它不负责具体场景或对话，只回答"故事要走向哪里"和"何时发生关键转折"。

    与后续规划器的关系：
        - ArcPlanner 将全局大纲的每一幕扩展为具体的弧线规划（情绪曲线、里程碑）。
        - ChapterPlanner 将弧线规划进一步细化为单章的场景序列。
        - 因此，全局大纲必须保持足够的抽象度，给下层规划器留出创作空间。

    生成策略：
        1. 根据 ProjectMeta.target_length 估算总章节数（短篇30章/中篇100章/长篇300章/超长篇500章）。
        2. 在 prompt 中根据 tone（基调）提供对应的大纲示例（热血/成长/虐心）。
        3. 使用详细的三幕结构示例，引导 LLM 输出正确的 JSON 格式。
        4. 提供"正确 vs 错误"的示例对比，明确什么是"方向"、什么是"情节"。

    伏笔处理：
        生成的大纲中的 foreshadowing_list 会被直接转换为 ForeshadowingItem 存入 ForeshadowingDB。
        埋设幕次和解决幕次会被映射为粗略的章节范围（act1→1-25, act2a→26-50 等）。
    """

    def _get_generator_name(self) -> str:
        """返回生成器名称，用于日志和进度报告中标识当前环节。"""
        return "OutlineGenerator"

    def get_output_schema(self) -> dict:
        """
        返回大纲生成的 JSON Schema 定义。

        Schema 结构：
            - three_act_structure: 三幕结构对象，包含 act1/act2a/act2b/act3，
              每幕有 name/chapter_range/description/key_directions。
            - main_conflict: 核心冲突对象，包含 central_conflict/protagonist_goal/antagonist_force/stakes。
            - story_pattern: 剧情风格对象，包含 growth_curve/rhythm_mode/highlight_density/description。
            - turning_points: 转折点数组，每项包含 name/chapter/description。
            - ending: 结局对象，包含 direction/resolution_type。
            - foreshadowing_list: 伏笔数组，每项包含 type/description/planted_act/resolution_act/importance。
        """
        return {
            "name": "outline_output",
            "schema": {
                "type": "object",
                "properties": {
                    "three_act_structure": {
                        "type": "object",
                        "properties": {
                            "act1": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "chapter_range": {
                                        "type": "array",
                                        "items": {"type": "integer"},
                                        "minItems": 2,
                                        "maxItems": 2
                                    },
                                    "description": {"type": "string"},
                                    "key_directions": {
                                        "type": "array",
                                        "items": {"type": "string"}
                                    }
                                },
                                "required": ["name", "chapter_range", "description", "key_directions"]
                            },
                            "act2a": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "chapter_range": {
                                        "type": "array",
                                        "items": {"type": "integer"},
                                        "minItems": 2,
                                        "maxItems": 2
                                    },
                                    "description": {"type": "string"},
                                    "key_directions": {
                                        "type": "array",
                                        "items": {"type": "string"}
                                    }
                                },
                                "required": ["name", "chapter_range", "description", "key_directions"]
                            },
                            "act2b": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "chapter_range": {
                                        "type": "array",
                                        "items": {"type": "integer"},
                                        "minItems": 2,
                                        "maxItems": 2
                                    },
                                    "description": {"type": "string"},
                                    "key_directions": {
                                        "type": "array",
                                        "items": {"type": "string"}
                                    }
                                },
                                "required": ["name", "chapter_range", "description", "key_directions"]
                            },
                            "act3": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "chapter_range": {
                                        "type": "array",
                                        "items": {"type": "integer"},
                                        "minItems": 2,
                                        "maxItems": 2
                                    },
                                    "description": {"type": "string"},
                                    "key_directions": {
                                        "type": "array",
                                        "items": {"type": "string"}
                                    }
                                },
                                "required": ["name", "chapter_range", "description", "key_directions"]
                            }
                        },
                        "required": ["act1", "act2a", "act2b", "act3"]
                    },
                    "main_conflict": {
                        "type": "object",
                        "properties": {
                            "central_conflict": {"type": "string"},
                            "protagonist_goal": {"type": "string"},
                            "antagonist_force": {"type": "string"},
                            "stakes": {"type": "string"}
                        },
                        "required": ["central_conflict", "protagonist_goal", "antagonist_force", "stakes"]
                    },
                    "story_pattern": {
                        "type": "object",
                        "properties": {
                            "growth_curve": {"type": "string"},
                            "rhythm_mode": {"type": "string"},
                            "highlight_density": {"type": "string"},
                            "description": {"type": "string"}
                        },
                        "required": ["growth_curve", "rhythm_mode", "highlight_density", "description"]
                    },
                    "turning_points": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "chapter": {"type": "integer"},
                                "description": {"type": "string"}
                            },
                            "required": ["name", "chapter", "description"]
                        }
                    },
                    "ending": {
                        "type": "object",
                        "properties": {
                            "direction": {"type": "string"},
                            "resolution_type": {"type": "string"}
                        },
                        "required": ["direction", "resolution_type"]
                    },
                    "foreshadowing_list": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {"type": "string"},
                                "description": {"type": "string"},
                                "planted_act": {"type": "string"},
                                "resolution_act": {"type": "string"},
                                "importance": {"type": "string"}
                            },
                            "required": ["type", "description", "planted_act", "resolution_act", "importance"]
                        }
                    }
                },
                "required": ["three_act_structure", "main_conflict", "story_pattern", "turning_points", "ending", "foreshadowing_list"]
            }
        }

    def _build_prompt(self,
                      project_meta: ProjectMeta,
                      bible_data: dict,
                      characters_data: dict,
                      **kwargs) -> str:
        """
        构建大纲生成 prompt。

        Prompt 设计要点：
            1. 提取主角和配角的关键信息，作为大纲生成的人物依据。
            2. 根据 target_length 估算总章节数，用于计算每幕的章节范围。
            3. 提供详细的基调控制说明，引导 LLM 根据 tone 选择合适的节奏模式。
            4. 提供"正确 vs 错误"的示例对比，明确抽象"方向"与具体"情节"的界限。
            5. 每幕只要求 2 个 key_directions，保持精简。

        Args:
            project_meta: 项目元信息。
            bible_data: Bible 数据。
            characters_data: 人物数据，包含 protagonist 和 supporting_characters。
            **kwargs: 预留扩展参数。

        Returns:
            完整的 prompt 字符串。
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

**重要**：这是全局大纲，只需要大的发展方向和关键节点。不要生成具体情节、场景或详细事件。

请输出严格的 JSON 格式：

```json
{{
  "three_act_structure": {{
    "act1": {{
      "name": "第一幕名称（简洁，如'觉醒与启程'）",
      "chapter_range": [1, {max(1, target_chapters // 4)}],
      "description": "这一阶段的发展方向（10-20字），不是详细情节",
      "key_directions": ["方向1", "方向2"]
    }},
    "act2a": {{
      "name": "第二幕上名称",
      "chapter_range": [{max(1, target_chapters // 4) + 1}, {target_chapters // 2}],
      "description": "这一阶段的发展方向（10-20字）",
      "key_directions": ["方向1", "方向2"]
    }},
    "act2b": {{
      "name": "第二幕下名称",
      "chapter_range": [{target_chapters // 2 + 1}, {target_chapters * 3 // 4}],
      "description": "这一阶段的发展方向（10-20字）",
      "key_directions": ["方向1", "方向2"]
    }},
    "act3": {{
      "name": "第三幕名称",
      "chapter_range": [{target_chapters * 3 // 4 + 1}, {target_chapters}],
      "description": "这一阶段的发展方向（10-20字）",
      "key_directions": ["方向1", "方向2"]
    }}
  }},
  "main_conflict": {{
    "central_conflict": "一句话概括核心冲突（主角 vs 什么/谁）",
    "protagonist_goal": "一句话概括主角的核心目标",
    "antagonist_force": "一句话概括对抗力量",
    "stakes": "一句话概括失败的代价"
  }},
  "story_pattern": {{
    "growth_curve": "steady_rise|up_and_down|power_fantasy|late_bloom",
    "rhythm_mode": "step_by_step|crouching_tiger|underdog_hero|last_stand",
    "highlight_density": "high|medium|low",
    "description": "一句话描述本故事的节奏特点（如'扮猪吃虎，装逼打脸'）"
  }},
  "turning_points": [
    {{
      "name": "转折点名称（简洁）",
      "chapter": 章节数,
      "description": "一句话描述转折内容"
    }}
  ],
  "ending": {{
    "direction": "结局方向（一句话，可模糊）",
    "resolution_type": "胜利/悲剧/bittersweet/开放式"
  }},
  "foreshadowing_list": [
    {{
      "type": "plot|character|world",
      "description": "一句话伏笔描述",
      "planted_act": "埋设幕次",
      "resolution_act": "解决幕次",
      "importance": "critical|major"
    }}
  ]
}}
```

## 剧情风格控制

**基调**：`{project_meta.tone}`

请根据上述基调，在大纲中体现相应的节奏模式：

| 基调关键词 | 节奏模式 | 大纲特点 |
|-----------|---------|---------|
| 热血/爽文 | 一路开挂/扮猪吃虎 | 主角快速成长，每次挫折都是装逼机会 |
| 成长/养成 | 稳扎稳打 | 每步成长都有代价，稳中求进 |
| 跌宕起伏 | 大起大落 | 有高潮有低谷，情绪波动大 |
| 虐心/悲剧 | 先甜后虐 | 前期顺利后期反转，结局可能不好 |
| 悬疑/烧脑 | 真相递进 | 每次突破都揭示新真相，高潮解密 |
| 轻松/日常 | 张弛有度 | 有热血也有日常，劳逸结合 |

### 不同基调的大纲体现

**热血/扮猪吃虎基调**：
- 第一幕就展示主角潜力（被嘲笑）
- 第二幕多次"打脸"名场面
- 第三幕碾压式结局

**稳扎稳打基调**：
- 每阶段成长都有明确代价和积累
- 盟友和敌人都有自己完整的成长线
- 结局是量变到质变的自然结果

**跌宕起伏基调**：
- 第一幕末尾要有"跌"
- 第二幕上要有"起"
- 第二幕下要有更低谷
- 第三幕最终"起"

请根据 `{project_meta.tone}` 选择合适的节奏模式，在大纲中体现。

## 生成原则

### 宏观视角
1. **方向而非情节**：描述"这一阶段主角要做什么/面对什么"，不是"具体发生什么事"
2. **人物关系变化**：关注人物间关系的大方向（结盟/对立/背叛），不是具体事件
3. **势力格局演变**：关注大势力间的冲突趋势，不是具体战役

### 内容精简
1. **每幕 2 个方向**：只列出最重要的 2 个发展方向
2. **转折点 3-5 个**：只列出全局关键转折点
3. **伏笔 3-5 条**：只列出主线伏笔
4. **描述要短**：每个描述 10-20 字，越简短越好

### 不同风格的 key_directions 示例

**扮猪吃虎（热血）**：
- act1: "展现潜力但被低估"、"获得第一个打脸机会"
- act2a: "多次装逼成功"、"积累名气"
- act2b: "遭遇真正强敌"、"被打脸后反转"
- act3: "终极装逼"、"碾压所有质疑者"

**稳扎稳打（成长）**：
- act1: "夯实基础"、"建立人脉"
- act2a: "稳步提升境界"、"经营势力"
- act2b: "遭遇瓶颈期"、"寻找突破契机"
- act3: "水到渠成的质变"

**跌宕起伏（虐心）**：
- act1: "前期顺风顺水"
- act2a: "遭遇重大挫折，跌入谷底"
- act2b: "艰难恢复，再次跌入更低谷"
- act3: "绝地反击，最终胜利或悲剧结局"

### 示例对比

❌ 错误（太细节）：
- "主角在下山途中遭遇埋伏，与神秘黑衣人激战，身受重伤后被路过的老者所救"

❌ 错误（太平淡）：
- "主角开始修炼"
- "主角遇到一个朋友"

✅ 正确（符合风格的方向）：
- "扮猪吃虎基调：第一幕就展示主角潜力但被嘲笑"（热血）
- "稳步积累：每一境界都有明确代价和收获"（成长）
- "跌入谷底：最信任的人背叛"（虐心）

## 重要提示

- 只输出 JSON，不要输出任何其他内容
- 不要使用 markdown 代码块包裹
- **JSON 字符串要极简，每项不超过 20 字**
- **JSON 字符串值内部如需引号，必须使用英文单引号（'）**
- 章节范围必须连续且不重叠
"""
        return prompt

    def _estimate_chapter_count(self, target_length: str) -> int:
        """
        根据目标篇幅估算章节数。

        估算规则：
            - 短篇(<10万): 约30章，每章3000字左右。
            - 中篇(10-50万): 约100章，每章4000-5000字。
            - 长篇(50-200万): 约300章，每章5000-6000字。
            - 超长篇(200万+): 约500章，每章4000字以上。

        此估算用于计算三幕结构中每幕的章节范围。实际章节数可能因
        ChapterPlanner 的规划而略有出入。

        Args:
            target_length: 目标篇幅描述字符串（来自 ProjectMeta.target_length）。

        Returns:
            估算的章节数整数。
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
        解析大纲生成响应。

        使用基类提供的 _safe_json_parse() 进行安全解析。
        若解析失败，基类会自动构造修正提示词并重试。

        Args:
            response: LLM 返回的原始 JSON 字符串。

        Returns:
            解析后的大纲数据字典。
        """
        return self._safe_json_parse(response)

    def _validate_result(self, result: dict) -> bool:
        """
        验证大纲数据完整性。

        验证项：
            1. 三幕结构存在性：必须有 act1/act2a/act2b/act3，每个 act 有 chapter_range 和 key_directions。
            2. 章节范围连续性：act1 结束 + 1 == act2a 开始，act2a 结束 + 1 == act2b 开始，
               act2b 结束 + 1 == act3 开始。不连续时抛出 ValidationError。
            3. 核心冲突：必须有 central_conflict。
            4. 剧情风格：必须有 story_pattern，且 growth_curve/rhythm_mode 必须在合法枚举中。
            5. 转折点数量：至少3个，最多5个（保持宏观视角）。
            6. 伏笔数量：至少3个，最多5个（只列主线伏笔）。

        Args:
            result: 解析后的大纲数据字典。

        Returns:
            True 表示验证通过。

        Raises:
            ValidationError: 验证失败时抛出，包含具体错误信息。
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

            if "key_directions" not in act_data or not act_data["key_directions"]:
                raise ValidationError(
                    f"{act} 缺少发展方向",
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

        # 验证剧情风格
        story_pattern = result.get("story_pattern", {})
        if not story_pattern:
            raise ValidationError(
                "缺少 story_pattern（剧情风格控制）",
                stage="validation"
            )

        valid_growth = ["steady_rise", "up_and_down", "power_fantasy", "late_bloom"]
        if story_pattern.get("growth_curve") not in valid_growth:
            raise ValidationError(
                f"growth_curve 必须是 {valid_growth} 之一",
                stage="validation"
            )

        valid_rhythm = ["step_by_step", "crouching_tiger", "underdog_hero", "last_stand"]
        if story_pattern.get("rhythm_mode") not in valid_rhythm:
            raise ValidationError(
                f"rhythm_mode 必须是 {valid_rhythm} 之一",
                stage="validation"
            )

        # 验证转折点
        turning_points = result.get("turning_points", [])
        if len(turning_points) < 3:
            raise ValidationError(
                f"转折点数量不足: 只有 {len(turning_points)} 个，至少需要 3 个",
                stage="validation"
            )
        if len(turning_points) > 5:
            raise ValidationError(
                f"转折点数量过多: 有 {len(turning_points)} 个，最多 5 个（保持宏观视角）",
                stage="validation"
            )

        # 验证伏笔
        foreshadowing = result.get("foreshadowing_list", [])
        if len(foreshadowing) < 3:
            raise ValidationError(
                f"伏笔数量不足: 只有 {len(foreshadowing)} 个，至少需要 3 个",
                stage="validation"
            )
        if len(foreshadowing) > 5:
            raise ValidationError(
                f"伏笔数量过多: 有 {len(foreshadowing)} 个，最多 5 个（只列主线伏笔）",
                stage="validation"
            )

        return True

    async def _save_result(self, result: dict) -> None:
        """
        保存大纲到知识库，并将全局伏笔写入伏笔库。

        保存流程：
            1. 调用 StoryDB.save_outline() 保存三幕结构、核心冲突、转折点、结局等。
            2. 提取 foreshadowing_list，将每条全局伏笔转换为 ForeshadowingItem 存入 ForeshadowingDB。

        伏笔章节范围映射：
            由于大纲只记录幕次（act1/act2a/act2b/act3），需要映射为粗略的章节范围：
            - act1 → (1, 25)
            - act2a → (26, 50)
            - act2b → (51, 75)
            - act3 → (76, 100)
            这是一个简化处理，实际触发范围在 ArcPlanner 中会进一步细化。

        Args:
            result: 验证通过的大纲数据字典。
        """
        story_db = StoryDB(self.project_id)

        # 保存大纲
        await story_db.save_outline(result)

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

            await foreshadowing_db.add_foreshadowing(
                fs_type=fs_data.get("type", "plot"),
                description=fs_data.get("description", ""),
                trigger_range=(trigger_start, trigger_end),
                urgency=fs_data.get("importance", "medium")
            )

    async def _vectorize_result(self, result: dict) -> None:
        """
        将大纲内容向量化存储，供后续语义检索。

        向量化策略：
            1. 三幕结构：每幕单独向量化，metadata 标记 type=act 和 chapter_range。
            2. 转折点：每个转折点单独向量化，metadata 标记 type=turning_point 和 chapter。
            3. 核心冲突：整体向量化，metadata 标记 type=conflict。

        存入 collection "outlines"，便于后续按语义检索故事结构信息。
        例如："故事的高潮在哪里" → 检索到对应幕或转折点的向量。

        Args:
            result: 已保存的大纲数据字典。
        """
        from vector_store.store import VectorStore

        vector_store = VectorStore(self.project_id)

        # 向量化三幕结构
        three_act = result.get("three_act_structure", {})
        for act_name, act_data in three_act.items():
            act_text = f"""{act_data.get('name', act_name)}
{act_data.get('description', '')}
发展方向：{'；'.join(act_data.get('key_directions', []))}
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
