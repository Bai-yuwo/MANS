"""
core/injection_engine.py
注入引擎 - 系统的"注意力机制"

设计原则：
1. 三层处理：规则层（同步）→ 检索层（异步）→ 裁剪层（条件）
2. Token 预算控制：严格控制在 INJECTION_TOKEN_BUDGET 内
3. 优先级排序：强制注入 > 向量检索 > 动态裁剪
"""

import json
import time
from typing import Optional
from pathlib import Path

from core.config import get_config
from core.schemas import (
    ScenePlan, ChapterPlan, InjectionContext,
    CharacterCard, WorldRule, ForeshadowingItem,
    WorldRuleCategory, WorldRuleImportance
)
from core.llm_client import LLMClient, quick_call
from core.logging_config import get_logger, log_exception
from core.update_extractor import clean_json_response

logger = get_logger('core.injection_engine')


class InjectionEngine:
    """
    注入引擎
    
    职责：在 Writer 每次生成前，组装最优的 InjectionContext
    
    使用示例：
        engine = InjectionEngine(project_id="xxx")
        context = await engine.build_context(
            scene_plan=scene_plan,
            chapter_plan=chapter_plan
        )
    """
    
    # Token 预算分配（总计 3500）
    BUDGET_ALLOCATION = {
        "system_rules": 800,        # 系统指令 + 写作规则
        "scene_intent": 200,        # 场景意图 + 章节目标
        "previous_text": 400,       # 上一场景末尾文本
        "character_card": 600,      # 单个人物卡
        "foreshadowing": 300,       # 激活的伏笔（最多2条）
        "world_rules": 400,         # 世界规则（向量检索）
        "style_examples": 300,      # 文风范例
        "similar_scenes": 400,      # 相似历史场景
        "buffer": 400,              # 预留 buffer
    }
    
    def __init__(self, project_id: str):
        self.project_id = project_id
        self.config = get_config()
        self.llm_client = LLMClient()
        
        # 项目路径
        self.project_path = Path(self.config.WORKSPACE_PATH) / project_id
        
        # 知识库引用（延迟初始化）
        self._character_db = None
        self._bible_db = None
        self._foreshadowing_db = None
        self._story_db = None
        self._style_db = None
        self._vector_store = None
    
    @property
    def character_db(self):
        """延迟初始化人物库"""
        if self._character_db is None:
            from knowledge_bases.character_db import CharacterDB
            self._character_db = CharacterDB(self.project_id)
        return self._character_db
    
    @property
    def bible_db(self):
        """延迟初始化世界观库"""
        if self._bible_db is None:
            from knowledge_bases.bible_db import BibleDB
            self._bible_db = BibleDB(self.project_id)
        return self._bible_db
    
    @property
    def foreshadowing_db(self):
        """延迟初始化伏笔库"""
        if self._foreshadowing_db is None:
            from knowledge_bases.foreshadowing_db import ForeshadowingDB
            self._foreshadowing_db = ForeshadowingDB(self.project_id)
        return self._foreshadowing_db
    
    @property
    def story_db(self):
        """延迟初始化故事库"""
        if self._story_db is None:
            from knowledge_bases.story_db import StoryDB
            self._story_db = StoryDB(self.project_id)
        return self._story_db
    
    @property
    def vector_store(self):
        """延迟初始化向量存储"""
        if self._vector_store is None:
            from vector_store.store import VectorStore
            self._vector_store = VectorStore(self.project_id)
        return self._vector_store

    @property
    def style_db(self):
        """延迟初始化文风库"""
        if self._style_db is None:
            from knowledge_bases.style_db import StyleDB
            self._style_db = StyleDB(self.project_id)
        return self._style_db
    
    async def build_context(
        self,
        scene_plan: ScenePlan,
        chapter_plan: ChapterPlan
    ) -> InjectionContext:
        """
        构建注入上下文（主入口）
        
        Args:
            scene_plan: 当前场景规划
            chapter_plan: 当前章节规划
        
        Returns:
            组装完成的 InjectionContext
        """
        # 第一层：规则层（异步，<10ms）
        mandatory = await self._get_mandatory_context(scene_plan, chapter_plan)
        
        # 计算剩余预算
        used_tokens = self._estimate_mandatory_tokens(mandatory)
        remaining_budget = self.config.INJECTION_TOKEN_BUDGET - used_tokens
        
        # 第二层：向量检索层（异步，~200ms）
        retrieved = await self._get_retrieved_context(
            scene_plan,
            chapter_number=chapter_plan.chapter_number,
            budget_tokens=remaining_budget
        )

        # 合并上下文
        context_data = {**mandatory, **retrieved}

        # 第三层：裁剪层（条件执行，仅超预算时触发）
        total_tokens = self._estimate_tokens(context_data)
        if total_tokens > self.config.INJECTION_TOKEN_BUDGET:
            context_data = await self._trim_to_budget(
                context_data,
                self.config.INJECTION_TOKEN_BUDGET
            )

        # 计算下一场景锚点（用于锁死剧情节奏）
        next_scene_intent = ""
        if hasattr(chapter_plan, 'scenes') and chapter_plan.scenes:
            current_idx = scene_plan.scene_index
            for s in chapter_plan.scenes:
                if getattr(s, 'scene_index', None) == current_idx + 1:
                    next_scene_intent = s.intent
                    break

        # 组装最终上下文
        injection_context = await self._assemble_context(
            context_data,
            used_tokens=used_tokens,
            remaining_budget=remaining_budget,
            next_scene_intent=next_scene_intent
        )

        return injection_context
    
    async def _get_mandatory_context(
        self,
        scene_plan: ScenePlan,
        chapter_plan: ChapterPlan
    ) -> dict:
        """
        第一层：规则层
        必须注入的信息，直接从存储读取，零 LLM 调用
        """
        # 1. 当前场景意图
        scene_intent = scene_plan.intent

        # 2. 本章目标
        chapter_goal = chapter_plan.chapter_goal

        # 3. 上一场景末尾文本
        previous_text = await self._get_previous_scene_tail(
            chapter_plan.chapter_number,
            scene_plan.scene_index,
            tail_chars=400
        )

        # 4. 出场人物卡（限制数量）
        character_cards = []
        max_chars = self.config.INJECTION_MAX_CHARACTERS
        for name in scene_plan.present_characters[:max_chars]:
            char = await self.character_db.get_character(name)
            if char:
                character_cards.append(char)
            else:
                logger.warning(f"人物 '{name}' 不在知识库中，无法注入人物卡。请检查 scene_plan.present_characters 与 CharacterDB 名称是否一致。")

        # 5. 需要处理的伏笔（限制数量）
        max_fs = self.config.INJECTION_MAX_FORESHADOWING
        active_foreshadowing = []
        if hasattr(self.foreshadowing_db, 'get_active_for_chapter'):
            trigger_ids = []
            if hasattr(scene_plan, 'foreshadowing_to_trigger') and scene_plan.foreshadowing_to_trigger:
                trigger_ids.extend(scene_plan.foreshadowing_to_trigger)
            if hasattr(scene_plan, 'foreshadowing_to_plant') and scene_plan.foreshadowing_to_plant:
                trigger_ids.extend(scene_plan.foreshadowing_to_plant)
            all_foreshadowing = await self.foreshadowing_db.get_active_for_chapter(
                current_chapter=chapter_plan.chapter_number,
                trigger_ids=trigger_ids or None
            )
            active_foreshadowing = all_foreshadowing[:max_fs]

        return {
            "scene_plan": scene_plan,
            "chapter_goal": chapter_goal,
            "previous_text": previous_text,
            "character_cards": character_cards,
            "active_foreshadowing": active_foreshadowing,
        }
    
    async def _get_retrieved_context(
        self,
        scene_plan: ScenePlan,
        chapter_number: int,
        budget_tokens: int
    ) -> dict:
        """
        第二层：向量检索层
        通过语义检索获取相关背景知识
        """
        # 构建检索 query
        query = f"{scene_plan.intent} {' '.join(scene_plan.present_characters)} {scene_plan.emotional_tone}"

        results = {}

        # 如果启用向量检索
        if self.config.ENABLE_VECTOR_SEARCH:
            try:
                # 检索相关世界规则
                world_rules_raw = await self.vector_store.search(
                    collection="bible_rules",
                    query=query,
                    n_results=5
                )
                # 转换为 WorldRule 对象，确保枚举值有效
                valid_categories = {e.value for e in WorldRuleCategory}
                valid_importances = {e.value for e in WorldRuleImportance}
                world_rules = []
                for r in world_rules_raw:
                    cat = r.get("metadata", {}).get("category", "special")
                    if cat not in valid_categories:
                        cat = "special"
                    imp = r.get("metadata", {}).get("importance", "major")
                    if imp not in valid_importances:
                        imp = "major"
                    try:
                        world_rules.append(
                            WorldRule(
                                content=r.get("text", ""),
                                category=cat,
                                source_chapter=chapter_number,
                                importance=imp
                            )
                        )
                    except Exception:
                        continue
                results["world_rules"] = world_rules

                # 检索相似历史场景
                similar_scenes_raw = await self.vector_store.search(
                    collection="chapter_scenes",
                    query=query,
                    n_results=3
                )
                results["similar_scenes"] = [r.get("text", "") for r in similar_scenes_raw]

                # 检索文风范例
                style_examples_raw = await self.vector_store.search(
                    collection="style_examples",
                    query=scene_plan.emotional_tone,
                    n_results=2
                )
                results["style_examples"] = [r.get("text", "") for r in style_examples_raw]

            except Exception as e:
                # 向量检索失败，记录日志但继续执行
                logger.error(f"向量检索失败: {e}")
                results["world_rules"] = []
                results["similar_scenes"] = []
                results["style_examples"] = []
        else:
            results["world_rules"] = []
            results["similar_scenes"] = []
            results["style_examples"] = []

        return results
    
    async def _trim_to_budget(
        self,
        context: dict,
        budget_tokens: int
    ) -> dict:
        """
        第三层：裁剪层
        当检索结果超出预算时，调用小模型决定保留哪些内容。
        对于 WorldRule，只返回序号索引，代码层保留原始对象，绝不丢失元数据。
        """
        rules = context.get('world_rules', [])

        trim_prompt = f"""
当前写作场景：{context['scene_plan'].intent}
情绪基调：{context['scene_plan'].emotional_tone}
Token 预算：{budget_tokens}

以下是候选背景信息（已超出预算），请按重要性选取：

【世界规则】（仅返回需要保留的序号，不要改写内容）
{self._format_world_rules_indexed(rules)}

【相似历史场景】（可直接压缩/概括）
{self._format_similar_scenes(context.get('similar_scenes', []))}

【文风范例】（可直接压缩/概括）
{self._format_style_examples(context.get('style_examples', []))}

请输出选择结果（JSON格式）：
{{
    "keep_rule_indices": [0, 2],
    "similar_scenes": ["保留的场景片段"],
    "style_reference": "文风描述"
}}
"""

        try:
            from core.llm_client import LLMClient
            client = LLMClient()

            trim_schema = {
                "name": "trim_output",
                "schema": {
                    "type": "object",
                    "properties": {
                        "keep_rule_indices": {
                            "type": "array",
                            "items": {"type": "integer"}
                        },
                        "similar_scenes": {
                            "type": "array",
                            "items": {"type": "string"}
                        },
                        "style_reference": {"type": "string"}
                    },
                    "required": ["keep_rule_indices", "similar_scenes", "style_reference"]
                }
            }

            response_obj = await client.call_with_retry(
                role="trim",
                prompt=trim_prompt,
                max_tokens=budget_tokens // 2,
                temperature=0.1,
                response_format="json_schema",
                json_schema=trim_schema
            )

            # 无条件 JSON 清洗
            cleaned = clean_json_response(response_obj.content)
            trimmed = json.loads(cleaned)

            # 代码层根据索引保留原始 WorldRule 对象，绝不丢失 source_chapter / category / importance
            keep_indices = set(trimmed.get("keep_rule_indices", []))
            context["world_rules"] = [
                rules[i] for i in keep_indices
                if isinstance(i, int) and 0 <= i < len(rules)
            ]
            context["similar_scenes"] = trimmed.get("similar_scenes", [])
            context["style_reference"] = trimmed.get("style_reference", "")

        except Exception as e:
            logger.error(f"上下文裁剪失败: {e}，使用硬截断")
            # 硬截断：按重要性保留前两条，绝不重写规则内容
            sorted_rules = sorted(
                context.get("world_rules", []),
                key=lambda r: (
                    0 if getattr(r, 'importance', '') == WorldRuleImportance.CRITICAL else
                    1 if getattr(r, 'importance', '') == WorldRuleImportance.MAJOR else 2
                )
            )
            context["world_rules"] = sorted_rules[:2]
            context["similar_scenes"] = context.get("similar_scenes", [])[:1]
            context["style_reference"] = ""

        return context

    async def _assemble_context(
        self,
        context: dict,
        used_tokens: int,
        remaining_budget: int,
        next_scene_intent: str = ""
    ) -> InjectionContext:
        """组装最终的 InjectionContext"""
        style_reference = context.get("style_reference", "")

        # 若未通过裁剪层获得 style_reference，尝试从 StyleDB 渲染 style_injection.j2
        if not style_reference:
            try:
                style_config = await self.style_db.get_style_config()
                if style_config:
                    from jinja2 import Environment, FileSystemLoader
                    env = Environment(
                        loader=FileSystemLoader("writer/prompts"),
                        trim_blocks=True,
                        lstrip_blocks=True
                    )
                    template = env.get_template("style_injection.j2")
                    style_reference = template.render(style_config=style_config)
                else:
                    style_examples = context.get("style_examples", [])
                    style_reference = "\n\n".join(style_examples[:2])
            except Exception:
                style_examples = context.get("style_examples", [])
                style_reference = "\n\n".join(style_examples[:2])

        return InjectionContext(
            scene_plan=context["scene_plan"],
            chapter_goal=context["chapter_goal"],
            previous_text=context["previous_text"],
            present_character_cards=context["character_cards"],
            relevant_world_rules=context.get("world_rules", []),
            active_foreshadowing=context.get("active_foreshadowing", []),
            similar_scenes_reference=context.get("similar_scenes", []),
            style_reference=style_reference,
            next_scene_intent=next_scene_intent,
            total_tokens_used=used_tokens,
            token_budget_remaining=remaining_budget
        )
    
    async def _get_previous_scene_tail(
        self,
        chapter_number: int,
        scene_index: int,
        tail_chars: int = 400
    ) -> str:
        """获取上一场景的末尾文本"""
        if scene_index == 0:
            # 本章第一个场景，获取上一章摘要
            if hasattr(self.story_db, 'get_chapter_summary'):
                return await self.story_db.get_chapter_summary(chapter_number - 1)
            return ""

        # 获取本章之前场景的草稿
        draft = await self.story_db.get_chapter_draft(chapter_number)
        if draft:
            try:
                scenes = draft.get("scenes", [])
                if scene_index > 0 and len(scenes) >= scene_index:
                    prev_scene_text = scenes[scene_index - 1].get("text", "")
                    return prev_scene_text[-tail_chars:] if len(prev_scene_text) > tail_chars else prev_scene_text
            except Exception:
                pass

        return ""

    def _estimate_mandatory_tokens(self, mandatory: dict) -> int:
        """估算强制注入内容的 token 数"""
        total = 0
        
        # 场景意图 + 章节目标
        total += self.BUDGET_ALLOCATION["scene_intent"]
        
        # 上一场景文本
        total += self.BUDGET_ALLOCATION["previous_text"]
        
        # 人物卡
        char_count = len(mandatory.get("character_cards", []))
        max_chars = self.config.INJECTION_MAX_CHARACTERS
        total += min(char_count, max_chars) * self.BUDGET_ALLOCATION["character_card"]
        
        # 伏笔
        fs_count = len(mandatory.get("active_foreshadowing", []))
        max_fs = self.config.INJECTION_MAX_FORESHADOWING
        total += min(fs_count, max_fs) * (self.BUDGET_ALLOCATION["foreshadowing"] // max_fs)
        
        return total
    
    def _estimate_tokens(self, context: dict) -> int:
        """估算总 token 数"""
        # 简化估算：将内容转为 JSON 后按字符数估算
        try:
            text = json.dumps(context, default=str, ensure_ascii=False)
            # 中文在 cl100k_base 下平均每字约 1.5 tokens，保守估算用 1.5
            return int(len(text) * 1.5)
        except Exception:
            return 0
    
    def _format_world_rules(self, rules: list) -> str:
        """格式化世界规则"""
        if not rules:
            return "无"
        formatted = []
        for rule in rules[:5]:
            if isinstance(rule, WorldRule):
                formatted.append(f"- {rule.content}")
            else:
                formatted.append(f"- {rule}")
        return "\n".join(formatted)

    def _format_world_rules_indexed(self, rules: list) -> str:
        """格式化世界规则（带序号索引，供裁剪层使用）"""
        if not rules:
            return "无"
        formatted = []
        for i, rule in enumerate(rules[:10]):
            if isinstance(rule, WorldRule):
                formatted.append(
                    f"{i}: [{rule.importance}] {rule.content} "
                    f"(来源:第{rule.source_chapter}章, 类别:{rule.category})"
                )
            else:
                formatted.append(f"{i}: {rule}")
        return "\n".join(formatted)

    def _format_similar_scenes(self, scenes: list) -> str:
        """格式化相似场景"""
        if not scenes:
            return "无"
        return "\n".join([f"- {scene[:100]}..." for scene in scenes[:3]])
    
    def _format_style_examples(self, examples: list) -> str:
        """格式化文风范例"""
        if not examples:
            return "无"
        return "\n".join([f"- {example[:100]}..." for example in examples[:2]])
