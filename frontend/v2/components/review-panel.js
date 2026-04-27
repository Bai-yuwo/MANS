/**
 * review-panel.js — 审查历史面板组件
 *
 * 展示单场景的 Critic / Continuity 审查 issues 与 ReviewManager 仲裁 guidance。
 * 紧邻 chapter-reader 或作为可展开的侧栏。
 *
 * 属性:
 *   - project-id: 项目 ID
 *   - chapter-number: 章节号
 *   - scene-index: 场景索引
 *
 * 用法:
 *   <review-panel project-id="xxx" chapter-number="1" scene-index="0"></review-panel>
 */

class ReviewPanel extends HTMLElement {
    constructor() {
        super();
        this._projectId = "";
        this._chapterNumber = 1;
        this._sceneIndex = 0;
        this._data = null;
        this._loading = false;
        this._api = typeof MANSApiClient !== "undefined" ? new MANSApiClient() : null;
        this._waitingAskUser = false;
        this._askUserData = null;
    }

    connectedCallback() {
        this._projectId = this.getAttribute("project-id") || "";
        this._chapterNumber = parseInt(this.getAttribute("chapter-number"), 10) || 1;
        this._sceneIndex = parseInt(this.getAttribute("scene-index"), 10) || 0;
        this._render();
        if (this._projectId) {
            this._loadData();
        }
        document.addEventListener("ask-user-arrived", (e) => this._onAskUserArrived(e));
        document.addEventListener("ask-user-responded", (e) => this._onAskUserResponded(e));
    }

    _onAskUserArrived(e) {
        const { projectId, data } = e.detail;
        if (projectId !== this._projectId) return;
        this._waitingAskUser = true;
        this._askUserData = data;
        this._updateButtonStates();
    }

    _onAskUserResponded(e) {
        this._waitingAskUser = false;
        this._askUserData = null;
        this._updateButtonStates();
    }

    _updateButtonStates() {
        const bar = this.querySelector(".review-action-bar");
        if (!bar) return;
        if (this._waitingAskUser) {
            bar.classList.add("waiting-ask-user");
        } else {
            bar.classList.remove("waiting-ask-user");
        }
    }

    static get observedAttributes() {
        return ["project-id", "chapter-number", "scene-index"];
    }

    attributeChangedCallback(name, oldVal, newVal) {
        if (oldVal === newVal) return;
        if (name === "project-id") this._projectId = newVal;
        if (name === "chapter-number") this._chapterNumber = parseInt(newVal, 10) || 1;
        if (name === "scene-index") this._sceneIndex = parseInt(newVal, 10) || 0;
        if (this._projectId && this.isConnected) {
            this._loadData();
        }
    }

    connectedCallback() {
        this._projectId = this.getAttribute("project-id") || "";
        this._chapterNumber = parseInt(this.getAttribute("chapter-number"), 10) || 1;
        this._sceneIndex = parseInt(this.getAttribute("scene-index"), 10) || 0;
        this._render();
        if (this._projectId) {
            this._loadData();
        }
    }

    async _loadData() {
        if (!this._projectId || !this._api) return;
        this._loading = true;
        this._render();

        try {
            const data = await this._api.getSceneReviewHistory(
                this._projectId,
                this._chapterNumber,
                this._sceneIndex
            );
            this._data = data;
        } catch (err) {
            this._data = { error: err.message };
        }

        this._loading = false;
        this._render();
    }

