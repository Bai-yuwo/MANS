/**
 * agent-stream.js — 右栏 Agent 实时流 Web Component
 *
 * 布局: 上下两栏固定框
 *   - 上方 #reasoning-box : 思考内容(追加文字,自动滚动)
 *   - 下方 #output-box    : 正文内容(追加文字,自动滚动)
 *
 * 不每个 chunk 新建 div,直接往对应 pre 框追加 textContent。
 */

class AgentStream extends HTMLElement {
    constructor() {
        super();
        this.client = new MANSApiClient();
        this.projectId = null;
        this.eventSource = null;
        this.isRunning = false;
        this._paused = false;
    }

    connectedCallback() {
        this.innerHTML = `
            <div class="panel-title" style="display:flex;justify-content:space-between;align-items:center;">
                <span>Agent 实时流</span>
                <span id="stream-status" style="font-size:10px;color:var(--text-muted);">就绪</span>
            </div>
            <div class="stream-layout">
                <div class="stream-section">
                    <div class="stream-section-header">
                        <span>思考</span>
                        <span id="reasoning-agent" class="stream-section-agent"></span>
                    </div>
                    <pre class="stream-section-body" id="reasoning-box"></pre>
                </div>
                <div class="stream-section">
                    <div class="stream-section-header">
                        <span>正文</span>
                        <span id="output-agent" class="stream-section-agent"></span>
                    </div>
                    <pre class="stream-section-body" id="output-box"></pre>
                </div>
            </div>
            <div style="padding:10px;border-top:1px solid var(--border);display:flex;gap:6px;">
                <button class="btn btn-secondary" id="btn-clear" style="flex:1;font-size:11px;">清空</button>
                <button class="btn btn-secondary" id="btn-pause" style="flex:1;font-size:11px;">暂停</button>
                <button class="btn btn-danger" id="btn-stop" style="flex:1;font-size:11px;">停止</button>
            </div>
        `;

        this.querySelector("#btn-clear").addEventListener("click", () => this.clear());
        this.querySelector("#btn-pause").addEventListener("click", () => this.togglePause());
        this.querySelector("#btn-stop").addEventListener("click", () => this._onStop());
    }

    start(projectId, options = {}) {
        const { clear = true } = options;
        if (this.eventSource) {
            this.eventSource.close();
        }
        this.projectId = projectId;
        this.isRunning = true;
        this._paused = false;
        this._setStatus("连接中...");
        if (clear) {
            this.clear();
        } else {
            // 不清空时尝试恢复该项目上次保存的流内容
            this._restoreContent();
        }

        this.eventSource = this.client.connectStream(projectId, (event) => {
            if (!this._paused) {
                this._handleEvent(event);
            }
        });
    }

    stop() {
        this.isRunning = false;
        if (this.eventSource) {
            this.eventSource.close();
            this.eventSource = null;
        }
        this._setStatus("已停止");
    }

    clear() {
        if (this.isRunning) {
            alert("流正在运行中，请先停止或等待完成后再清空。");
            return;
        }
        const rbox = this.querySelector("#reasoning-box");
        const obox = this.querySelector("#output-box");
        if (rbox) rbox.textContent = "";
        if (obox) obox.textContent = "";
        this.querySelector("#reasoning-agent").textContent = "";
        this.querySelector("#output-agent").textContent = "";
        this._clearStorage();
    }

    _storageKey(kind) {
        const pid = this.projectId || "global";
        return `mans:stream:${pid}:${kind}`;
    }

    _persistContent() {
        try {
            const rbox = this.querySelector("#reasoning-box");
            const obox = this.querySelector("#output-box");
            if (rbox) sessionStorage.setItem(this._storageKey("reasoning"), rbox.textContent);
            if (obox) sessionStorage.setItem(this._storageKey("output"), obox.textContent);
            const rAgent = this.querySelector("#reasoning-agent");
            const oAgent = this.querySelector("#output-agent");
            if (rAgent) sessionStorage.setItem(this._storageKey("ragent"), rAgent.textContent);
            if (oAgent) sessionStorage.setItem(this._storageKey("oagent"), oAgent.textContent);
        } catch (e) {
            // sessionStorage 容量超限时不阻塞
        }
    }

