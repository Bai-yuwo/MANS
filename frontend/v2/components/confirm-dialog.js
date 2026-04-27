/**
 * confirm-dialog.js — 阶段确认弹窗 Web Component
 *
 * 职责:
 *   - 当 SSE 收到 confirm 事件时弹出
 *   - 展示当前阶段成果摘要(summary)和确认问句(prompt)
 *   - 收集用户回复(默认"同意,进入下一阶段")
 *   - 调用 POST /api/v2/projects/{pid}/respond
 *   - 触发 confirm-responded 事件让 app.js 重新连接 SSE
 */

class ConfirmDialog extends HTMLElement {
    constructor() {
        super();
        this.client = new MANSApiClient();
        this.projectId = null;
    }

    connectedCallback() {
        this.style.display = "none";
        this.innerHTML = `
            <div class="modal-overlay" id="overlay">
                <div class="modal">
                    <div class="modal-header">
                        阶段切换确认
                        <span id="stage-label" style="float:right;font-size:11px;color:var(--text-secondary);"></span>
                    </div>
                    <div class="modal-body">
                        <div id="confirm-summary" style="font-size:13px;line-height:1.7;margin-bottom:12px;background:var(--bg-primary);padding:10px;border-radius:var(--radius);"></div>
                        <div id="confirm-prompt" style="font-size:12px;color:var(--text-secondary);margin-bottom:12px;"></div>
                        <div class="form-group">
                            <label>你的回复</label>
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
        this.querySelector("#stage-label").textContent = `${data.from_stage} → ${data.to_stage}`;
        this.querySelector("#confirm-summary").textContent = data.summary || "(无摘要)";
        this.querySelector("#confirm-prompt").textContent = data.prompt || "是否确认进入下一阶段?";
        this.querySelector("#confirm-reply").value = "同意，进入下一阶段。";
        this.style.display = "block";
    }

    hide() {
        this.style.display = "none";
        this.projectId = null;
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
            btn.textContent = "确认并继续";
        }
    }
}

customElements.define("confirm-dialog", ConfirmDialog);
