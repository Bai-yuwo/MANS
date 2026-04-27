/**
 * confirm-dialog.js — 通用用户交互弹窗 Web Component
 *
 * 职责:
 *   - 当 SSE 收到 confirm 或 ask_user 事件时弹出
 *   - confirm(kind="stage_advance")：阶段切换确认 UI
 *   - ask_user(kind="user_question")：通用问答 UI（支持选项按钮）
 *   - 收集用户回复，调用 POST /api/v2/projects/{pid}/respond
 *   - 触发 confirm-responded 事件让 app.js 重新连接 SSE
 */

class ConfirmDialog extends HTMLElement {
    constructor() {
        super();
        this.client = new MANSApiClient();
        this.projectId = null;
        this._data = null;
    }

    connectedCallback() {
        this.style.display = "none";
        this.innerHTML = `
            <div class="modal-overlay" id="overlay">
                <div class="modal">
                    <div class="modal-header" id="modal-header">
                        阶段切换确认
                        <span id="stage-label" style="float:right;font-size:11px;color:var(--text-secondary);"></span>
                    </div>
                    <div class="modal-body">
                        <div id="confirm-summary" style="font-size:13px;line-height:1.7;margin-bottom:12px;background:var(--bg-primary);padding:10px;border-radius:var(--radius);"></div>
                        <div id="confirm-prompt" style="font-size:12px;color:var(--text-secondary);margin-bottom:12px;"></div>
                        <div id="ask-user-options" style="display:none;margin-bottom:12px;flex-wrap:wrap;gap:6px;"></div>
                        <div class="form-group">
                            <label id="reply-label">你的回复</label>
                            <textarea id="confirm-reply" rows="3">同意，进入下一阶段。</textarea>
                        </div>
                    </div>
                    <div class="modal-footer">
                        <button class="btn btn-secondary" id="btn-cancel">取消</button>
                        <button class="btn btn-primary" id="btn-confirm">确认并继续</button>
                    </div>
                </div>
            </div>
        `;

        this.querySelector("#btn-cancel").addEventListener("click", () => this.hide());
        this.querySelector("#btn-confirm").addEventListener("click", () => this._confirm());
        this.querySelector("#overlay").addEventListener("click", (e) => {
            if (e.target === this.querySelector("#overlay")) this.hide();
        });
    }

    show(projectId, data) {
        this.projectId = projectId;
        this._data = data;
        const kind = data.kind || "stage_advance";

        const header = this.querySelector("#modal-header");
        const stageLabel = this.querySelector("#stage-label");
        const summaryDiv = this.querySelector("#confirm-summary");
        const promptDiv = this.querySelector("#confirm-prompt");
        const optionsDiv = this.querySelector("#ask-user-options");
        const replyLabel = this.querySelector("#reply-label");
        const replyArea = this.querySelector("#confirm-reply");
        const confirmBtn = this.querySelector("#btn-confirm");

        if (kind === "user_question") {
            // ask_user 模式
            header.childNodes[0].textContent = "需要你的确认";
            stageLabel.style.display = "none";

            summaryDiv.textContent = data.question || "(无问题)";
            summaryDiv.style.fontWeight = "500";

            if (data.context) {
                promptDiv.textContent = data.context;
                promptDiv.style.display = "block";
            } else {
                promptDiv.style.display = "none";
            }

            // 选项按钮
            optionsDiv.innerHTML = "";
            if (data.options && data.options.length > 0) {
                optionsDiv.style.display = "flex";
                data.options.forEach(opt => {
                    const btn = document.createElement("button");
                    btn.className = "btn btn-secondary";
                    btn.style.fontSize = "12px";
                    btn.style.padding = "4px 10px";
                    btn.textContent = opt;
                    btn.addEventListener("click", () => {
                        replyArea.value = opt;
                    });
                    optionsDiv.appendChild(btn);
                });
            } else {
                optionsDiv.style.display = "none";
            }

            replyLabel.textContent = "你的答复";
            replyArea.value = data.options && data.options[0] ? data.options[0] : "";
            confirmBtn.textContent = "回复并继续";
        } else {
            // stage_advance 模式（默认）
            header.childNodes[0].textContent = "阶段切换确认";
            stageLabel.style.display = "inline";
            stageLabel.textContent = `${data.from_stage || "?"} → ${data.to_stage || "?"}`;

            summaryDiv.textContent = data.summary || "(无摘要)";
            summaryDiv.style.fontWeight = "normal";

            promptDiv.textContent = data.prompt || "是否确认进入下一阶段?";
            promptDiv.style.display = "block";

            optionsDiv.style.display = "none";

            replyLabel.textContent = "你的回复";
            replyArea.value = "同意，进入下一阶段。";
            confirmBtn.textContent = "确认并继续";
        }

        this.style.display = "block";
    }

    hide() {
        this.style.display = "none";
        this.projectId = null;
        this._data = null;
    }

    async _confirm() {
        if (!this.projectId) return;
        const reply = this.querySelector("#confirm-reply").value.trim();
        if (!reply) return;

        const btn = this.querySelector("#btn-confirm");
        btn.disabled = true;
        btn.textContent = "提交中...";

        try {
            await this.client.approve(this.projectId, reply);
            this.hide();
            this.dispatchEvent(new CustomEvent("confirm-responded", {
                detail: { projectId: this.projectId, reply },
                bubbles: true,
            }));
        } catch (e) {
            alert("提交失败: " + e.message);
        } finally {
            btn.disabled = false;
            btn.textContent = this._data && this._data.kind === "user_question" ? "回复并继续" : "确认并继续";
        }
    }
}

customElements.define("confirm-dialog", ConfirmDialog);
