"""
api/

MANS 17-Agent 架构的新 API 层(P3)。

与旧 API(frontend/web_app.py)的区别:
    - 旧 API 面向单 Writer + InjectionEngine + UpdateExtractor 的"注入式管线"
    - 新 API 面向 Orchestrator + Director + 5 主管 + 12 专家的二级协作架构
    - 旧 API 的 SSE 事件是 start/progress/token/complete/error/done
    - 新 API 的 SSE 事件是 reasoning/output/completed/error/confirm(直接映射 StreamPacket)

迁移策略:
    新旧 API 共存,旧前端继续用 /api/... 旧路由,新前端用 /api/v2/... 路由。
    P5 集成清理阶段再逐步废弃旧路由。
"""
