/**
 * stage-workbench.js — 中栏阶段工作台 Web Component
 *
 * 职责:
 *   - 展示当前项目阶段(INIT/PLAN/WRITE/COMPLETED)
 *   - 提供阶段操作按钮(开始/继续 INIT/PLAN/WRITE)
 *   - 展示 KB 数据概览(bible / characters / outline 摘要)
 *   - 提供"向 Director 发送指令"折叠面板(会话活跃时可用)
 */

class StageWorkbench extends HTMLElement {
    constructor() {
        super();
        this.client = new MANSApiClient();
        this.projectId = null;
        this.project = null;
        this._isRunning = false;
        this._pumpRunning = false;
        this._waitingConfirm = false;
        this._lastOverview = null;
        this._timerStart = 0;
        this._timerInterval = null;
        this._elapsedMs = 0;
    }

    disconnectedCallback() {
        this._stopTimer();
    }

    connectedCallback() {
        this.innerHTML = `
            <div class="panel-title">阶段工作台</div>
            <div class="panel-content" id="workbench-content">
                <div style="color:var(--text-muted);text-align:center;padding:40px;font-size:13px;">
                    请先在左侧选择一个项目
                </div>
            </div>
        `;
    }

    setProject(projectId, project) {
        this.projectId = projectId;
        this.project = project;
        this._resetTimer();
        this._render();
        this._loadOverview();
        this._renderChapterReader();
    }

    _render() {
        const container = this.querySelector("#workbench-content");
        if (!this.projectId) {
            container.innerHTML = `<div style="color:var(--text-muted);text-align:center;padding:40px;">请先在左侧选择一个项目</div>`;
            return;
        }

        const stage = this.project?.stage || "INIT";
        const stageDesc = {
            INIT: "初始化阶段：构建世界观、地理、规则与角色设定",
            PLAN: "规划阶段：设计大纲、故事弧与章节场景",
            WRITE: "写作阶段：逐场景生成正文、审查、修订",
            COMPLETED: "已完成：全书写作结束",
        };

        const currentElapsed = this._pumpRunning && this._timerStart > 0
            ? Date.now() - this._timerStart
            : this._elapsedMs;
        const timerHtml = currentElapsed > 0
            ? `<span class="stage-timer" id="stage-timer">${this._formatElapsed(currentElapsed)}</span>`
            : "";

        container.innerHTML = `
            <div class="card" style="margin-bottom:16px;">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                    <div style="display:flex;align-items:center;gap:8px;">
                        <span class="stage-badge ${stage.toLowerCase()}">${stage}</span>
                        ${timerHtml}
                    </div>
                    <span style="font-size:11px;color:var(--text-secondary)">第 ${this.project?.current_chapter || 0} 章</span>
                </div>
                <div style="font-size:12px;color:var(--text-secondary);line-height:1.6;">
                    ${stageDesc[stage] || ""}
                </div>
            </div>

            <div id="stage-actions" style="margin-bottom:16px;"></div>

            <div id="instruction-area" style="margin-bottom:16px;">
                <button class="btn btn-secondary" id="btn-toggle-instruction" style="width:100%;font-size:11px;">
                    向 Director 发送指令...
                </button>
                <div id="instruction-panel" style="display:none;margin-top:8px;">
                    <div style="font-size:11px;color:var(--text-muted);margin-bottom:4px;">
                        可直接给 Director 下指令：补充设定、跳过阶段、清理数据等
                    </div>
                    <textarea id="instruction-input" rows="2"
                        placeholder="例如：补充一个反派角色 / 跳过当前阶段 / 删除角色林默 / 清理重复角色..."
                        style="width:100%;font-size:12px;padding:8px;border-radius:var(--radius);border:1px solid var(--border);background:var(--bg-primary);color:var(--text-primary);resize:vertical;"></textarea>
                    <div style="display:flex;gap:6px;margin-top:6px;">
                        <button class="btn btn-secondary" id="btn-hide-instruction" style="flex:1;font-size:11px;">收起</button>
                        <button class="btn btn-primary" id="btn-send-instruction" style="flex:1;font-size:11px;">发送指令</button>
                    </div>
                </div>
            </div>

            <div id="kb-overview">
                <div style="font-size:12px;color:var(--text-secondary);margin-bottom:8px;">知识库概览</div>
                <div class="card" style="font-size:11px;color:var(--text-muted);">
                    KB 数据加载中...
                </div>
            </div>

            <div id="chapter-reader-area" style="margin-top:16px;"></div>
        `;

        this._renderActions();
        this._wireInstructionPanel();
    }

