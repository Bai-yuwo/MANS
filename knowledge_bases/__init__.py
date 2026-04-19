"""
knowledge_bases/__init__.py

知识库模块。

所有具体知识库均继承自 base_db.BaseDB，获得异步文件读写、原子写入和并发安全能力。

子模块：
    - base_db: 知识库基类，提供通用的 JSON 持久化操作。
    - bible_db: 世界观知识库，管理世界规则和设定。
    - character_db: 人物知识库，管理人物卡和关系网络。
    - story_db: 故事知识库，管理大纲、弧线、章节规划和草稿。
    - foreshadowing_db: 伏笔知识库，追踪伏笔的全生命周期。
    - style_db: 文风知识库，管理风格配置和范例。

存储约定：
    每个知识库在 workspace/{project_id}/ 下拥有独立的子目录，
    数据以 JSON 文件形式存储，由 BaseDB 统一管理。
"""