    _render() {
        if (this._loading) {
            this.innerHTML = `
                <div class="review-panel">
                    <div class="review-header">
                        <span class="review-title">审查记录</span>
                        <span class="review-meta">第${this._chapterNumber}章 · 场景${this._sceneIndex + 1}</span>
                    </div>
                    <div class="review-loading">加载中...</div>
                </div>
            `;
            return;
        }

        if (this._data?.error) {
            this.innerHTML = `
                <div class="review-panel">
                    <div class="review-header">
                        <span class="review-title">审查记录</span>
                    </div>
                    <div class="review-empty">${this._escapeHtml(this._data.error)}</div>
                </div>
            `;
            return;
        }

        const issues = this._data?.issues;
        const guidanceHistory = this._data?.guidance_history || [];
        const criticIssues = issues?.critic_issues || [];
        const continuityIssues = issues?.continuity_issues || [];
        const consistencyIssues = issues?.consistency_issues || [];
        const scores = issues?.scores || null;

        const hasContent = criticIssues.length > 0 || continuityIssues.length > 0 || consistencyIssues.length > 0 || guidanceHistory.length > 0 || scores;

        if (!hasContent) {
            this.innerHTML = `
                <div class="review-panel">
                    <div class="review-header">
                        <span class="review-title">审查记录</span>
                        <span class="review-meta">第${this._chapterNumber}章 · 场景${this._sceneIndex + 1}</span>
                    </div>
                    <div class="review-empty">该场景暂无审查记录</div>
                </div>
            `;
            return;
        }

        this.innerHTML = `
            <div class="review-panel">
                <div class="review-header">
                    <span class="review-title">审查记录</span>
                    <span class="review-meta">第${this._chapterNumber}章 · 场景${this._sceneIndex + 1}</span>
                </div>
                <div class="review-body">
                    ${scores ? this._renderScores(scores) : ""}
                    ${this._data?.issues?.metrics ? this._renderMetrics(this._data.issues.metrics) : ""}
                    ${this._renderIssuesSection(criticIssues, continuityIssues, consistencyIssues)}
                    ${this._renderGuidanceSection(guidanceHistory)}
                    ${this._renderActionBar()}
                </div>
            </div>
        `;

        // 绑定折叠事件
        this.querySelectorAll(".review-section-header").forEach((header) => {
            header.addEventListener("click", () => {
                const body = header.nextElementSibling;
                if (body) {
                    body.classList.toggle("collapsed");
                    header.classList.toggle("collapsed");
                }
            });
        });

        // 绑定操作栏按钮事件
        const commentBtn = this.querySelector(".review-btn-comment");
        const acceptBtn = this.querySelector(".review-btn-accept");
        const rewriteBtn = this.querySelector(".review-btn-rewrite");
        if (commentBtn) commentBtn.addEventListener("click", () => this._onComment());
        if (acceptBtn) acceptBtn.addEventListener("click", () => this._onAccept());
        if (rewriteBtn) rewriteBtn.addEventListener("click", () => this._onRewrite());
    }

    _renderScores(scores) {
        const star = (n) => "★".repeat(n) + "☆".repeat(5 - n);
        const scoreClass = (n) => n <= 2 ? "score-low" : n >= 4 ? "score-high" : "score-mid";

        return `
            <div class="review-scores">
                <div class="review-score-item">
                    <span class="review-score-label">情绪曲线</span>
                    <span class="review-score-stars ${scoreClass(scores.emotion_arc_score || 0)}">${star(scores.emotion_arc_score || 0)}</span>
                    <span class="review-score-num">${scores.emotion_arc_score ?? "-"}/5</span>
                </div>
                <div class="review-score-item">
                    <span class="review-score-label">期待感</span>
                    <span class="review-score-stars ${scoreClass(scores.anticipation_score || 0)}">${star(scores.anticipation_score || 0)}</span>
                    <span class="review-score-num">${scores.anticipation_score ?? "-"}/5</span>
                </div>
                <div class="review-score-item">
                    <span class="review-score-label">爽点释放</span>
                    <span class="review-score-stars ${scoreClass(scores.payoff_satisfaction || 0)}">${star(scores.payoff_satisfaction || 0)}</span>
                    <span class="review-score-num">${scores.payoff_satisfaction ?? "-"}/5</span>
                </div>
            </div>
        `;
    }

