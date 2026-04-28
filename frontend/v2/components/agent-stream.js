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
        this._reconnectCount = 0;
        this._maxReconnect = 10;
        this._reconnectTimer = null;
        this._lastKbRefresh = 0;
    }

    // 会修改知识库数据的工具列表（completed 后触发前端刷新）
    static KB_WRITE_TOOLS = [
        "save_bible", "append_foreshadowing", "apply_kb_diff",
        "save_character", "save_relationships",
        "save_outline", "save_arc", "save_chapter_plan",
        "save_scene_beatsheet", "save_scene_draft", "save_scene_final",
        "save_review_issues", "save_rewrite_guidance",
        "save_geo_node", "save_faction_node", "save_cultivation_node",
        "save_tech_node", "save_social_node", "save_setting_node",
        "clear_checkpoint",
    ];

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
        const { clear = true, isReconnect = false } = options;
        // 清除任何待执行的自动重连计时器，防止旧计时器干扰新连接
        if (this._reconnectTimer) {
            clearTimeout(this._reconnectTimer);
            this._reconnectTimer = null;
        }
        if (this.eventSource) {
            this.eventSource.close();
            this.eventSource = null;
        }
        this.projectId = projectId;
        this._paused = false;

        if (isReconnect) {
            this._reconnectCount++;
            this._setStatus(`重连中...(第${this._reconnectCount}次)`);
        } else {
            this._reconnectCount = 0;
            this._setStatus("连接中...");
        }

        if (clear) {
            this.clear();
        } else if (!isReconnect) {
            // 不清空时尝试恢复该项目上次保存的流内容
            this._restoreContent();
        }

        this.isRunning = true;

        this.eventSource = this.client.connectStream(projectId, (event) => {
            if (!this._paused) {
                this._handleEvent(event);
            }
        });

        // 覆盖 onopen：重连成功后同步后端状态
        this.eventSource.onopen = () => {
            console.log("[SSE] 连接已建立", projectId);
            if (this._reconnectCount > 0) {
                this._setStatus("运行中");
                this._syncStatus();
            }
        };

        // 覆盖 onerror，接管重连逻辑
        this.eventSource.onerror = (e) => {
            this._onSseError(e);
        };
    }

    _onSseError(e) {
        console.error("[SSE] 连接错误", e);
        if (!this.isRunning) return;

        if (this._reconnectCount < this._maxReconnect) {
            const backoffMs = Math.min(Math.pow(2, this._reconnectCount) * 1000, 30000);
            this._setStatus(`断线，${backoffMs / 1000}秒后重连...`);
            const obox = this.querySelector("#output-box");
            if (obox) {
                obox.textContent += `\n[SSE] 连接中断，${backoffMs / 1000}秒后尝试重连(第${this._reconnectCount + 1}次)...\n`;
                this._autoScroll(obox);
            }
            this._reconnectTimer = setTimeout(() => {
                this.start(this.projectId, { clear: false, isReconnect: true });
            }, backoffMs);
        } else {
            this._setStatus("重连失败");
            const obox = this.querySelector("#output-box");
            if (obox) {
                obox.textContent += `\n[SSE] 重连次数已达上限(${this._maxReconnect}次)，请手动刷新页面。\n`;
                this._autoScroll(obox);
            }
            this.isRunning = false;
            this._notifyEnded("error", "SSE 重连失败");
        }
    }

    async _syncStatus() {
        if (!this.projectId) return;
        try {
            const status = await this.client.getStatus(this.projectId);
            if (!status.pump_running && this.isRunning) {
                // 后端 pump 已结束但前端仍显示运行中，自动修正
                this._setStatus("已完成");
                this.isRunning = false;
                if (this._reconnectTimer) {
                    clearTimeout(this._reconnectTimer);
                    this._reconnectTimer = null;
                }
                if (this.eventSource) {
                    this.eventSource.close();
                    this.eventSource = null;
                }
                this._notifyEnded("done");
                const obox = this.querySelector("#output-box");
                if (obox) {
                    obox.textContent += "\n[系统] 后端任务已完成，流已关闭。\n";
                    this._autoScroll(obox);
                }
            }
        } catch (e) {
            console.error("[SSE] 状态同步失败", e);
        }
    }

    stop() {
        this.isRunning = false;
        if (this._reconnectTimer) {
            clearTimeout(this._reconnectTimer);
            this._reconnectTimer = null;
        }
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

            // 若本轮调用了 KB 写工具，广播 kb-changed 事件（防抖 3 秒）
            const now = Date.now();
            if (now - this._lastKbRefresh > 3000) {
                const hasKbWrite = tc.some(t => AgentStream.KB_WRITE_TOOLS.includes(t.name));
                if (hasKbWrite) {
                    this._lastKbRefresh = now;
                    this.dispatchEvent(new CustomEvent("kb-changed", {
                        detail: { projectId: this.projectId, tools: tc.map(t => t.name) },
                        bubbles: true,
                    }));
                }
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
            // 若仍在运行或正在重连中且未达上限，视为可恢复中断，由 _onSseError 统一处理重连
            if ((this.isRunning || this._reconnectCount > 0) && this._reconnectCount < this._maxReconnect) {
                const obox = this.querySelector("#output-box");
                if (obox) {
                    obox.textContent += `\n[提示] 连接中断，正在尝试自动恢复...\n`;
                    this._autoScroll(obox);
                }
                return;
            }
            const obox = this.querySelector("#output-box");
            const msg = event.data.error || event.data.message || "未知错误";
            if (obox) {
                obox.textContent += `\n[错误] ${msg}\n`;
                this._autoScroll(obox);
            }
            this._setStatus("出错");
            this.isRunning = false;
            this._reconnectCount = 0;
            if (this._reconnectTimer) {
                clearTimeout(this._reconnectTimer);
                this._reconnectTimer = null;
            }
            this._notifyEnded("error", msg);
        } else if (event.type === "done") {
            this._setStatus("完成");
            this.isRunning = false;
            this._notifyEnded("done");
        }

        // 每次事件处理后广播 packet 到达（供 review-panel 等解锁按钮用）
        this.dispatchEvent(new CustomEvent("stream-packet", {
            detail: { type: event.type, agent: event.data?.agent || "" },
            bubbles: true,
        }));

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