    _renderActions() {
        const actionsDiv = this.querySelector("#stage-actions");
        const stage = this.project?.stage || "INIT";

        let buttons = "";
        if (this._pumpRunning) {
            buttons = `<button class="btn btn-primary" disabled>运行中...</button>`;
        } else if (this._waitingConfirm) {
            buttons = `<button class="btn btn-primary" disabled>等待用户确认...</button>`;
        } else if (stage === "INIT") {
            const hasData = this._hasPartialInitData();
            if (hasData) {
                buttons = `
                    <button class="btn btn-primary" id="btn-start-init" style="flex:1;">继续完善设定</button>
                    <button class="btn btn-secondary" id="btn-advance-plan" style="flex:1;">进入 PLAN 阶段</button>
                `;
            } else {
                buttons = `<button class="btn btn-primary" id="btn-start-init" style="width:100%;">开始构建世界观</button>`;
            }
        } else if (stage === "PLAN") {
            const hasData = this._hasPartialPlanData();
            if (hasData) {
                buttons = `
                    <button class="btn btn-primary" id="btn-start-plan" style="flex:1;">继续规划章节</button>
                    <button class="btn btn-secondary" id="btn-advance-write" style="flex:1;">进入 WRITE 阶段</button>
                `;
            } else {
                buttons = `<button class="btn btn-primary" id="btn-start-plan" style="width:100%;">开始设计大纲</button>`;
            }
        } else if (stage === "WRITE") {
            buttons = `<button class="btn btn-primary" id="btn-start-write" style="width:100%;">开始写作</button>`;
        } else if (stage === "COMPLETED") {
            buttons = `<div style="color:var(--success);font-size:12px;">项目已完成</div>`;
        }

        actionsDiv.innerHTML = `<div style="display:flex;gap:8px;">${buttons}</div>`;

        const btnInit = actionsDiv.querySelector("#btn-start-init");
        const btnPlan = actionsDiv.querySelector("#btn-start-plan");
        const btnWrite = actionsDiv.querySelector("#btn-start-write");
        const btnAdvancePlan = actionsDiv.querySelector("#btn-advance-plan");
        const btnAdvanceWrite = actionsDiv.querySelector("#btn-advance-write");

        if (btnInit) btnInit.addEventListener("click", () => this._startStage("INIT"));
        if (btnPlan) btnPlan.addEventListener("click", () => this._startStage("PLAN"));
        if (btnWrite) btnWrite.addEventListener("click", () => this._startStage("WRITE"));
        if (btnAdvancePlan) btnAdvancePlan.addEventListener("click", () => this._requestAdvance("PLAN"));
        if (btnAdvanceWrite) btnAdvanceWrite.addEventListener("click", () => this._requestAdvance("WRITE"));
    }

    _hasPartialInitData() {
        const ov = this._lastOverview || {};
        return (ov.bible?.count > 0) || (ov.characters?.count > 0) || (ov.foreshadowing?.count > 0);
    }

    _hasPartialPlanData() {
        const ov = this._lastOverview || {};
        return (ov.outline?.chapter_count > 0) || (ov.arcs?.count > 0) || (ov.chapter_plans?.count > 0);
    }

