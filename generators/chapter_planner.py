"""
generators/chapter_planner.py
章节规划器

职责：基于弧线规划生成单章的详细场景序列
输出：符合 schemas.ChapterPlan 结构的完整章节规划
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
    
    生成内容：
    - 章节标题
    - 本章目标
    - 情绪走向
    - 关键事件
    - 场景序列（2-6个场景）
    - 每个场景的详细规划
    
    使用示例：
        planner = ChapterPlanner(project_id="xxx")
        chapter_plan = await planner.generate(
            chapter_number=5,
            arc_plan=arc_plan,
            previous_chapter_summary="上一章摘要..."
        )
    """
    
    def _get_generator_name(self) -> str:
        return "ChapterPlanner"
    
    def _build_prompt(self,
                      chapter_number: int,
                      arc_plan: dict,
                      previous_chapter_summary: str = "",
                      **kwargs) -> str:
        """
        构建章节规划 prompt
        
        Args:
            chapter_number: 章节编号
            arc_plan: 弧线规划数据
            previous_chapter_summary: 上一章摘要
            
        Returns:
            完整的 prompt 字符串
        """
        # 提取弧线信息
        arc_id = arc_plan.get("arc_id", "unknown")
        arc_theme = arc_plan.get("arc_theme", "")
        
        # 找到本章的弧线内规划
        chapter_plans = arc_plan.get("chapter_plans", [])
        current_chapter_plan = None
        for cp in chapter_plans:
            if cp.get("chapter_number") == chapter_number:
                current_chapter_plan = cp
                break
        
        if not current_chapter_plan:
            # 使用默认规划
            current_chapter_plan = {
                "chapter_number": chapter_number,
                "title": f"第{chapter_number}章",
                "key_event": "推进剧情",
                "emotional_tone": "平静"
            }
        
        # 提取情绪曲线
        emotional_curve = arc_plan.get("emotional_curve", [])
        current_emotion = None
        for ec in emotional_curve:
            if ec.get("chapter") == chapter_number:
                current_emotion = ec
                break
        
        prompt = f"""基于以下弧线规划，生成第 {chapter_number} 章的详细场景序列。

## 弧线信息

- **弧线ID**：{arc_id}
- **弧线主题**：{arc_theme}
- **弧线目标**：{arc_plan.get('arc_goal', '')}

## 本章规划（来自弧线）

- **章节编号**：{chapter_number}
- **暂定标题**：{current_chapter_plan.get('title', '')}
- **关键事件**：{current_chapter_plan.get('key_event', '')}
- **主角行动**：{current_chapter_plan.get('protagonist_action', '')}
- **情绪基调**：{current_chapter_plan.get('emotional_tone', '')}
"""
        
        if current_emotion:
            prompt += f"""
- **情绪强度**：{current_emotion.get('intensity', 5)}/10
- **情绪描述**：{current_emotion.get('description', '')}
"""
        
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
        解析章节规划响应
        
        Args:
            response: LLM 返回的 JSON 字符串
            
        Returns:
            解析后的章节规划数据
        """
        return self._safe_json_parse(response)
    
    def _validate_result(self, result: dict) -> bool:
        """
        验证章节规划数据完整性
        
        Args:
            result: 解析后的章节规划数据
            
        Returns:
            验证是否通过
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
        保存章节规划到知识库
        
        Args:
            result: 验证通过的章节规划数据
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
        story_db.save_chapter_plan(
            result.get("chapter_number", 0),
            chapter_plan.model_dump()
        )
    
    async def _vectorize_result(self, result: dict) -> None:
        """
        将章节规划向量化存储
        
        Args:
            result: 已保存的章节规划数据
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
