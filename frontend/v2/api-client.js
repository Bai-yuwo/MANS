/**
 * api-client.js — MANS v2 API / SSE 客户端
 *
 * 封装 /api/v2 所有端点:
 *   - REST: createProject / listProjects / getProject / deleteProject / getStatus
 *   - REST: startRun / approve
 *   - SSE:  connectStream
 *
 * 使用:
 *   const client = new MANSApiClient();
 *   const proj = await client.createProject({ name: "...", genre: "玄幻" });
 *   await client.startRun(proj.project_id, "开始构建世界观");
 *   const source = client.connectStream(proj.project_id, (event) => {
 *       console.log(event.type, event.data);
 *   });
 */

class MANSApiClient {
    constructor(baseUrl = "") {
        this.baseUrl = baseUrl;
    }

    _url(path) {
        return `${this.baseUrl}${path}`;
    }

    async _json(res) {
        if (!res.ok) {
            const err = await res.text();
            throw new Error(`HTTP ${res.status}: ${err}`);
        }
        return res.json();
    }

    // --------------------------------------------------------
    // 项目管理
    // --------------------------------------------------------
    async createProject(data) {
        const res = await fetch(this._url("/api/v2/projects"), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(data),
        });
        return this._json(res);
    }

    async listProjects() {
        const res = await fetch(this._url("/api/v2/projects"));
        return this._json(res);
    }

    async getProject(projectId) {
        const res = await fetch(this._url(`/api/v2/projects/${projectId}`));
        return this._json(res);
    }

    async deleteProject(projectId) {
        const res = await fetch(this._url(`/api/v2/projects/${projectId}`), {
            method: "DELETE",
        });
        return this._json(res);
    }

    async getStatus(projectId) {
        const res = await fetch(this._url(`/api/v2/projects/${projectId}/status`));
        return this._json(res);
    }

    async getOverview(projectId) {
        const res = await fetch(this._url(`/api/v2/projects/${projectId}/overview`));
        return this._json(res);
    }

    // --------------------------------------------------------
    // Orchestrator 运行
    // --------------------------------------------------------
    async startRun(projectId, userPrompt) {
        const res = await fetch(this._url(`/api/v2/projects/${projectId}/run`), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ user_prompt: userPrompt }),
        });
        return this._json(res);
    }

    async approve(projectId, reply) {
        const res = await fetch(this._url(`/api/v2/projects/${projectId}/respond`), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ reply }),
        });
        return this._json(res);
    }

    async sendCommand(projectId, instruction) {
        const res = await fetch(this._url(`/api/v2/projects/${projectId}/command`), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ instruction }),
        });
        return this._json(res);
    }

    // --------------------------------------------------------
    // SSE 流式
    // --------------------------------------------------------
    connectStream(projectId, onEvent) {
        const url = this._url(`/api/v2/projects/${projectId}/stream`);
        const source = new EventSource(url);

        source.onopen = () => {
            console.log("[SSE] 连接已建立", projectId);
        };

        source.addEventListener("reasoning", (e) => {
            onEvent({ type: "reasoning", data: JSON.parse(e.data) });
        });

        source.addEventListener("output", (e) => {
            onEvent({ type: "output", data: JSON.parse(e.data) });
        });

        source.addEventListener("completed", (e) => {
            onEvent({ type: "completed", data: JSON.parse(e.data) });
        });

        source.addEventListener("confirm", (e) => {
            onEvent({ type: "confirm", data: JSON.parse(e.data) });
        });

        source.addEventListener("ask_user", (e) => {
            onEvent({ type: "ask_user", data: JSON.parse(e.data) });
        });

        source.addEventListener("error", (e) => {
            let data;
            try { data = JSON.parse(e.data); } catch { data = { error: e.data }; }
            onEvent({ type: "error", data });
        });

        source.addEventListener("done", (e) => {
            onEvent({ type: "done", data: JSON.parse(e.data) });
            source.close();
        });

        source.onerror = (e) => {
            console.error("[SSE] 连接错误", e);
            onEvent({ type: "sse_error", data: { message: "SSE 连接中断" } });
            // 让调用方决定是否重连
        };

        return source;
    }
}