    _startStage(stage) {
        if (!this.projectId) return;

        // 判断是否为断点续接
        const isResume = (stage === "INIT" && this._hasPartialInitData()) ||
                         (stage === "PLAN" && this._hasPartialPlanData());

        let userPrompt;
        if (isResume) {
            const resumePrompts = {
                INIT: `继续 INIT 阶段。作品《${this.project.name}》的部分 KB 数据已存在（世界观/角色等）。请 Director 先读取 project_meta 和现有 KB，判断 WorldArchitect 和 CastingDirector 的工作哪些已完成，只补充缺失部分，不要重复创建已有内容。`,
                PLAN: `继续 PLAN 阶段。作品《${this.project.name}》的部分大纲数据已存在。请 Director 先读取现有 outline/arcs/chapter_plans，判断哪些已完成，只补充缺失部分。`,
            };
            userPrompt = resumePrompts[stage] || `继续 ${stage} 阶段，先检查现有 KB 再补充缺失部分。`;
        } else {
            const prompts = {
                INIT: `开始 INIT 阶段。请构建作品《${this.project.name}》的世界观、地理、规则与角色设定。题材：${this.project.genre}。核心创意：${this.project.core_idea || "未填写"}。`,
                PLAN: `开始 PLAN 阶段。请为作品《${this.project.name}》设计大纲、故事弧与章节场景规划。`,
                WRITE: `开始 WRITE 阶段。请为作品《${this.project.name}》撰写第 ${(this.project.current_chapter || 0) + 1} 章。`,
            };
            userPrompt = prompts[stage] || `开始 ${stage} 阶段`;
        }

        this.dispatchEvent(new CustomEvent("start-stage", {
            detail: { projectId: this.projectId, userPrompt, stage, isResume },
            bubbles: true,
        }));
    }

    _requestAdvance(targetStage) {
        if (!this.projectId) return;

        const currentStage = this.project?.stage || "INIT";
        const prompts = {
            INIT: `当前 INIT 阶段的数据已就绪，请 Director 评估是否可以推进到 PLAN 阶段。调用 confirm_stage_advance 请求用户确认。`,
            PLAN: `当前 PLAN 阶段的数据已就绪，请 Director 评估是否可以推进到 WRITE 阶段。调用 confirm_stage_advance 请求用户确认。`,
        };
        const userPrompt = prompts[currentStage] || `请 Director 推进到 ${targetStage} 阶段。`;

        this.dispatchEvent(new CustomEvent("start-stage", {
            detail: { projectId: this.projectId, userPrompt, stage: currentStage, isResume: false, isAdvance: true },
            bubbles: true,
        }));
    }

    _wireInstructionPanel() {
        const area = this.querySelector("#instruction-area");
        const toggleBtn = this.querySelector("#btn-toggle-instruction");
        const panel = this.querySelector("#instruction-panel");
        const hideBtn = this.querySelector("#btn-hide-instruction");
        const sendBtn = this.querySelector("#btn-send-instruction");

        if (!toggleBtn || !panel || !hideBtn || !sendBtn) return;

        toggleBtn.addEventListener("click", () => {
            panel.style.display = "block";
            toggleBtn.style.display = "none";
        });
        hideBtn.addEventListener("click", () => {
            panel.style.display = "none";
            toggleBtn.style.display = "block";
        });
        sendBtn.addEventListener("click", () => this._sendInstruction());
    }

