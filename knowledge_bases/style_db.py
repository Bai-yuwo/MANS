"""
knowledge_bases/style_db.py

文风知识库，存储和管理与写作风格相关的配置与范例。

职责边界：
    - 按情绪基调分类存储文风范例，便于 Injection Engine 根据当前场景情绪检索参考。
    - 管理全局文风配置（如句式偏好、修辞风格、节奏控制等）。
    - 所有操作继承 BaseDB 的异步文件读写能力。

数据结构：
    - tone_{emotion}.json：按情绪分类的范例集合，每项包含 text（范例文本）和 tone（情绪标签）。
    - config.json：全局文风配置，由用户在项目初始化时设定或后续调整。

典型用法：
    db = StyleDB(project_id="xxx")
    examples = await db.get_examples_by_tone("热血", limit=3)
    config = await db.get_style_config()
"""

from knowledge_bases.base_db import BaseDB


class StyleDB(BaseDB):
    """
    文风知识库。

    文风是影响小说质感的核心要素。StyleDB 不仅存储情绪基调对应的范例文本，
    还保存全局文风配置（如句式长短偏好、修辞密度、对话风格等），
    供 Injection Engine 在组装上下文时注入到 Writer 的提示词中。

    存储结构：
        workspace/{project_id}/style/
        ├── tone_热血.json      # 情绪分类范例
        ├── tone_压抑.json
        ├── config.json         # 全局文风配置
        └── ...

    延迟加载：
        BaseDB 的所有读写方法均为异步，文件在首次访问时自动创建。
    """

    def __init__(self, project_id: str):
        super().__init__(project_id, "style")

    async def get_examples_by_tone(self, tone: str, limit: int = 3, scene_type: str = "") -> list[dict]:
        """
        根据情绪基调获取参考范文片段，支持按场景类型过滤。

        检索逻辑：
            从 tone_{tone}.json 中读取 examples 数组。
            若指定 scene_type，只返回 scene_types 包含该标签的范例。
            若该情绪尚未有任何范例，返回空列表。

        Args:
            tone: 情绪基调名称（如"热血"、"压抑"、"温情"、"悬疑"）。
            limit: 最多返回的范例数量，默认 3 条。
            scene_type: 场景类型过滤（如"fight""dialogue""environment"）。为空时不过滤。

        Returns:
            范文字典列表（含 text / tone / scene_types），按存入顺序排列。
        """
        data = await self.load(f"tone_{tone}") or {}
        examples = data.get("examples", [])

        if scene_type:
            filtered = []
            for ex in examples:
                st = ex.get("scene_types", []) if isinstance(ex, dict) else []
                if scene_type in st:
                    filtered.append(ex)
            examples = filtered

        return examples[:limit]

    async def add_example(self, tone: str, example: str) -> bool:
        """
        向指定情绪分类添加文风范例。

        使用 BaseDB.append() 方法实现只追加写入，避免覆盖已有范例。
        范例文本应尽量保持原汁原味，不要添加额外解释或 markdown 标记。

        Args:
            tone: 情绪基调名称。
            example: 范文片段文本（通常为 100-300 字的代表性段落）。

        Returns:
            是否添加成功。
        """
        return await self.append(f"tone_{tone}", {
            "text": example,
            "tone": tone
        })

    async def get_style_config(self) -> dict:
        """
        获取全局文风配置。

        配置内容通常包括：
            - sentence_style：句式偏好（长短句搭配、整散句比例）。
            - rhetoric_density：修辞密度（比喻、排比、拟人等的使用频率）。
            - dialogue_style：对话风格（口语化、文言化、简洁化等）。
            - pacing_preference：节奏偏好（紧凑/舒缓/张弛有度）。
            - sensory_emphasis：感官侧重（视觉/听觉/触觉/嗅觉的描写倾向）。

        Returns:
            文风配置字典，若未配置则返回空字典。
        """
        return await self.load("config") or {}

    async def save_style_config(self, config: dict) -> bool:
        """
        保存全局文风配置。

        Args:
            config: 文风配置字典，结构由前端或 Generator 决定。

        Returns:
            是否保存成功。
        """
        return await self.save("config", config)