    _restoreContent() {
        try {
            const rbox = this.querySelector("#reasoning-box");
            const obox = this.querySelector("#output-box");
            const rAgent = this.querySelector("#reasoning-agent");
            const oAgent = this.querySelector("#output-agent");

            const pid = this.projectId;
            if (!pid) return;
            const prefix = `mans:stream:${pid}`;

            if (rbox) {
                const rv = sessionStorage.getItem(`${prefix}:reasoning`);
                if (rv) rbox.textContent = rv;
            }
            if (obox) {
                const ov = sessionStorage.getItem(`${prefix}:output`);
                if (ov) obox.textContent = ov;
            }
            if (rAgent) {
                const ra = sessionStorage.getItem(`${prefix}:ragent`);
                if (ra) rAgent.textContent = ra;
            }
            if (oAgent) {
                const oa = sessionStorage.getItem(`${prefix}:oagent`);
                if (oa) oAgent.textContent = oa;
            }
        } catch (e) {
            console.error("恢复流内容失败", e);
        }
    }

    _clearStorage() {
        try {
            Object.keys(sessionStorage).forEach(k => {
                if (k.startsWith("mans:stream:")) sessionStorage.removeItem(k);
            });
        } catch (e) {}
    }

    togglePause() {
        const btn = this.querySelector("#btn-pause");
        if (btn.textContent === "暂停") {
            this._paused = true;
            btn.textContent = "继续";
            this._setStatus("已暂停");
        } else {
            this._paused = false;
            btn.textContent = "暂停";
            this._setStatus("运行中");
        }
    }

    async _onStop() {
        if (!this.isRunning || !this.projectId) {
            this.stop();
            return;
        }
        try {
            this._setStatus("正在停止...");
            await this.client.sendCommand(this.projectId, "停止当前执行");
        } catch (e) {
            console.error("发送停止指令失败", e);
        }
        this.stop();
    }

    _handleEvent(event) {
        const agent = event.data.agent || "";

        if (event.type === "reasoning") {
            const box = this.querySelector("#reasoning-box");
            const agentLabel = this.querySelector("#reasoning-agent");
            if (agentLabel && agent) agentLabel.textContent = agent;
            if (box) {
                box.textContent += event.data.content || "";
                this._autoScroll(box);
            }
        } else if (event.type === "output") {
            const box = this.querySelector("#output-box");
            const agentLabel = this.querySelector("#output-agent");
            if (agentLabel && agent) agentLabel.textContent = agent;
            if (box) {
                const content = event.data.content || "";
                // 检测 JSON 结构化输出，显示简化提示而非原始 JSON
                if (this._isJsonLike(content)) {
                    const summary = this._tryExtractJsonSummary(content, agent);
                    box.textContent += summary;
                } else {
                    box.textContent += content;
                }
                this._autoScroll(box);
            }
        } else if (event.type === "completed") {
            const rbox = this.querySelector("#reasoning-box");
            const obox = this.querySelector("#output-box");
            const tc = event.data.tool_calls || [];
            const outputTypes = event.data.output_types || [];

            // 在 reasoning-box 中显示本轮输出类型摘要
            const typeNote = outputTypes.length
                ? `\n[${agent || "Agent"}] 完成 · ${event.data.total_tokens || 0} tokens · 输出类型: ${outputTypes.join(", ")}`
                : `\n[${agent || "Agent"}] 完成 · ${event.data.total_tokens || 0} tokens`;
            if (rbox) {
                rbox.textContent += typeNote;
                this._autoScroll(rbox);
            }

            // 在 output-box 中显示工具调用摘要（如果有）
            if (tc.length && obox) {
                const toolSummary = this._formatToolCallSummary(tc, agent);
                obox.textContent += toolSummary;
                this._autoScroll(obox);
            }
        } else if (event.type === "confirm" || event.type === "ask_user") {
            this._setStatus(event.type === "ask_user" ? "等待答复" : "等待确认");
            this.dispatchEvent(new CustomEvent("stage-confirm", {
                detail: event.data,
                bubbles: true,
            }));
            const obox = this.querySelector("#output-box");
            if (obox) {
                if (event.type === "ask_user") {
                    obox.textContent += `\n[询问用户] ${event.data.question || "需要确认"}\n`;
                } else {
                    obox.textContent += `\n[阶段确认] ${event.data.from_stage} → ${event.data.to_stage}\n`;
                }
                this._autoScroll(obox);
            }
        } else if (event.type === "error" || event.type === "sse_error") {
            const obox = this.querySelector("#output-box");
            const msg = event.data.error || event.data.message || "未知错误";
            if (obox) {
                obox.textContent += `\n[错误] ${msg}\n`;
                this._autoScroll(obox);
            }
            this._setStatus("出错");
            this.isRunning = false;
            this._notifyEnded("error", msg);
        } else if (event.type === "done") {
            this._setStatus("完成");
            this.isRunning = false;
            this._notifyEnded("done");
        }

        // 每次事件处理后持久化到 sessionStorage（刷新后可恢复）
        this._persistContent();
    }