    async _sendInstruction() {
        const input = this.querySelector("#instruction-input");
        const instruction = input.value.trim();
        if (!instruction || !this.projectId) return;

        // 敏感操作检测
        const sensitiveKeywords = ["删除", "清理", "清空", "移除", "删掉", "erase", "delete", "clear", "remove"];
        const isSensitive = sensitiveKeywords.some(kw => instruction.includes(kw));
        if (isSensitive) {
            const confirmed = confirm(`检测到敏感操作指令：\n\n"${instruction}"\n\n这会修改知识库数据，确定要继续吗？`);
            if (!confirmed) return;
        }

        const btn = this.querySelector("#btn-send-instruction");
        btn.disabled = true;
        btn.textContent = "发送中...";

        try {
            await this.client.sendCommand(this.projectId, instruction);
            input.value = "";
            btn.textContent = "已发送";
            // 触发事件让 app.js 重连 SSE
            this.dispatchEvent(new CustomEvent("instruction-sent", {
                detail: { projectId: this.projectId, instruction },
                bubbles: true,
            }));
            setTimeout(() => {
                btn.disabled = false;
                btn.textContent = "发送指令";
            }, 1500);
        } catch (err) {
            alert("发送指令失败: " + err.message);
            btn.disabled = false;
            btn.textContent = "发送指令";
        }
    }

    async _loadOverview() {
        if (!this.projectId) return;
        try {
            const data = await this.client.getOverview(this.projectId);
            this._lastOverview = data;
            const container = this.querySelector("#kb-overview");
            if (!container) return;

            const sections = this._buildKbSections(data);

            if (sections.length === 0) {
                container.innerHTML = `
                    <div class="kb-header">知识库概览</div>
                    <div class="card" style="font-size:11px;color:var(--text-muted);">暂无数据，请先运行 INIT 阶段</div>
                `;
                return;
            }

            container.innerHTML = `
                <div class="kb-header">知识库概览</div>
                <div class="kb-scroll">${sections.join("")}</div>
            `;

            // 绑定折叠事件
            container.querySelectorAll(".kb-section-header").forEach(hdr => {
                hdr.addEventListener("click", () => {
                    const key = hdr.dataset.key;
                    const body = container.querySelector(`.kb-section-body[data-key="${key}"]`);
                    const icon = hdr.querySelector(".kb-toggle-icon");
                    if (!body || !icon) return;
                    const expanded = body.style.display !== "none";
                    body.style.display = expanded ? "none" : "block";
                    icon.textContent = expanded ? "▸" : "▾";
                    if (!this._kbExpanded) this._kbExpanded = {};
                    this._kbExpanded[key] = !expanded;
                });
            });
        } catch (e) {
            console.error("加载 KB 概览失败", e);
        }
    }

