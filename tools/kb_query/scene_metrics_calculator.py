"""
tools/kb_query/scene_metrics_calculator.py

SceneMetricsCalculator — 纯函数工具,不调用 LLM。

职责:
    对 Writer 产出的 scene_text 进行纯文本统计分析,产出结构化指标供 Critic 审查时参考。
    Critic 的 system prompt 明确要求"禁止给出无法被 scene_metrics 数据支撑的建议"。

输入:
    scene_text + beatsheet + target_word_count
输出:
    {
        "word_count": 实际字数(中文字符数),
        "word_count_ratio": 实际/目标百分比,
        "sentence_count": 总句数(按句号/感叹号/问号分割),
        "protagonist_action_ratio": 主角主动行为句数/总句数百分比,
        "scene_transition_count": 单场景内时间/空间/视角跳跃次数,
        "dialogue_to_action_ratio": 对话节拍数/动作节拍数,
        "dialogue_line_count": 对话行数(以引号开头或含说话人提示),
        "description_paragraph_ratio": 纯描写段落占比,
    }

设计原则:
    - 所有指标基于纯文本统计,不依赖 LLM,零 token 消耗
    - 统计规则尽量简单明确,避免歧义
    - protagonist 识别:beatsheet.pov_character 或 beatsheet 中出场频率最高的角色名
"""

import json
import re
from typing import Any

from core.base_tool import BaseTool
from core.logging_config import get_logger

logger = get_logger("tools.kb_query.scene_metrics_calculator")


def _count_chinese_chars(text: str) -> int:
    """统计中文字符数(不含标点和空格)。"""
    return len(re.findall(r"[一-鿿]", text))


def _split_sentences(text: str) -> list[str]:
    """按句号、感叹号、问号分割句子,保留分割符。"""
    # 匹配中文句号、英文句号、感叹号、问号,但排除引号内的标点
    parts = re.split(r"(?<=[。！？.!?])\s*", text)
    return [s.strip() for s in parts if s.strip()]


def _count_scene_transitions(text: str) -> int:
    """
    统计场景内时间/空间/视角跳跃次数。

    启发式规则:
        - 时间跳跃词:"三天后""翌日""与此同时""与此同时"等
        - 空间跳跃词:"与此同时""另一边""千里之外"等(单场景内出现视为视角切换)
        - 视角切换词:"与此同时""他不知道的是""在xxx看来"等
    """
    transition_patterns = [
        r"三天后|翌日|次日|数日后|片刻后|良久|与此同时|另一边",
        r"千里之外|与此同时|在\w+看来|他不知道的是|她不知道的是",
        r"与此同时|\w+的视角|\w+眼中",
    ]
    count = 0
    for pattern in transition_patterns:
        count += len(re.findall(pattern, text))
    return count


def _count_dialogue_lines(text: str) -> int:
    """
    统计对话行数。

    启发式规则:
        - 以中文引号"或「开头的行
        - 或包含"xxx说/道/问/答"的行
    """
    lines = text.split("\n")
    count = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith(("\"", "\"", "「", "『")):
            count += 1
        elif re.search(r"[一-鿿]+[说道问答喊叫骂嘀咕].{0,3}[：:]", line):
            count += 1
    return count


