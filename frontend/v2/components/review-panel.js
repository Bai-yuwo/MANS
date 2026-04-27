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
                    ${this._renderIssuesSection(criticIssues, continuityIssues, consistencyIssues)}
                    ${this._renderGuidanceSection(guidanceHistory)}
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

    _escapeHtml(text) {
        if (!text) return "";
        const div = document.createElement("div");
        div.textContent = text;
        return div.innerHTML;
    }
}

customElements.define("review-panel", ReviewPanel);
