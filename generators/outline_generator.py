"""
generators/outline_generator.py
全局大纲生成器

职责：基于 Bible 和人物设定生成三幕式全局大纲
输出：包含三幕结构、关键转折点、全局伏笔的大纲数据
"""

from typing import Any

from core.schemas import ProjectMeta
from generators.base_generator import BaseGenerator, ValidationError
from knowledge_bases.foreshadowing_db import ForeshadowingDB
from knowledge_bases.story_db import StoryDB


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
    
    def get_output_schema(self) -> dict:
        """返回大纲生成的 JSON Schema"""
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
        保存大纲到知识库（异步）
        
        Args:
            result: 验证通过的大纲数据
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
