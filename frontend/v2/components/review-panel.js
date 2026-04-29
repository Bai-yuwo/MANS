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
        this._commandPending = false;
        this._commandTimeout = null;
        this._perfData = null;
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
        document.addEventListener("stream-packet", () => this._unlockButtons());
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

    _lockButtons() {
        this._commandPending = true;
        const bar = this.querySelector(".review-action-bar");
        if (!bar) return;
        bar.querySelectorAll("button").forEach((btn) => {
            btn.disabled = true;
            btn.style.opacity = "0.5";
            btn.style.cursor = "not-allowed";
        });
    }

    _unlockButtons() {
        if (!this._commandPending) return;
        this._commandPending = false;
        if (this._commandTimeout) {
            clearTimeout(this._commandTimeout);
            this._commandTimeout = null;
        }
        const bar = this.querySelector(".review-action-bar");
        if (!bar) return;
        bar.querySelectorAll("button").forEach((btn) => {
            btn.disabled = false;
            btn.style.opacity = "";
            btn.style.cursor = "";
        });
    }

    _notifyAgentStream(message) {
        const agentStream = document.querySelector("#agent-stream");
        if (!agentStream) return;
        const obox = agentStream.querySelector("#output-box");
        if (!obox) return;
        obox.textContent += `\n${message}\n`;
        // 自动滚动到底部
        obox.scrollTop = obox.scrollHeight;
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

    async _loadData() {
        if (!this._projectId || !this._api) return;
        this._loading = true;
        this._render();

        // 并行加载审查历史与 token 审计
        const [reviewData, perfData] = await Promise.allSettled([
            this._api.getSceneReviewHistory(
                this._projectId,
                this._chapterNumber,
                this._sceneIndex
            ),
            this._api.getPerformance(
                this._projectId,
                this._chapterNumber,
                this._sceneIndex
            ),
        ]);

        this._data = reviewData.status === "fulfilled"
            ? reviewData.value
            : { error: reviewData.reason?.message || String(reviewData.reason) };
        this._perfData = perfData.status === "fulfilled" ? perfData.value : null;

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
                    ${this._renderIssueStats(criticIssues, continuityIssues, consistencyIssues)}
                    ${scores ? this._renderScores(scores) : ""}
                    ${this._data?.issues?.metrics ? this._renderMetrics(this._data.issues.metrics) : ""}
                    ${this._renderIssuesSection(criticIssues, continuityIssues, consistencyIssues)}
                    ${this._renderGuidanceSection(guidanceHistory)}
                    ${this._renderTokenAudit()}
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

    _renderIssueStats(criticIssues, continuityIssues, consistencyIssues) {
        const hasConsistency = consistencyIssues.length > 0;
        const total = criticIssues.length + continuityIssues.length + consistencyIssues.length;
        if (total === 0) return "";

        const severityOrder = ["critical", "high", "medium", "low"];
        const severityColor = {
            critical: "#f44336",
            high: "#e94560",
            medium: "#ff9800",
            low: "#666",
        };

        const countBySev = (issues) => {
            const counts = { critical: 0, high: 0, medium: 0, low: 0 };
            issues.forEach((i) => {
                const s = (i.severity || "low").toLowerCase();
                counts[s] = (counts[s] || 0) + 1;
            });
            return counts;
        };

        const renderDots = (counts) => {
            return severityOrder
                .filter((s) => counts[s] > 0)
                .map((s) => {
                    const n = counts[s];
                    const dots = `●`.repeat(Math.min(n, 5)) + (n > 5 ? `+${n - 5}` : "");
                    return `<span style="color:${severityColor[s]}" title="${s}: ${n}">${dots}</span>`;
                })
                .join(" ");
        };

        const cCounts = countBySev(criticIssues);
        const coCounts = countBySev(continuityIssues);
        const co2Counts = countBySev(consistencyIssues);

        return `
            <div class="review-issue-stats">
                <div class="review-issue-stat-col">
                    <span class="review-issue-stat-label">Critic</span>
                    <span class="review-issue-stat-dots">${renderDots(cCounts)}</span>
                    <span class="review-issue-stat-num">${criticIssues.length}</span>
                </div>
                <div class="review-issue-stat-col">
                    <span class="review-issue-stat-label">Continuity</span>
                    <span class="review-issue-stat-dots">${renderDots(coCounts)}</span>
                    <span class="review-issue-stat-num">${continuityIssues.length}</span>
                </div>
                ${hasConsistency ? `
                <div class="review-issue-stat-col">
                    <span class="review-issue-stat-label">Consistency</span>
                    <span class="review-issue-stat-dots">${renderDots(co2Counts)}</span>
                    <span class="review-issue-stat-num">${consistencyIssues.length}</span>
                </div>
                ` : ""}
            </div>
        `;
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

            // Diff 比对视图
            const diffHtml = this._renderGuidanceDiff(g);

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
                    ${diffHtml}
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

    _renderGuidanceDiff(g) {
        // 若后端返回了 before_text / after_text，渲染真正文本 diff
        const hasTextDiff = g.before_text && g.after_text;
        if (hasTextDiff) {
            const diff = this._computeDiff(g.before_text, g.after_text);
            return `
                <div class="review-guidance-block">
                    <div class="review-guidance-label">修改对比</div>
                    <div class="diff-container">
                        ${this._renderTextDiff(diff)}
                    </div>
                </div>
            `;
        }

        // 否则用 must_keep / must_change 做结构化摘要对比
        const mustKeep = (g.must_keep || []).map((k) =>
            `<div class="diff-summary-line diff-summary-keep"><span class="diff-summary-sign">+</span>${this._escapeHtml(k)}</div>`
        ).join("");
        const mustChange = (g.must_change || []).map((c) =>
            `<div class="diff-summary-line diff-summary-change"><span class="diff-summary-sign">−</span>${this._escapeHtml(c)}</div>`
        ).join("");

        if (!mustKeep && !mustChange) return "";

        return `
            <div class="review-guidance-block">
                <div class="review-guidance-label">修改摘要</div>
                <div class="diff-summary-container">
                    ${mustKeep}
                    ${mustChange}
                </div>
            </div>
        `;
    }

    _computeDiff(before, after) {
        const a = before.split("\n");
        const b = after.split("\n");
        const diff = [];
        let i = 0,
            j = 0;

        while (i < a.length || j < b.length) {
            if (i >= a.length) {
                diff.push({ type: "add", text: b[j++] });
            } else if (j >= b.length) {
                diff.push({ type: "del", text: a[i++] });
            } else if (a[i] === b[j]) {
                diff.push({ type: "same", text: a[i++] });
                j++;
            } else {
                const nextMatchA = a.indexOf(b[j], i + 1);
                const nextMatchB = b.indexOf(a[i], j + 1);
                if (nextMatchA !== -1 && (nextMatchB === -1 || nextMatchA - i <= nextMatchB - j)) {
                    while (i < nextMatchA) diff.push({ type: "del", text: a[i++] });
                } else if (nextMatchB !== -1) {
                    while (j < nextMatchB) diff.push({ type: "add", text: b[j++] });
                } else {
                    diff.push({ type: "del", text: a[i++] });
                    diff.push({ type: "add", text: b[j++] });
                }
            }
        }
        return diff;
    }

    _renderTextDiff(diff) {
        return diff
            .map((d) => {
                const cls = d.type === "add" ? "diff-add" : d.type === "del" ? "diff-del" : "diff-same";
                const sign = d.type === "add" ? "+" : d.type === "del" ? "−" : " ";
                return `<div class="diff-line ${cls}"><span class="diff-sign">${sign}</span><span class="diff-text">${this._escapeHtml(d.text)}</span></div>`;
            })
            .join("");
    }

    _renderTokenAudit() {
        const perf = this._perfData;
        if (!perf || !perf.entries || perf.entries.length === 0) {
            return "";
        }

        const fmtK = (n) => n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
        const fmtTime = (ms) => {
            const s = Math.round(ms / 1000);
            return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
        };

        const agents = Object.entries(perf.agent_breakdown || {})
            .sort((a, b) => b[1].tokens - a[1].tokens)
            .map(([name, data]) => {
                const agentClass = name === "Writer" ? "agent-writer" :
                    name === "Critic" ? "agent-critic" :
                    name === "SceneDirector" ? "agent-director" : "agent-other";
                return `
                    <div class="token-audit-row">
                        <span class="token-audit-name ${agentClass}">${name}</span>
                        <span class="token-audit-count">${data.count}次</span>
                        <span class="token-audit-tokens">${fmtK(data.tokens)} tok</span>
                        <span class="token-audit-time">${fmtTime(data.duration_ms)}</span>
                    </div>
                `;
            })
            .join("");

        return `
            <div class="review-section">
                <div class="review-section-header">
                    <span class="review-toggle-icon">▼</span>
                    <span class="review-section-title">Token 消耗</span>
                    <span class="review-section-count">${fmtK(perf.total_tokens)} tok</span>
                </div>
                <div class="review-section-body">
                    <div class="token-audit-summary">
                        <span>输入 ${fmtK(perf.total_input_tokens)} tok</span>
                        <span>输出 ${fmtK(perf.total_output_tokens)} tok</span>
                        <span>总耗时 ${fmtTime(perf.total_duration_ms)}</span>
                    </div>
                    ${agents}
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
        // 立即锁定按钮 + 本地反馈
        this._lockButtons();
        this._notifyAgentStream("[系统] 已将您的指令传达给 Director，等待响应...");

        // 10 秒超时：无响应则提示延迟并解锁
        this._commandTimeout = setTimeout(() => {
            this._notifyAgentStream("[系统] 响应延迟较长，请稍候或检查网络");
            this._unlockButtons();
        }, 10000);

        try {
            const result = await this._api.sendCommand(this._projectId, instruction);
            console.log("[review-panel] 指令已发送:", result);
        } catch (err) {
            console.error("[review-panel] 发送指令失败:", err);
            this._notifyAgentStream(`[系统] 指令发送失败: ${err.message}`);
            this._unlockButtons();
        }
    }

    async _respond(reply) {
        if (!this._api || !this._projectId) {
            alert("API 客户端未初始化");
            return;
        }
        // 立即锁定按钮 + 本地反馈
        this._lockButtons();
        this._notifyAgentStream("[系统] 已将您的答复传达给 Director，等待响应...");

        // 10 秒超时
        this._commandTimeout = setTimeout(() => {
            this._notifyAgentStream("[系统] 响应延迟较长，请稍候或检查网络");
            this._unlockButtons();
        }, 10000);

        try {
            const result = await this._api.approve(this._projectId, reply);
            console.log("[review-panel] 答复已发送:", result);
            this._waitingAskUser = false;
            this._askUserData = null;
            this._updateButtonStates();
        } catch (err) {
            console.error("[review-panel] 发送答复失败:", err);
            this._notifyAgentStream(`[系统] 答复发送失败: ${err.message}`);
            this._unlockButtons();
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