    _renderMetrics(metrics) {
        if (!metrics) return "";

        const fmt = (n) => (typeof n === "number" ? n.toFixed(1) : "-");

        const wordCountRatio = metrics.word_count_ratio || 0;
        const protagonistActionRatio = metrics.protagonist_action_ratio || 0;
        const transitionCount = metrics.scene_transition_count || 0;
        const dialogueRatio = metrics.dialogue_to_action_ratio || 0;
        const expectedRatio = metrics.expected_dialogue_ratio || 0.4;

        // 阈值标色
        const ratioClass = (n) => n < 85 ? "metric-bad" : n > 110 ? "metric-warn" : "metric-good";
        const actionClass = (n) => n < 30 ? "metric-warn" : "metric-good";
        const transitionClass = (n) => n > 3 ? "metric-bad" : "metric-good";
        const dialogueClass = (n) => {
            const dev = Math.abs(n - expectedRatio);
            return dev > 1.5 ? "metric-warn" : "metric-good";
        };

        return `
            <div class="review-section">
                <div class="review-section-header">
                    <span class="review-toggle-icon">▼</span>
                    <span class="review-section-title">量化指标</span>
                </div>
                <div class="review-section-body">
                    <div class="review-metrics">
                        <div class="review-metric-item">
                            <span class="review-metric-label">字数利用率</span>
                            <span class="review-metric-value ${ratioClass(wordCountRatio)}">${fmt(wordCountRatio)}%</span>
                            <span class="review-metric-target">目标 ${metrics.target_word_count || 1200}</span>
                        </div>
                        <div class="review-metric-item">
                            <span class="review-metric-label">主角动作占比</span>
                            <span class="review-metric-value ${actionClass(protagonistActionRatio)}">${fmt(protagonistActionRatio)}%</span>
                        </div>
                        <div class="review-metric-item">
                            <span class="review-metric-label">场景转换次数</span>
                            <span class="review-metric-value ${transitionClass(transitionCount)}">${transitionCount}</span>
                        </div>
                        <div class="review-metric-item">
                            <span class="review-metric-label">对话动作比</span>
                            <span class="review-metric-value ${dialogueClass(dialogueRatio)}">${fmt(dialogueRatio)}</span>
                            <span class="review-metric-target">预期 ${fmt(expectedRatio)}</span>
                        </div>
                    </div>
                </div>
            </div>
        `;
    }

    _renderIssuesSection(criticIssues, continuityIssues, consistencyIssues) {
        const total = criticIssues.length + continuityIssues.length + consistencyIssues.length;
        if (total === 0) return "";

        const severityClass = (s) => {
            switch (s) {
                case "critical":
                    return "sev-critical";
                case "high":
                    return "sev-high";
                case "medium":
                    return "sev-medium";
                default:
                    return "sev-low";
            }
        };

        const renderIssue = (issue) => {
            const sev = issue.severity || "low";
            const loc = issue.location || "";
            const suggestion = issue.suggestion || "";
            const affected = issue.affected_characters || [];
            const ruleRef = issue.rule_reference || "";
            return `
                <div class="review-issue">
                    <div class="review-issue-header">
                        <span class="review-issue-type ${severityClass(sev)}">${this._escapeHtml(issue.type || "other")}</span>
                        <span class="review-issue-sev ${severityClass(sev)}">${this._escapeHtml(sev)}</span>
                    </div>
                    <div class="review-issue-desc">${this._escapeHtml(issue.description || "")}</div>
                    ${loc ? `<div class="review-issue-loc">📍 ${this._escapeHtml(loc)}</div>` : ""}
                    ${suggestion ? `<div class="review-issue-suggestion">💡 ${this._escapeHtml(suggestion)}</div>` : ""}
                    ${affected.length > 0 ? `<div class="review-issue-meta">👤 ${affected.map(a => this._escapeHtml(a)).join(", ")}</div>` : ""}
                    ${ruleRef ? `<div class="review-issue-meta">📖 ${this._escapeHtml(ruleRef)}</div>` : ""}
                </div>
            `;
        };

        return `
            <div class="review-section">
                <div class="review-section-header">
                    <span class="review-toggle-icon">▼</span>
                    <span class="review-section-title">原始 Issues</span>
                    <span class="review-section-count">${total}</span>
                </div>
                <div class="review-section-body">
                    ${criticIssues.length > 0 ? `
                        <div class="review-subheader">Critic（文学性）</div>
                        ${criticIssues.map(renderIssue).join("")}
                    ` : ""}
                    ${continuityIssues.length > 0 ? `
                        <div class="review-subheader">Continuity（设定连贯）</div>
                        ${continuityIssues.map(renderIssue).join("")}
                    ` : ""}
                    ${consistencyIssues.length > 0 ? `
                        <div class="review-subheader">Consistency（内在一致性）</div>
                        ${consistencyIssues.map(renderIssue).join("")}
                    ` : ""}
                </div>
            </div>
        `;
    }

