/**
 * app.js — MANS v2 前端主应用
 *
 * 职责:
 *   - 监听 project-panel 的 project-selected 事件
 *   - 将选中项目同步到 stage-workbench,并查询最新状态
 *   - 若后台有活跃会话,自动重连 SSE
 *   - 监听 stage-workbench 的 start-stage 事件,调用 API 启动 Director
 *   - 监听 agent-stream 的 stage-confirm 事件,弹出 confirm-dialog
 *   - 监听 confirm-dialog 的 confirm-responded 事件,重新连接 SSE
 */

document.addEventListener("DOMContentLoaded", () => {
    const projectPanel = document.querySelector("#project-panel");
    const stageWorkbench = document.querySelector("#stage-workbench");
    const agentStream = document.querySelector("#agent-stream");
    const confirmDialog = document.querySelector("#confirm-dialog");

    const client = new MANSApiClient();
    let currentProjectId = null;

    // --------------------------------------------------------
    // 项目切换 — 同步工作台 + 查询最新状态 + 自动重连 SSE
    // --------------------------------------------------------
    projectPanel.addEventListener("project-selected", async (e) => {
        const { projectId, project } = e.detail;
        currentProjectId = projectId;

        if (!projectId) {
            // 项目被删除或取消选择
            localStorage.removeItem("mans:lastProjectId");
            stageWorkbench.setProject(null, null);
            return;
        }

        localStorage.setItem("mans:lastProjectId", projectId);
        stageWorkbench.setProject(projectId, project);
        console.log("选中项目:", projectId, project?.name);

        // 查询最新状态(可能后台正在运行或等待确认)
        try {
            const status = await client.getStatus(projectId);
            const overview = await client.getOverview(projectId);
            stageWorkbench.updateStatus({ ...status, ...overview });

            if (status.session_active) {
                if (status.waiting_confirm && status.confirm_payload) {
                    // 后台正在等待用户确认,弹出 confirm 弹窗
                    confirmDialog.show(projectId, status.confirm_payload);
                } else if (status.pump_running) {
                    // 后台 pump 正在运行,自动重连 SSE(不清空,可能已有内容)
                    agentStream.start(projectId, { clear: false });
                }
                // pump_running=false && waiting_confirm=false:
                // pump 已结束(完成/异常/max_turns),不做自动操作,
                // 让 stage-workbench 显示"继续 X 阶段"按钮
            }
        } catch (err) {
            console.error("查询项目状态失败", err);
        }
    });

    // --------------------------------------------------------
    // 启动阶段 / 断点续接
    // --------------------------------------------------------
    stageWorkbench.addEventListener("start-stage", async (e) => {
        const { projectId, userPrompt, stage, isResume } = e.detail;
        if (!projectId) return;

        try {
            if (isResume) {
                // 断点续接：用 /command 发送指令，Director 会检查现有 KB 并补充
                await client.sendCommand(projectId, userPrompt);
            } else {
                // 全新启动
                await client.startRun(projectId, userPrompt);
            }
            agentStream.start(projectId);
            // 立即禁用按钮,显示"运行中..."
            stageWorkbench.updateStatus({
                stage,
                current_chapter: stageWorkbench.project?.current_chapter || 0,
                session_active: true,
                pump_running: true,
            });
        } catch (err) {
            alert("启动失败: " + err.message);
        }
    });

    // --------------------------------------------------------
    // 用户发送指令后自动重连 SSE
    // --------------------------------------------------------
    stageWorkbench.addEventListener("instruction-sent", async (e) => {
        const { projectId } = e.detail;
        if (!projectId) return;
        agentStream.start(projectId, { clear: false });
        // 刷新状态
        try {
            const status = await client.getStatus(projectId);
            const overview = await client.getOverview(projectId);
            stageWorkbench.updateStatus({ ...status, ...overview });
        } catch (err) {
            console.error("刷新状态失败", err);
        }
    });

    // --------------------------------------------------------
    // 阶段确认弹窗
    // --------------------------------------------------------
    agentStream.addEventListener("stage-confirm", (e) => {
        const data = e.detail;
        confirmDialog.show(currentProjectId, data);
    });

    // --------------------------------------------------------
    // 流自然结束(pump 完成/异常) -> 刷新状态恢复按钮
    // --------------------------------------------------------
    agentStream.addEventListener("stream-ended", async (e) => {
        const { projectId, reason } = e.detail;
        console.log("[SSE] 流结束,原因:", reason);
        // 查询最新状态,pump_running 应该已变为 false
        try {
            const status = await client.getStatus(projectId);
            const overview = await client.getOverview(projectId);
            stageWorkbench.updateStatus({ ...status, ...overview });
        } catch (err) {
            console.error("流结束后刷新状态失败", err);
        }
    });

    // --------------------------------------------------------
    // 用户确认后续接
    // --------------------------------------------------------
    confirmDialog.addEventListener("confirm-responded", async (e) => {
        const { projectId } = e.detail;
        // 重新连接 SSE 流(不清空之前内容)
        setTimeout(() => {
            agentStream.start(projectId, { clear: false });
        }, 300);

        // 刷新项目状态,更新工作台 stage 显示
        try {
            const status = await client.getStatus(projectId);
            stageWorkbench.updateStatus(status);
        } catch (err) {
            console.error("刷新项目状态失败", err);
        }
    });

    console.log("MANS v2 前端已加载");
});