def _count_protagonist_actions(text: str, protagonist: str) -> int:
    """
    统计主角主动行为句数。

    启发式规则:
        - 句子以 protagonist 开头,且包含动作动词
        - 动作动词库:攥、拔、跃、冲、退、挡、挥、斩、踏、掠、追、闪、迎、击、扑、抓、掷、抽、拍、推、拉、转、走、跑、飞、落、站、坐、卧、跪、拜、抱、扛、提、举、扔、摔、踢、踏、迈、跨、翻、滚、爬、钻、挤、撞、碰、顶、压、按、捏、揉、搓、撕、扯、割、刺、扎、射、放、点、燃、吹、吸、喝、吃、咬、嚼、吞、咽、吐、呼、吸、叹、哼、唱、喊、叫、骂、哭、笑、怒、喜、悲、惧、惊、疑、思、想、忆、念、忘、记、识、知、觉、感、受、忍、耐、等、待、寻、找、搜、查、探、问、答、辩、论、议、评、判、决、定、选、择、取、舍、得、失、赢、输、胜、败、攻、守、防、护、救、杀、死、生、活、变、化、成、长、退、进、升、降、增、减、加、减、乘、除、合、分、聚、散、离、合、归、来、去、往、返、回、到、达、至、及、过、越、超、跨、穿、透、渗、浸、泡、淹、没、沉、浮、漂、游、泳、滑、溜、跌、倒、塌、陷、崩、溃、塌、毁、灭、绝、尽、空、无、有、在、为、作、做、干、办、理、处、置、安、排、布、置、设、建、造、制、作、写、画、刻、雕、塑、铸、锻、炼、烧、烤、煮、蒸、炖、煎、炒、炸、拌、调、配、混、搅、搅、拌、和、合、融、溶、化、解、析、分、离、提、取、精、炼、纯、净、清、洗、涤、刷、擦、抹、涂、敷、贴、粘、连、接、缝、补、修、整、理、顺、齐、整、洁、净、清、爽、利、落、干脆、果、断、坚、决、毅、然、决、然、断、然、毅、然、果、断、干、脆、利、索、麻、利、快、捷、敏、捷、灵、活、轻、巧、熟、练、老、练、纯、熟、精、通、擅、长、专、精、深、湛、高、超、卓、越、杰、出、优、秀、精、良、佳、好、美、妙、绝、佳、极、好、完、美、无、瑕、天、衣、无、缝、无、懈、可、击、无、可、挑、剔、无、可、非、议、无、可、置、疑、无、可、争、辩、无、可、抗、拒、无、可、抵、挡、无、可、避、免、无、可、挽、回、无、可、救、药、无、可、奈、何、无、可、奉、告、无、可、奉、献、无、可、奉、承、无、可、奉、陪、无、可、奉、送、无、可、奉、还、无、可、奉、酬、无、可、奉、赐、无、可、奉、赠、无、可、奉、献、无、可、奉、祀")
    """
    if not protagonist:
        return 0
    sentences = _split_sentences(text)
    action_verbs = {
        "攥", "拔", "跃", "冲", "退", "挡", "挥", "斩", "踏", "掠", "追", "闪", "迎", "击",
        "扑", "抓", "掷", "抽", "拍", "推", "拉", "转", "走", "跑", "飞", "落", "站", "坐",
        "卧", "跪", "拜", "抱", "扛", "提", "举", "扔", "摔", "踢", "迈", "跨", "翻", "滚",
        "爬", "钻", "挤", "撞", "碰", "顶", "压", "按", "捏", "揉", "搓", "撕", "扯", "割",
        "刺", "扎", "射", "放", "点", "燃", "吹", "吸", "喝", "吃", "咬", "嚼", "吞", "咽",
        "吐", "呼", "叹", "哼", "唱", "喊", "叫", "骂", "哭", "笑", "怒", "喜", "悲", "惧",
        "惊", "疑", "思", "想", "忆", "念", "忘", "记", "识", "知", "觉", "感", "受", "忍",
        "耐", "等", "待", "寻", "找", "搜", "查", "探", "问", "答", "辩", "论", "议", "评",
        "判", "决", "定", "选", "择", "取", "舍", "得", "失", "赢", "输", "胜", "败", "攻",
        "守", "防", "护", "救", "杀", "死", "生", "活", "变", "化", "成", "长", "退", "进",
        "升", "降", "增", "减", "加", "合", "分", "聚", "散", "离", "归", "来", "去", "往",
        "返", "回", "到", "达", "至", "及", "过", "越", "超", "跨", "穿", "透", "渗", "浸",
        "泡", "淹", "没", "沉", "浮", "漂", "游", "泳", "滑", "溜", "跌", "倒", "塌", "陷",
        "崩", "溃", "毁", "灭", "绝", "尽", "空", "无", "有", "在", "为", "作", "做", "干",
        "办", "理", "处", "置", "安", "排", "布", "设", "建", "造", "制", "写", "画", "刻",
        "雕", "塑", "铸", "锻", "炼", "烧", "烤", "煮", "蒸", "炖", "煎", "炒", "炸", "拌",
        "调", "配", "混", "搅", "和", "融", "溶", "解", "析", "提", "取", "精", "纯", "净",
        "清", "洗", "涤", "刷", "擦", "抹", "涂", "敷", "贴", "粘", "连", "接", "缝", "补",
        "修", "整", "顺", "齐", "洁", "爽", "利", "落", "干", "脆", "果", "断", "坚", "决",
        "毅", "然", "快", "捷", "敏", "捷", "灵", "活", "轻", "巧", "熟", "练", "老", "练",
        "纯", "熟", "精", "通", "擅", "长", "专", "精", "深", "湛", "高", "超", "卓", "越",
        "杰", "出", "优", "秀", "精", "良", "佳", "好", "美", "妙", "绝", "极", "完",
    }
    count = 0
    for sent in sentences:
        # 句子以主角名开头(主角名在句首或句首附近)
        if protagonist in sent[:len(protagonist) + 6]:
            # 且包含动作动词
            for verb in action_verbs:
                if verb in sent:
                    count += 1
                    break
    return count