    _renderGuidanceSection(guidanceHistory) {
        if (guidanceHistory.length === 0) return "";

        const renderGuidance = (g, i) => {
            const priority = (g.priority_issues || []).map((p) => `
                <li>${this._escapeHtml(typeof p === "string" ? p : p.description || JSON.stringify(p))}</li>
            `).join("");

            const mustKeep = (g.must_keep || []).map((k) => `
                <li>✅ ${this._escapeHtml(k)}</li>
            `).join("");

            const mustChange = (g.must_change || []).map((c) => `
                <li>🔄 ${this._escapeHtml(c)}</li>
            `).join("");

            const styleHints = g.style_hints || "";
            const needsRewrite = g.needs_rewrite ? "需要重写" : "无需重写";
            const rewriteClass = g.needs_rewrite ? "rewrite-needed" : "rewrite-ok";

            return `
                <div class="review-guidance">
                    <div class="review-guidance-header">
                        <span>第 ${g.rewrite_attempt ?? i} 轮</span>
                        <span class="review-guidance-status ${rewriteClass}">${needsRewrite}</span>
                    </div>
                    ${priority ? `<div class="review-guidance-block">
                        <div class="review-guidance-label">优先问题</div>
                        <ul>${priority}</ul>
                    </div>` : ""}
                    ${mustKeep ? `<div class="review-guidance-block">
                        <div class="review-guidance-label">必须保留</div>
                        <ul>${mustKeep}</ul>
                    </div>` : ""}
                    ${mustChange ? `<div class="review-guidance-block">
                        <div class="review-guidance-label">必须修改</div>
                        <ul>${mustChange}</ul>
                    </div>` : ""}
                    ${styleHints ? `<div class="review-guidance-block">
                        <div class="review-guidance-label">风格提示</div>
                        <div class="review-guidance-text">${this._escapeHtml(styleHints)}</div>
                    </div>` : ""}
                </div>
            `;
        };

        return `
            <div class="review-section">
                <div class="review-section-header">
                    <span class="review-toggle-icon">▼</span>
                    <span class="review-section-title">仲裁 Guidance</span>
                    <span class="review-section-count">${guidanceHistory.length}</span>
                </div>
                <div class="review-section-body">
                    ${guidanceHistory.map(renderGuidance).join("")}
                </div>
            </div>
        `;
    }

    _renderActionBar() {
        return `
            <div class="review-action-bar">
                <button class="review-btn review-btn-comment">💬 暂停并评论</button>
                <button class="review-btn review-btn-accept">✅ 接受当前稿</button>
                <button class="review-btn review-btn-rewrite">🔄 要求重写</button>
            </div>
        `;
    }

    _onComment() {
        const comment = prompt("请输入您的评论或修改意见:");
        if (comment === null) return;
        if (this._waitingAskUser) {
            // ask_user 等待中：以"补充意见后重写"回复
            this._respond(`补充意见后重写: ${comment}`);
        } else {
            const instruction = `暂停并评论: ${comment}`;
            this._sendCommand(instruction);
        }
    }

    _onAccept() {
        if (this._waitingAskUser) {
            this._respond("跳过重写保持当前稿");
            return;
        }
        if (!confirm("确认接受当前场景草稿？这将跳过重写直接继续。")) return;
        this._sendCommand("接受当前场景,跳过重写");
    }

    _onRewrite() {
        if (this._waitingAskUser) {
            this._respond("接受重写");
            return;
        }
        const comment = prompt("请输入重写补充意见(可选):");
        const base = "触发重写,补充意见:";
        const instruction = comment ? `${base} ${comment}` : base;
        this._sendCommand(instruction);
    }

    async _sendCommand(instruction) {
        if (!this._api || !this._projectId) {
            alert("API 客户端未初始化");
            return;
        }
        try {
            const result = await this._api.sendCommand(this._projectId, instruction);
            console.log("[review-panel] 指令已发送:", result);
        } catch (err) {
            console.error("[review-panel] 发送指令失败:", err);
            alert(`发送指令失败: ${err.message}`);
        }
    }

    async _respond(reply) {
        if (!this._api || !this._projectId) {
            alert("API 客户端未初始化");
            return;
        }
        try {
            const result = await this._api.approve(this._projectId, reply);
            console.log("[review-panel] 答复已发送:", result);
            this._waitingAskUser = false;
            this._askUserData = null;
            this._updateButtonStates();
        } catch (err) {
            console.error("[review-panel] 发送答复失败:", err);
            alert(`发送答复失败: ${err.message}`);
        }
    }

    _escapeHtml(text) {
        if (!text) return "";
        const div = document.createElement("div");
        div.textContent = text;
        return div.innerHTML;
    }
}

customElements.define("review-panel", ReviewPanel);