    _buildKbSections(data) {
        const sections = [];

        if (data.bible?.count > 0) {
            const items = (data.bible.items || []).map(it => `
                <div class="kb-item">
                    <span class="kb-tag ${it.importance || 'normal'}">${it.importance || 'rule'}</span>
                    <span class="kb-item-title">[${it.category || '未分类'}]</span>
                    <span class="kb-item-text">${it.content || ''}</span>
                </div>
            `).join("");
            sections.push(this._wrapKbSection("bible", "世界观 (Bible)", data.bible.count, items));
        }

        if (data.foreshadowing?.count > 0) {
            const items = (data.foreshadowing.items || []).map(it => `
                <div class="kb-item">
                    <span class="kb-tag ${it.status || 'planted'}">${it.status || 'planted'}</span>
                    <span class="kb-item-title">[${it.type || '未分类'}]</span>
                    <span class="kb-item-text">${it.description || ''}</span>
                </div>
            `).join("");
            sections.push(this._wrapKbSection("foreshadowing", "伏笔", data.foreshadowing.count, items));
        }

        if (data.characters?.count > 0) {
            const items = (data.characters.items || []).map(it => `
                <div class="kb-item">
                    <span class="kb-tag ${it.is_protagonist ? 'protagonist' : 'character'}">${it.is_protagonist ? '主角' : '角色'}</span>
                    <span class="kb-item-title">${it.name || '未命名'}</span>
                    <div class="kb-item-detail">性格: ${it.personality_core || '—'}</div>
                    <div class="kb-item-detail">外貌: ${it.appearance || '—'}</div>
                </div>
            `).join("");
            sections.push(this._wrapKbSection("characters", "角色", data.characters.count, items));
        }

        if (data.relationships?.count > 0) {
            const items = (data.relationships.items || []).map(it => `
                <div class="kb-item">
                    <span class="kb-tag relation">关系</span>
                    <span class="kb-item-title">${it.source || ''}</span>
                    <span style="color:var(--text-muted)"> → </span>
                    <span class="kb-item-title">${it.target || ''}</span>
                    <span class="kb-item-text">(${it.type || ''})</span>
                </div>
            `).join("");
            sections.push(this._wrapKbSection("relationships", "关系网", data.relationships.count, items));
        }

        if (data.outline?.chapter_count > 0) {
            const items = (data.outline.items || []).map(it => `
                <div class="kb-item">
                    <span class="kb-tag chapter">第${it.number || '?'}章</span>
                    <span class="kb-item-title">${it.title || '未命名'}</span>
                    <div class="kb-item-detail">目标: ${it.goal || '—'}</div>
                </div>
            `).join("");
            sections.push(this._wrapKbSection("outline", "大纲", data.outline.chapter_count, items));
        }

        if (data.arcs?.count > 0) {
            const items = (data.arcs.items || []).map(it => `
                <div class="kb-item">
                    <span class="kb-tag arc">弧</span>
                    <span class="kb-item-title">${it.title || it.id || '未命名'}</span>
                </div>
            `).join("");
            sections.push(this._wrapKbSection("arcs", "故事弧", data.arcs.count, items));
        }

        if (data.chapter_plans?.count > 0) {
            const items = (data.chapter_plans.items || []).map(it => `
                <div class="kb-item">
                    <span class="kb-tag plan">第${it.chapter_number || '?'}章</span>
                    <span class="kb-item-title">${it.title || '未命名'}</span>
                    <span class="kb-item-text">(${it.scene_count || 0} 场景)</span>
                </div>
            `).join("");
            sections.push(this._wrapKbSection("chapter_plans", "章节规划", data.chapter_plans.count, items));
        }

        return sections;
    }

    _wrapKbSection(key, title, count, itemsHtml) {
        const expanded = this._kbExpanded && this._kbExpanded[key] ? 'block' : 'none';
        const icon = this._kbExpanded && this._kbExpanded[key] ? '▾' : '▸';
        return `
            <div class="kb-section">
                <div class="kb-section-header" data-key="${key}">
                    <span class="kb-toggle-icon">${icon}</span>
                    <span class="kb-section-title">${title}</span>
                    <span class="kb-section-count">${count}</span>
                </div>
                <div class="kb-section-body" data-key="${key}" style="display:${expanded}">
                    ${itemsHtml}
                </div>
            </div>
        `;
    }

    updateStatus(status) {
        if (!status || !this.project) return;
        const wasRunning = this._pumpRunning;
        this.project.stage = status.stage;
        this.project.current_chapter = status.current_chapter;
        this._isRunning = !!status.session_active;
        this._pumpRunning = !!status.pump_running;
        this._waitingConfirm = !!status.waiting_confirm;
        if (!wasRunning && this._pumpRunning) {
            this._startTimer();
        } else if (wasRunning && !this._pumpRunning) {
            this._stopTimer();
        }
        if (status.bible) this._lastOverview = status;
        this._render();
        this._loadOverview();
        this._renderChapterReader();
    }

    _renderChapterReader() {
        const area = this.querySelector("#chapter-reader-area");
        if (!area) return;

        const stage = this.project?.stage || "";
        const chapter = this.project?.current_chapter || 0;

        if (stage === "WRITE" && chapter > 0) {
            area.innerHTML = `
                <div class="kb-header">章节阅读</div>
                <chapter-reader project-id="${this.projectId}" chapter-number="${chapter}"></chapter-reader>
                <div id="chapter-review-summary" style="margin-top:12px;"></div>
                <div class="kb-header" style="margin-top:12px;">审查记录</div>
                <review-panel project-id="${this.projectId}" chapter-number="${chapter}" scene-index="0"></review-panel>
            `;
            this._loadChapterReviewSummary(chapter);
        } else {
            area.innerHTML = "";
        }
    }