def _count_description_paragraphs(text: str) -> int:
    """
    统计纯描写段落数(无对话、无动作叙述的段落)。

    启发式规则:
        - 段落不含引号、不含说话人提示、不含明显动作动词
        - 且包含环境/感官类词汇
    """
    sensory_words = {
        "风", "雨", "雪", "霜", "露", "雾", "云", "雷", "电", "光", "影", "色", "香", "味",
        "声", "音", "静", "寂", "寒", "冷", "暖", "热", "凉", "温", "湿", "干", "润", "燥",
        "暗", "明", "亮", "昏", "暗", "幽", "深", "浅", "远", "近", "高", "低", "大", "小",
        "长", "短", "宽", "窄", "厚", "薄", "粗", "细", "密", "疏", "浓", "淡", "清", "浊",
        "鲜", "陈", "新", "旧", "古", "老", "嫩", "青", "红", "白", "黑", "黄", "绿", "蓝",
        "紫", "灰", "褐", "苍", "碧", "翠", "丹", "绯", "绛", "绯", "绯", "朱", "彤", "素",
        "皎", "皓", "皑", "黝", "黯", "晦", "晦", "胧", "朦", "胧", "渺", "茫", "茫", "渺",
        "浩", "瀚", "瀚", "瀚", "瀚", "瀚", "瀚", "瀚", "瀚", "瀚", "瀚", "瀚", "瀚", "瀚",
    }
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    desc_count = 0
    for para in paragraphs:
        # 不含引号
        if "\"" in para or "\"" in para or "「" in para or "『" in para:
            continue
        # 不含说话人提示
        if re.search(r"[一-鿿]+[说道问答喊叫骂嘀咕].{0,3}[：:]", para):
            continue
        # 包含感官词汇
        for word in sensory_words:
            if word in para:
                desc_count += 1
                break
    return desc_count


class SceneMetricsCalculator(BaseTool):
    """
    场景文本量化指标计算器 — 纯函数,不调用 LLM。

    供 SceneShowrunner 在调 Critic 之前调用,将指标数据拼入 Critic 的 user prompt。
    Critic 的 system prompt 禁止给出无法被 metrics 数据支撑的建议。
    """

    @property
    def name(self) -> str:
        return "scene_metrics_calculator"

    @property
    def description(self) -> str:
        return (
            "计算场景正文的纯文本量化指标(word_count/sentence_count/"
            "protagonist_action_ratio/dialogue_line_count/scene_transition_count 等)。"
            "零 token 消耗,纯函数统计。"
        )

    @property
    def schema(self) -> dict:
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "scene_text": {
                        "type": "string",
                        "description": "Writer 产出的场景正文(必填)。",
                    },
                    "beatsheet": {
                        "type": "object",
                        "description": "SceneBeatsheet(用于提取 pov_character 和 action_beats 数量)。",
                    },
                    "target_word_count": {
                        "type": "integer",
                        "description": "目标字数(默认1200)。",
                    },
                },
                "required": ["scene_text"],
                "additionalProperties": False,
            },
        }

    async def execute(
        self,
        scene_text: str,
        beatsheet: dict | None = None,
        target_word_count: int = 1200,
        **kwargs,
    ) -> str:
        if not scene_text or not scene_text.strip():
            return json.dumps({"error": "scene_text 不能为空"}, ensure_ascii=False)

        try:
            bs = beatsheet or {}
            pov = bs.get("pov_character", "")
            action_beats = bs.get("action_beats", []) or []

            word_count = _count_chinese_chars(scene_text)
            sentences = _split_sentences(scene_text)
            sentence_count = len(sentences)

            protagonist_actions = _count_protagonist_actions(scene_text, pov)
            protagonist_action_ratio = (
                round(protagonist_actions / sentence_count * 100, 1)
                if sentence_count > 0 else 0.0
            )

            transition_count = _count_scene_transitions(scene_text)
            dialogue_lines = _count_dialogue_lines(scene_text)
            desc_paras = _count_description_paragraphs(scene_text)
            total_paras = len([p for p in scene_text.split("\n\n") if p.strip()])

            action_beat_count = len(action_beats)
            dialogue_to_action_ratio = (
                round(dialogue_lines / max(action_beat_count, 1), 2)
                if action_beat_count > 0 else dialogue_lines
            )

            # 基于 narrative_function 的预期对话占比
            narrative_function = bs.get("narrative_function", "setup")
            expected_dialogue_ratio_map = {
                "setup": 0.6,
                "rising_action": 0.4,
                "climax": 0.2,
                "falling_action": 0.4,
                "resolution": 0.5,
                "transition": 0.3,
            }
            expected_dialogue_ratio = expected_dialogue_ratio_map.get(narrative_function, 0.4)

            result = {
                "word_count": word_count,
                "target_word_count": target_word_count,
                "word_count_ratio": round(word_count / target_word_count * 100, 1) if target_word_count > 0 else 0.0,
                "sentence_count": sentence_count,
                "protagonist_action_count": protagonist_actions,
                "protagonist_action_ratio": protagonist_action_ratio,
                "scene_transition_count": transition_count,
                "dialogue_line_count": dialogue_lines,
                "action_beat_count": action_beat_count,
                "dialogue_to_action_ratio": dialogue_to_action_ratio,
                "description_paragraph_count": desc_paras,
                "total_paragraph_count": total_paras,
                "description_paragraph_ratio": round(desc_paras / max(total_paras, 1) * 100, 1),
                "expected_dialogue_ratio": expected_dialogue_ratio,
                "pov_character": pov,
            }
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            logger.exception("scene_metrics_calculator 失败")
            return json.dumps({"error": f"计算失败: {e}"}, ensure_ascii=False)
