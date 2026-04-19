"""
main.py

MANS 系统入口文件。

职责边界：
    - 提供开发环境下的热重载（Hot Reload）启动方式。
    - 直接调用 uvicorn 运行 FastAPI 应用（frontend.web_app:app）。
    - 监听本地回环地址（127.0.0.1），端口 666，便于开发调试。

生产环境部署：
    生产环境建议使用以下命令启动，以获得更好的性能和稳定性：
        uvicorn frontend.web_app:app --host 0.0.0.0 --port 666 --workers 2

用法：
    python main.py
"""

import os
import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "frontend.web_app:app",
        host="127.0.0.1",
        port=666,
        reload=True
    )