    _startTimer() {
        if (this._timerInterval) return;
        this._timerStart = Date.now() - this._elapsedMs;
        this._timerInterval = setInterval(() => {
            const el = this.querySelector("#stage-timer");
            if (el) {
                el.textContent = this._formatElapsed(Date.now() - this._timerStart);
            }
        }, 1000);
        const el = this.querySelector("#stage-timer");
        if (el) {
            el.textContent = this._formatElapsed(Date.now() - this._timerStart);
        }
    }

    _stopTimer() {
        if (this._timerInterval) {
            clearInterval(this._timerInterval);
            this._timerInterval = null;
        }
        if (this._timerStart > 0) {
            this._elapsedMs = Date.now() - this._timerStart;
        }
    }

    _resetTimer() {
        this._stopTimer();
        this._timerStart = 0;
        this._elapsedMs = 0;
    }

    _formatElapsed(ms) {
        const totalSeconds = Math.floor(ms / 1000);
        const minutes = Math.floor(totalSeconds / 60);
        const seconds = totalSeconds % 60;
        return `已运行 ${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
    }

    async _loadChapterReviewSummary(chapterNumber) {
        const container = this.querySelector("#chapter-review-summary");
        if (!container) return;

        try {
            const data = await this.client.getChapterReviewSummary(this.projectId, chapterNumber);
            container.innerHTML = this._renderSummaryHTML(data);
        } catch (err) {
            // 404 表示暂无审查记录，静默处理
            if (err.message && err.message.includes("404")) {
                container.innerHTML = "";
                return;
            }
            console.error("加载章节审查摘要失败", err);
            container.innerHTML = `<div class="review-summary-error">摘要加载失败</div>`;
        }
    }

    _renderSummaryHTML(data) {
        const star = (n) => {
            if (n == null) return "—";
            const full = Math.round(n);
            return "★".repeat(full) + "☆".repeat(5 - full) + ` ${n.toFixed(1)}`;
        };

        const avg = data.avg_scores || {};
        const counts = data.issue_counts || {};

        const scoreItems = [
            { label: "情绪曲线", key: "emotion_arc_score" },
            { label: "期待感", key: "anticipation_score" },
            { label: "爽点释放", key: "payoff_satisfaction" },
        ].map(s => `
            <div class="review-summary-score-item">
                <span class="review-summary-score-label">${s.label}</span>
                <span class="review-summary-score-stars">${star(avg[s.key])}</span>
            </div>
        `).join("");

        const issueBadges = [];
        if (counts.critical > 0) issueBadges.push(`<span class="review-summary-badge critical">${counts.critical} Critical</span>`);
        if (counts.high > 0) issueBadges.push(`<span class="review-summary-badge high">${counts.high} High</span>`);
        if (counts.medium > 0) issueBadges.push(`<span class="review-summary-badge medium">${counts.medium} Medium</span>`);
        if (counts.low > 0) issueBadges.push(`<span class="review-summary-badge low">${counts.low} Low</span>`);
        if (issueBadges.length === 0) issueBadges.push(`<span class="review-summary-badge ok">无问题</span>`);

        const recs = (data.recommendations || []).map(r => `<div class="review-summary-rec">${r}</div>`).join("");

        return `
            <div class="review-summary-panel">
                <div class="review-summary-header">
                    <span class="review-summary-title">本章审查摘要</span>
                    <span class="review-summary-meta">${data.scene_count || 0} 场景已审</span>
                </div>
                <div class="review-summary-body">
                    <div class="review-summary-scores">${scoreItems}</div>
                    <div class="review-summary-issues">${issueBadges.join("")}</div>
                    <div class="review-summary-recommendations">${recs}</div>
                </div>
            </div>
        `;
    }
}

customElements.define("stage-workbench", StageWorkbench);