    _autoScroll(el) {
        // 仅在用户没有手动向上滚动时才自动滚到底
        const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 50;
        if (nearBottom) {
            el.scrollTop = el.scrollHeight;
        }
    }

    _setStatus(text) {
        const el = this.querySelector("#stream-status");
        if (el) el.textContent = text;
    }

    _notifyEnded(reason, detail = "") {
        this.dispatchEvent(new CustomEvent("stream-ended", {
            detail: { projectId: this.projectId, reason, detail },
            bubbles: true,
        }));
    }

    // --------------------------------------------------------
    // 辅助:JSON 检测与摘要
    // --------------------------------------------------------
    _isJsonLike(str) {
        if (!str || typeof str !== "string") return false;
        const trimmed = str.trim();
        return (trimmed.startsWith("{") && trimmed.endsWith("}")) ||
               (trimmed.startsWith("[") && trimmed.endsWith("]"));
    }

    _tryExtractJsonSummary(jsonStr, agentName) {
        try {
            const data = JSON.parse(jsonStr.trim());
            // 角色卡摘要
            if (data.name && (data.appearance || data.personality_core)) {
                return `\n[${agentName || "Expert"}] 完成角色卡: ${data.name}${data.personality_core ? " · " + data.personality_core : ""}\n`;
            }
            // 规则/世界规则摘要
            if (data.rules && Array.isArray(data.rules)) {
                const cats = [...new Set(data.rules.map(r => r.category).filter(Boolean))];
                return `\n[${agentName || "Expert"}] 完成世界观: ${data.rules.length} 条规则${cats.length ? " · 覆盖: " + cats.join(", ") : ""}\n`;
            }
            // 关系网摘要
            if (data.relationships && Array.isArray(data.relationships)) {
                return `\n[${agentName || "Expert"}] 完成关系网: ${data.relationships.length} 条关系\n`;
            }
            // 大纲/章节规划摘要
            if (data.chapters && Array.isArray(data.chapters)) {
                return `\n[${agentName || "Expert"}] 完成大纲: ${data.chapters.length} 章\n`;
            }
            if (data.scenes && Array.isArray(data.scenes)) {
                return `\n[${agentName || "Expert"}] 完成场景规划: ${data.scenes.length} 个场景\n`;
            }
            // 通用 fallback: 提取第一个有意义的字符串字段
            const firstKey = Object.keys(data).find(k => typeof data[k] === "string" && data[k].length > 0 && k !== "id");
            if (firstKey) {
                const preview = String(data[firstKey]).slice(0, 30);
                return `\n[${agentName || "Expert"}] 完成: ${firstKey}=${preview}${data[firstKey].length > 30 ? "..." : ""}\n`;
            }
            return `\n[${agentName || "Expert"}] 完成结构化输出\n`;
        } catch (e) {
            // 解析失败，回退到原样显示（但截断防刷屏）
            const preview = jsonStr.trim().slice(0, 80);
            return `\n[${agentName || "Expert"}] 输出: ${preview}${jsonStr.trim().length > 80 ? "..." : ""}\n`;
        }
    }

    _formatToolCallSummary(toolCalls, agentName) {
        if (!toolCalls || !toolCalls.length) return "";
        const names = toolCalls.map(tc => tc.name).join(", ");
        const argsPreview = toolCalls.map(tc => {
            try {
                const args = JSON.parse(tc.arguments || "{}");
                const firstVal = Object.values(args).find(v => typeof v === "string");
                return firstVal ? `(${firstVal.slice(0, 20)}${firstVal.length > 20 ? "..." : ""})` : "()";
            } catch (e) {
                return "()";
            }
        }).join(", ");
        return `\n[${agentName || "Agent"}] 调用工具: ${names} ${argsPreview}\n`;
    }
}

customElements.define("agent-stream", AgentStream);
