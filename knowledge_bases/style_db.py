"""
knowledge_bases/style_db.py
文风知识库

设计原则：
1. 情感分类：按情绪基调分类存储范文
2. 参考片段：存储典型场景的参考文本
3. 动态检索：根据当前情绪基调检索相似范文
"""

from knowledge_bases.base_db import BaseDB


class StyleDB(BaseDB):
    """
    文风知识库
    
    存储情感基调、参考范文、文风关键词
    
    使用示例：
        db = StyleDB(project_id="xxx")
        examples = db.get_examples_by_tone("热血")
    """
    
    def __init__(self, project_id: str):
        super().__init__(project_id, "style")
    
    def get_examples_by_tone(self, tone: str, limit: int = 3) -> list[str]:
        """
        根据情绪基调获取参考范文
        
        Args:
            tone: 情绪基调（如"热血"/"压抑"/"温情"）
            limit: 返回数量限制
        
        Returns:
            范文片段列表
        """
        data = self.load(f"tone_{tone}") or {}
        examples = data.get("examples", [])
        return examples[:limit]
    
    def add_example(self, tone: str, example: str) -> bool:
        """
        添加文风范例
        
        Args:
            tone: 情绪基调
            example: 范文片段
        
        Returns:
            是否添加成功
        """
        return self.append(f"tone_{tone}", {
            "text": example,
            "tone": tone
        })
    
    def get_style_config(self) -> dict:
        """
        获取文风配置
        
        Returns:
            文风配置字典
        """
        return self.load("config") or {}
    
    def save_style_config(self, config: dict) -> bool:
        """
        保存文风配置
        
        Args:
            config: 文风配置
        
        Returns:
            是否保存成功
        """
        return self.save("config", config)
