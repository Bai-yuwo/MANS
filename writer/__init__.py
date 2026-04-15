"""
writer/__init__.py
写作核心包

Writer 是系统的唯一正文生成器，职责：
1. 接收 Injection Engine 组装好的上下文
2. 渲染 Jinja2 提示词模板
3. 调用主力大模型，流式输出文本
4. 触发 Update Extractor 的异步更新

使用示例：
    from writer import Writer
    
    writer = Writer(project_id="xxx")
    text = await writer.write_scene(
        scene_plan=scene_plan,
        chapter_plan=chapter_plan,
        stream_callback=send_to_frontend
    )
"""

from writer.writer import Writer

__all__ = ["Writer"]
