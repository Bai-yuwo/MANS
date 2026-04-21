"""
core/injection_engine.py

注入引擎（Injection Engine）——系统的"注意力机制"。

职责边界：
    - 在 Writer 每次生成文本前，从各个知识库中筛选、组装最相关的上下文信息。
    - 严格控制上下文总 token 数不超过 INJECTION_TOKEN_BUDGET，防止大模型 Lost in the Middle。
    - 采用三层架构（强制层 → 检索层 → 裁剪层），按优先级递减的方式组织信息。

三层架构说明：
    第一层（强制层）：直接从知识库同步读取，零 LLM 调用。
        包含：场景意图、章节目标、前文尾部、出场人物卡、活跃伏笔。
    第二层（检索层）：通过向量语义检索异步获取相关背景知识。
        包含：世界规则、相似历史场景、文风范例。
    第三层（裁剪层）：当总 token 超出预算时，调用 trim 模型智能裁剪。
        策略：保留高优先级内容，压缩或丢弃低优先级内容。

人物卡注入策略（降级注入）：
    - 视角人物（pov_character）：注入完整 CharacterCard（外貌、修为、情绪、声线、目标）。
    - 边缘角色：仅注入 name 和 personality_core，一句话概括性格，大幅节省 token。

典型用法：
    engine = InjectionEngine(project_id="xxx")
    context = await engine.build_context(
        scene_plan=scene_plan,
        chapter_plan=chapter_plan
    )
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
    注入引擎，负责组装 Writer 生成所需的上下文。

    核心设计思想：大模型的上下文窗口有限，不可能把所有历史信息都塞进去。
    InjectionEngine 的作用就是"筛选"——在每次生成前，根据当前场景的需求，
    从海量知识库中挑选出最相关、最必要的信息，组装成一个紧凑的上下文包。

    Token 预算分配策略：
        总预算为 INJECTION_TOKEN_BUDGET（默认 3500）。
        预算被预先分配到各个信息类别，作为估算和裁剪的参考依据：
            - system_rules（800）：系统指令和写作规则（由 writer.j2 模板控制）。
            - scene_intent（200）：当前场景意图 + 本章目标。
            - previous_text（400）：上一场景末尾文本，用于续写衔接。
            - character_card（600）：单个人物卡完整信息量。
            - foreshadowing（300）：激活的伏笔（最多 2 条）。
            - world_rules（400）：通过向量检索获取的相关世界规则。
            - style_examples（300）：文风范例或风格参考。
            - similar_scenes（400）：相似历史场景片段。
            - buffer（400）：预留缓冲，应对估算误差。

    延迟初始化策略：
        所有知识库引用（character_db、bible_db 等）均采用延迟初始化（lazy loading），
        仅在首次访问时创建实例。这避免了在不需要使用某些知识库时浪费资源，
        同时简化了单元测试中的 mock 替换。
    """

    BUDGET_ALLOCATION = {
        "system_rules": 800,
        "scene_intent": 200,
        "previous_text": 400,
        "character_card": 600,
        "foreshadowing": 300,
        "world_rules": 400,
        "style_examples": 300,
        "similar_scenes": 400,
        "buffer": 400,
    }

    def __init__(self, project_id: str):
        self.project_id = project_id
        self.config = get_config()
        self.llm_client = LLMClient()
        self.project_path = Path(self.config.WORKSPACE_PATH) / project_id

        # 知识库实例缓存，通过 property 延迟初始化。
        self._character_db = None
        self._bible_db = None
        self._foreshadowing_db = None
        self._story_db = None
        self._style_db = None
        self._vector_store = None

    @property
    def character_db(self):
        """
        人物知识库（延迟初始化）。

        首次访问时从 knowledge_bases.character_db 导入并创建 CharacterDB 实例。
        后续访问直接返回缓存实例。
        """
        if self._character_db is None:
            from knowledge_bases.character_db import CharacterDB
            self._character_db = CharacterDB(self.project_id)
        return self._character_db

    @property
    def bible_db(self):
        """
        世界观知识库（延迟初始化）。

        存储世界规则、战力体系、地理势力等全局设定。
        遵循"只增不减"原则，确认后的规则不会被修改或删除。
        """
        if self._bible_db is None:
            from knowledge_bases.bible_db import BibleDB
            self._bible_db = BibleDB(self.project_id)
        return self._bible_db

    @property
    def foreshadowing_db(self):
        """
        伏笔知识库（延迟初始化）。

        追踪伏笔的全生命周期状态：planted → hinted → triggered → resolved。
        """
        if self._foreshadowing_db is None:
            from knowledge_bases.foreshadowing_db import ForeshadowingDB
            self._foreshadowing_db = ForeshadowingDB(self.project_id)
        return self._foreshadowing_db

    @property
    def story_db(self):
        """
        故事知识库（延迟初始化）。

        存储大纲、故事弧计划、章节规划、章节摘要和草稿。
        """
        if self._story_db is None:
            from knowledge_bases.story_db import StoryDB
            self._story_db = StoryDB(self.project_id)
        return self._story_db

    @property
    def vector_store(self):
        """
        向量存储（延迟初始化）。

        基于 ChromaDB + bge-m3 本地模型，提供语义检索能力。
        用于检索相关的世界规则、相似场景和文风范例。
        """
        if self._vector_store is None:
            from vector_store.store import VectorStore
            self._vector_store = VectorStore(self.project_id)
        return self._vector_store

    @property
    def style_db(self):
        """
        文风知识库（延迟初始化）。

        存储情感基调、风格参考、示例段落等写作风格相关配置。
        """
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
        构建注入上下文的主入口方法。

        执行流程：
            1. 调用 _get_mandatory_context() 获取强制层信息。
            2. 估算强制层已用 token，计算剩余预算。
            3. 调用 _get_retrieved_context() 在剩余预算内获取检索层信息。
            4. 合并两层结果，估算总 token。
            5. 若超预算，调用 _trim_to_budget() 进行智能裁剪。
            6. 查找下一场景意图作为剧情锚点。
            7. 组装为最终的 InjectionContext 返回。

        Args:
            scene_plan: 当前场景的规划信息（意图、视角、出场人物、情绪基调等）。
            chapter_plan: 当前章节的规划信息（目标、场景序列、情绪走向等）。

        Returns:
            组装完成的 InjectionContext，包含 Writer 生成所需的全部上下文信息。
        """
        mandatory = await self._get_mandatory_context(scene_plan, chapter_plan)

        used_tokens = self._estimate_mandatory_tokens(mandatory)
        remaining_budget = self.config.INJECTION_TOKEN_BUDGET - used_tokens

        retrieved = await self._get_retrieved_context(
            scene_plan,
            chapter_number=chapter_plan.chapter_number,
            budget_tokens=remaining_budget
        )

        context_data = {**mandatory, **retrieved}

        total_tokens = self._estimate_tokens(context_data)
        if total_tokens > self.config.INJECTION_TOKEN_BUDGET:
            context_data = await self._trim_to_budget(
                context_data,
                self.config.INJECTION_TOKEN_BUDGET
            )

        # 查找下一场景意图，作为剧情节奏控制的锚点。
        # Writer 在生成时会被告知"不要越界"，确保本场景为下一场景留有余地。
        next_scene_intent = ""
        if hasattr(chapter_plan, 'scenes') and chapter_plan.scenes:
            current_idx = scene_plan.scene_index
            for s in chapter_plan.scenes:
                if getattr(s, 'scene_index', None) == current_idx + 1:
                    next_scene_intent = s.intent
                    break

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
        第一层：强制层——必须注入的上下文信息。

        此层信息直接从知识库读取，不经过 LLM 处理，延迟极低（通常 < 10ms）。
        包含的信息按优先级排序：
            1. 场景意图：当前场景需要完成什么叙事任务。
            2. 章节目标：本章对主线的推进目标。
            3. 前文尾部：上一场景的最后一段文本，保证续写连贯性。
            4. 出场人物卡：按降级策略注入（主角完整，配角精简）。
            5. 活跃伏笔：需要在当前场景中处理（触发、埋入或提醒）的伏笔。

        Args:
            scene_plan: 当前场景规划。
            chapter_plan: 当前章节规划。

        Returns:
            包含强制层信息的字典，键包括 scene_plan、chapter_goal、previous_text、
            character_cards、active_foreshadowing。
        """
        scene_intent = scene_plan.intent
        chapter_goal = chapter_plan.chapter_goal

        previous_text = await self._get_previous_scene_tail(
            chapter_plan.chapter_number,
            scene_plan.scene_index,
            tail_chars=400
        )

        # 出场人物卡注入（降级策略）。
        # 只有视角人物（POV）获得完整卡片，其余角色仅保留 name 和 personality_core。
        # 这解决了群像场景中人物信息过多导致的 token 浪费和注意力稀释问题。
        character_cards = []
        max_chars = self.config.INJECTION_MAX_CHARACTERS
        pov = getattr(scene_plan, 'pov_character', '')
        for name in scene_plan.present_characters[:max_chars]:
            char = await self.character_db.get_character(name)
            if not char:
                logger.warning(
                    f"人物 '{name}' 不在知识库中，无法注入人物卡。"
                    f"请检查 scene_plan.present_characters 与 CharacterDB 名称是否一致。"
                )
                continue
            if name == pov:
                character_cards.append(char)
            else:
                brief = CharacterCard(
                    name=char.name,
                    personality_core=char.personality_core
                )
                character_cards.append(brief)

        # 活跃伏笔检索。
        # 获取在当前章节范围内需要处理的伏笔，限制数量不超过 INJECTION_MAX_FORESHADOWING。
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
        第二层：检索层——通过向量语义检索获取相关背景知识。

        检索策略：
            构建检索 query 时融合场景意图、出场人物和情绪基调，
            使语义检索结果更贴合当前场景的需求。

        检索目标：
            - bible_rules：世界规则（战力体系、地理、势力等），最多 5 条。
            - chapter_scenes：相似历史场景，最多 3 条，用于文风一致性参考。
            - style_examples：文风范例，最多 2 条。

        容错处理：
            向量检索失败（如 ChromaDB 未就绪）不会阻塞写作流程，
            而是记录错误日志并返回空列表。

        Args:
            scene_plan: 当前场景规划，用于构建检索 query。
            chapter_number: 当前章节号，用于世界规则的 source_chapter 字段。
            budget_tokens: 剩余 token 预算，当前仅用于日志记录，未来可用于动态调整 n_results。

        Returns:
            包含检索结果的字典，键包括 world_rules、similar_scenes、style_examples。
        """
        query = f"{scene_plan.intent} {' '.join(scene_plan.present_characters)} {scene_plan.emotional_tone}"

        results = {}

        if self.config.ENABLE_VECTOR_SEARCH:
            try:
                world_rules_raw = await self.vector_store.search(
                    collection="bible_rules",
                    query=query,
                    n_results=5
                )
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

                similar_scenes_raw = await self.vector_store.search(
                    collection="chapter_scenes",
                    query=query,
                    n_results=3
                )
                results["similar_scenes"] = [r.get("text", "") for r in similar_scenes_raw]

                style_examples_raw = await self.vector_store.search(
                    collection="style_examples",
                    query=scene_plan.emotional_tone,
                    n_results=2
                )
                results["style_examples"] = [r.get("text", "") for r in style_examples_raw]

            except Exception as e:
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
        第三层：裁剪层——当总 token 超出预算时，智能裁剪内容。

        裁剪策略：
            1. 优先尝试调用 trim 模型进行智能裁剪。
               trim 模型接收候选内容列表，返回应保留的索引和压缩后的内容。
            2. 若 trim 模型调用失败（网络异常、JSON 解析失败等），
               回退到硬截断策略：按重要性排序，保留前 N 条。

        元数据保护原则：
            对于 WorldRule，trim 模型只返回序号索引，代码层根据索引保留原始对象，
            绝不丢失 source_chapter、category、importance 等元数据。

        Args:
            context: 合并后的完整上下文数据（强制层 + 检索层）。
            budget_tokens: 目标 token 预算上限。

        Returns:
            裁剪后的上下文数据，确保估算 token 数不超过 budget_tokens。
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

            cleaned = clean_json_response(response_obj.content)
            trimmed = json.loads(cleaned)

            keep_indices = set(trimmed.get("keep_rule_indices", []))
            context["world_rules"] = [
                rules[i] for i in keep_indices
                if isinstance(i, int) and 0 <= i < len(rules)
            ]
            context["similar_scenes"] = trimmed.get("similar_scenes", [])
            context["style_reference"] = trimmed.get("style_reference", "")

        except Exception as e:
            logger.error(f"上下文裁剪失败: {e}，使用硬截断")
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
        """
        将处理后的上下文数据组装为最终的 InjectionContext 对象。

        组装逻辑：
            1. 从 context 中提取 style_reference，若不存在则尝试从 StyleDB 渲染模板。
            2. 若 StyleDB 也无法提供，回退到使用向量检索获取的 style_examples。
            3. 将所有信息填充到 InjectionContext 的对应字段。

        Args:
            context: 经过三层处理后的完整上下文数据。
            used_tokens: 强制层估算已用 token 数。
            remaining_budget: 剩余 token 预算。
            next_scene_intent: 下一场景意图，用于剧情节奏控制。

        Returns:
            完整的 InjectionContext 实例，可直接传入 Writer.render_prompt()。
        """
        style_reference = context.get("style_reference", "")

        if not style_reference:
            try:
                style_config = await self.style_db.get_style_config()
                if style_config:
                    from jinja2 import Environment, FileSystemLoader
                    prompts_dir = Path(__file__).parent.parent / "writer" / "prompts"
                    env = Environment(
                        loader=FileSystemLoader(str(prompts_dir)),
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
        """
        获取上一场景的末尾文本，用于续写衔接。

        处理逻辑：
            - 若当前是本章第一个场景（scene_index == 0），
              返回上一章的摘要（如果存在），帮助读者回忆前文。
            - 若当前不是第一个场景，返回上一场景文本的最后 tail_chars 个字符。
            - 若草稿不存在或解析失败，返回空字符串。

        Args:
            chapter_number: 当前章节号。
            scene_index: 当前场景在本章内的序号。
            tail_chars: 需要获取的尾部字符数，默认 400。

        Returns:
            上一场景的尾部文本，或上一章摘要，或空字符串。
        """
        if scene_index == 0:
            if hasattr(self.story_db, 'get_chapter_summary'):
                return await self.story_db.get_chapter_summary(chapter_number - 1)
            return ""

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
        """
        估算强制层内容的 token 消耗。

        估算方法：
            基于 BUDGET_ALLOCATION 中预定义的各类别预算值，
            按实际数量加权求和。此方法仅用于粗略估算，
            精确 token 数需通过 tiktoken 计算。

        Args:
            mandatory: _get_mandatory_context() 返回的强制层数据字典。

        Returns:
            估算的 token 数量。
        """
        total = 0
        total += self.BUDGET_ALLOCATION["scene_intent"]
        total += self.BUDGET_ALLOCATION["previous_text"]

        char_count = len(mandatory.get("character_cards", []))
        max_chars = self.config.INJECTION_MAX_CHARACTERS
        total += min(char_count, max_chars) * self.BUDGET_ALLOCATION["character_card"]

        fs_count = len(mandatory.get("active_foreshadowing", []))
        max_fs = self.config.INJECTION_MAX_FORESHADOWING
        total += min(fs_count, max_fs) * (self.BUDGET_ALLOCATION["foreshadowing"] // max_fs)

        return total

    def _estimate_tokens(self, context: dict) -> int:
        """
        估算上下文数据的总 token 数。

        采用简化估算策略：
            将数据序列化为 JSON 字符串后，按字符数乘以经验系数（1.5）估算。
            中文在 cl100k_base 下平均每字符约 1.5 tokens，此估算偏保守，
            确保实际 token 数不会明显超过估算值。

        Args:
            context: 需要估算的上下文数据字典。

        Returns:
            估算的 token 数量，序列化失败时返回 0。
        """
        try:
            text = json.dumps(context, default=str, ensure_ascii=False)
            return int(len(text) * 1.5)
        except Exception:
            return 0

    def _format_world_rules(self, rules: list) -> str:
        """
        将世界规则列表格式化为人类可读的文本。

        用于向 trim 模型或日志展示规则内容。
        最多展示前 5 条，避免输出过长。

        Args:
            rules: WorldRule 对象列表。

        Returns:
            格式化后的字符串，每条规则占一行。
        """
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
        """
        将世界规则格式化为带序号的文本，供裁剪层使用。

        每条规则包含序号、重要性级别、内容、来源章节和类别，
        使 trim 模型能够基于完整信息做出保留/丢弃决策。
        最多展示前 10 条。

        Args:
            rules: WorldRule 对象列表。

        Returns:
            带序号的格式化字符串。
        """
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
        """
        将相似场景列表格式化为摘要文本。

        每条场景截取前 100 个字符并附加省略号，最多展示 3 条。
        用于 trim 模型的决策输入。

        Args:
            scenes: 场景文本字符串列表。

        Returns:
            格式化后的摘要字符串。
        """
        if not scenes:
            return "无"
        return "\n".join([f"- {scene[:100]}..." for scene in scenes[:3]])

    def _format_style_examples(self, examples: list) -> str:
        """
        将文风范例列表格式化为摘要文本。

        每条范例截取前 100 个字符，最多展示 2 条。
        用于 trim 模型的决策输入。

        Args:
            examples: 文风范例文本字符串列表。

        Returns:
            格式化后的摘要字符串。
        """
        if not examples:
            return "无"
        return "\n".join([f"- {example[:100]}..." for example in examples[:2]])
