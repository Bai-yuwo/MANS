/**
 * chapter-reader.js — 章节阅读/编辑组件
 *
 * 展示当前章节的拼接正文（final > draft），支持翻页和编辑保存。
 * 新增:场景级拆分视图 — 按 scene_texts 逐场景展示与编辑。
 *
 * 属性:
 *   - project-id: 项目 ID
 *   - chapter-number: 当前章节号（默认 1）
 *
 * 用法:
 *   <chapter-reader project-id="xxx" chapter-number="1"></chapter-reader>
 */

class ChapterReader extends HTMLElement {
    constructor() {
        super();
        this._projectId = "";
        this._chapterNumber = 1;
        this._content = null;
        this._editing = false;
        this._loading = false;
        this._splitView = false;
        this._sceneEditors = []; // 拆分视图下各场景编辑缓存
        this._api = typeof MANSApiClient !== "undefined" ? new MANSApiClient() : null;
        this._selectionToolbar = null;
        this._resultCard = null;
        this._toolbarTimeout = null;
        this._cardTimeout = null;
    }

    static get observedAttributes() {
        return ["project-id", "chapter-number"];
    }

    attributeChangedCallback(name, oldVal, newVal) {
        if (oldVal === newVal) return;
        if (name === "project-id") {
            this._projectId = newVal;
        }
        if (name === "chapter-number") {
            this._chapterNumber = parseInt(newVal, 10) || 1;
        }
        if (this._projectId && this.isConnected) {
            this._loadContent();
        }
    }

    connectedCallback() {
        this._projectId = this.getAttribute("project-id") || "";
        this._chapterNumber = parseInt(this.getAttribute("chapter-number"), 10) || 1;
        this._render();
        if (this._projectId) {
            this._loadContent();
        }
    }

    disconnectedCallback() {
        if (this._outsideClickHandler) {
            document.removeEventListener("mousedown", this._outsideClickHandler);
            this._outsideClickHandler = null;
        }
        if (this._toolbarTimeout) {
            clearTimeout(this._toolbarTimeout);
            this._toolbarTimeout = null;
        }
        if (this._cardTimeout) {
            clearTimeout(this._cardTimeout);
            this._cardTimeout = null;
        }
    }

    // --------------------------------------------------------
    // 数据加载
    // --------------------------------------------------------
    async _loadContent() {
        if (!this._projectId || !this._api) return;
        this._loading = true;
        this._render();

        try {
            const data = await this._api.getChapterContent(
                this._projectId,
                this._chapterNumber
            );
            this._content = data;
            this._sceneEditors = (data.scene_texts || []).map((t) => t);
        } catch (err) {
            this._content = {
                chapter_number: this._chapterNumber,
                title: `第${this._chapterNumber}章`,
                full_text: "",
                scene_texts: [],
                is_final: false,
                error: err.message,
            };
            this._sceneEditors = [];
        }

        this._loading = false;
        this._render();
    }

    // --------------------------------------------------------
    // 保存
    // --------------------------------------------------------
    async _saveContent() {
        if (!this._projectId || !this._api) return;

        let fullText = "";
        let sceneTexts = this._content?.scene_texts || [];

        if (this._splitView) {
            // 拆分视图:从各场景编辑器收集，精细维护场景边界
            const editors = this.querySelectorAll(".scene-editor");
            sceneTexts = Array.from(editors).map((ta) => ta.value);
            fullText = sceneTexts.join("\n\n");
        } else {
            // 全文视图:自由编辑，只更新 full_text，不碰 scene_texts 边界
            const textarea = this.querySelector(".chapter-editor");
            if (!textarea) return;
            fullText = textarea.value;
            // 全文编辑不自动拆分回场景——段落与场景的边界不可靠
        }

        const saveBtn = this.querySelector(".btn-save");
        if (saveBtn) {
            saveBtn.textContent = "保存中...";
            saveBtn.disabled = true;
        }

        try {
            await this._api.saveChapterContent(this._projectId, this._chapterNumber, {
                full_text: fullText,
                scene_texts: sceneTexts,
            });
            this._editing = false;
            if (this._content) {
                this._content.full_text = fullText;
                this._content.scene_texts = sceneTexts;
            }
            this._sceneEditors = sceneTexts.map((t) => t);
        } catch (err) {
            alert("保存失败: " + err.message);
        }

        this._render();
    }

    // --------------------------------------------------------
    // 翻页
    // --------------------------------------------------------
    _prevChapter() {
        if (this._chapterNumber <= 1) return;
        this._chapterNumber -= 1;
        this.setAttribute("chapter-number", this._chapterNumber);
        this._loadContent();
    }

    _nextChapter() {
        this._chapterNumber += 1;
        this.setAttribute("chapter-number", this._chapterNumber);
        this._loadContent();
    }

    // --------------------------------------------------------
    // 编辑切换
    // --------------------------------------------------------
    _toggleEdit() {
        this._editing = !this._editing;
        // 进入编辑模式时同步编辑器缓存
        if (this._editing && this._content) {
            this._sceneEditors = (this._content.scene_texts || []).map((t) => t);
        }
        this._render();
    }

    _toggleSplitView() {
        this._splitView = !this._splitView;
        // 切换视图时同步缓存
        if (this._content) {
            this._sceneEditors = (this._content.scene_texts || []).map((t) => t);
        }
        this._render();
    }

    // --------------------------------------------------------
    // 渲染
    // --------------------------------------------------------
    _render() {
        const title = this._content?.title || `第${this._chapterNumber}章`;
        const fullText = this._content?.full_text || "";
        const isFinal = this._content?.is_final || false;
        const hasError = this._content?.error;
        const sceneTexts = this._content?.scene_texts || [];

        let bodyHtml = "";
        if (this._loading) {
            bodyHtml = `<div class="chapter-loading">加载中...</div>`;
        } else if (hasError) {
            bodyHtml = `<div class="chapter-error">${this._escapeHtml(hasError)}</div>`;
        } else if (this._editing) {
            if (this._splitView && sceneTexts.length > 0) {
                bodyHtml = this._renderSplitEditors(sceneTexts);
            } else {
                bodyHtml = `
                    <textarea class="chapter-editor" placeholder="章节内容...">${this._escapeHtml(fullText)}</textarea>
                `;
            }
        } else if (this._splitView && sceneTexts.length > 0) {
            bodyHtml = this._renderSplitReaders(sceneTexts);
        } else if (!fullText) {
            bodyHtml = `<div class="chapter-empty">本章暂无内容</div>`;
        } else {
            bodyHtml = `<article class="chapter-text">${this._formatText(fullText)}</article>`;
        }

        const statusBadge = isFinal
            ? `<span class="chapter-status final">终稿</span>`
            : `<span class="chapter-status draft">草稿</span>`;

        const editBtn = this._editing
            ? `<button class="btn-save">保存</button>`
            : `<button class="btn-edit">编辑</button>`;

        const splitBtn = `
            <button class="btn-split ${this._splitView ? "active" : ""}">
                ${this._splitView ? "全文视图" : "场景拆分"}
            </button>
        `;

        const regenBtn = `
            <button class="btn-regen" title="重新生成本章">🔄</button>
        `;

        this.innerHTML = `
            <div class="chapter-reader">
                <div class="chapter-header">
                    <div class="chapter-nav">
                        <button class="btn-nav" ${this._chapterNumber <= 1 ? "disabled" : ""}>上一章</button>
                        <span class="chapter-title">${this._escapeHtml(title)}</span>
                        <button class="btn-nav">下一章</button>
                    </div>
                    <div class="chapter-actions">
                        ${statusBadge}
                        ${editBtn}
                        ${splitBtn}
                        ${regenBtn}
                    </div>
                </div>
                <div class="chapter-body">
                    ${bodyHtml}
                </div>
            </div>
        `;

        // 绑定事件
        const prevBtn = this.querySelector(".chapter-nav .btn-nav:first-child");
        const nextBtn = this.querySelector(".chapter-nav .btn-nav:last-child");
        const editBtnEl = this.querySelector(".btn-edit");
        const saveBtnEl = this.querySelector(".btn-save");
        const splitBtnEl = this.querySelector(".btn-split");
        const regenBtnEl = this.querySelector(".btn-regen");

        if (prevBtn) prevBtn.addEventListener("click", () => this._prevChapter());
        if (nextBtn) nextBtn.addEventListener("click", () => this._nextChapter());
        if (editBtnEl) editBtnEl.addEventListener("click", () => this._toggleEdit());
        if (saveBtnEl) saveBtnEl.addEventListener("click", () => this._saveContent());
        if (splitBtnEl) splitBtnEl.addEventListener("click", () => this._toggleSplitView());
        if (regenBtnEl) regenBtnEl.addEventListener("click", () => this._onRegenerate());

        // 绑定划词搜索事件（仅在非编辑模式下）
        this._bindSelectionEvents();

        // 恢复悬浮元素（innerHTML 渲染后需重新 append）
        if (this._selectionToolbar) this.appendChild(this._selectionToolbar);
        if (this._resultCard) this.appendChild(this._resultCard);
    }

    _renderSplitReaders(sceneTexts) {
        return sceneTexts
            .map((text, i) => {
                const preview = text.length > 120 ? text.slice(0, 120) + "..." : text;
                return `
                <div class="scene-card">
                    <div class="scene-card-header">
                        <span class="scene-index">场景 ${i + 1}</span>
                        <span class="scene-wordcount">${text.length} 字</span>
                    </div>
                    <div class="scene-card-body">
                        ${this._formatText(preview)}
                    </div>
                </div>
            `;
            })
            .join("");
    }

    _renderSplitEditors(sceneTexts) {
        return sceneTexts
            .map((text, i) => {
                const val = this._escapeHtml(this._sceneEditors[i] ?? text);
                return `
                <div class="scene-card editing">
                    <div class="scene-card-header">
                        <span class="scene-index">场景 ${i + 1}</span>
                    </div>
                    <textarea class="scene-editor" data-index="${i}" placeholder="场景 ${i + 1} 内容...">${val}</textarea>
                </div>
            `;
            })
            .join("");
    }

    _escapeHtml(text) {
        if (!text) return "";
        const div = document.createElement("div");
        div.textContent = text;
        return div.innerHTML;
    }

    _formatText(text) {
        if (!text) return "";
        return text
            .split(/\n\n+/)
            .map((p) => `<p>${this._escapeHtml(p).replace(/\n/g, "<br>")}</p>`)
            .join("");
    }

    // --------------------------------------------------------
    // 重新生成
    // --------------------------------------------------------
    _onRegenerate() {
        if (!this._projectId) return;
        const confirmed = confirm(
            `确定重新生成本章？当前草稿将被覆盖。\n\n` +
            `SceneShowrunner 会保留已有节拍表，仅从 Writer 步骤重新生成。`
        );
        if (!confirmed) return;

        const instruction = `拒绝当前草稿，要求 SceneShowrunner 从 checkpoint 的 beatsheet 步骤重新生成本章`;

        if (this._api) {
            this._api.sendCommand(this._projectId, instruction)
                .then(() => {
                    // 发送成功后，通知外部重新连接 SSE
                    this.dispatchEvent(new CustomEvent("regenerate-requested", {
                        detail: { projectId: this._projectId, chapterNumber: this._chapterNumber },
                        bubbles: true,
                    }));
                })
                .catch((err) => {
                    alert("发送重新生成指令失败: " + err.message);
                });
        }
    }

    // --------------------------------------------------------
    // 划词搜索 KB
    // --------------------------------------------------------
    _bindSelectionEvents() {
        if (this._editing) return; // 编辑模式下不启用划词搜索
        const chapterBody = this.querySelector(".chapter-body");
        if (!chapterBody) return;

        chapterBody.addEventListener("mouseup", (e) => this._onTextSelected(e));

        // 点击外部隐藏工具条和卡片
        this._outsideClickHandler = (e) => {
            if (!this.contains(e.target)) {
                this._hideSelectionToolbar();
                this._hideResultCard();
            }
        };
        document.addEventListener("mousedown", this._outsideClickHandler);
    }

    _onTextSelected(e) {
        // 如果点击在悬浮元素上，忽略
        if (e.target.closest(".kb-selection-toolbar") || e.target.closest(".kb-result-card")) {
            return;
        }

        const selection = window.getSelection();
        const text = (selection?.toString() || "").trim();
        if (!text || text.length < 2) {
            this._hideSelectionToolbar();
            return;
        }

        const range = selection.getRangeAt(0);
        const rect = range.getBoundingClientRect();
        this._showSelectionToolbar(text, rect);
    }

    _showSelectionToolbar(text, rect) {
        this._hideSelectionToolbar();
        this._hideResultCard();

        const toolbar = document.createElement("div");
        toolbar.className = "kb-selection-toolbar";
        toolbar.innerHTML = `
            <button data-type="character" title="查询角色">🔍 查角色</button>
            <button data-type="location" title="查询地点">🔍 查地点</button>
            <button data-type="foreshadowing" title="查询伏笔">🔍 查伏笔</button>
        `;
        toolbar.querySelectorAll("button").forEach((btn) => {
            btn.addEventListener("click", () => {
                this._searchKB(btn.dataset.type, text);
                this._hideSelectionToolbar();
            });
        });

        this.appendChild(toolbar);
        this._positionFloatingElement(toolbar, rect, "above");
        this._selectionToolbar = toolbar;

        // 5 秒后自动消失
        this._toolbarTimeout = setTimeout(() => this._hideSelectionToolbar(), 5000);
    }

    _hideSelectionToolbar() {
        if (this._toolbarTimeout) {
            clearTimeout(this._toolbarTimeout);
            this._toolbarTimeout = null;
        }
        if (this._selectionToolbar) {
            this._selectionToolbar.remove();
            this._selectionToolbar = null;
        }
    }

    _positionFloatingElement(el, rect, position = "above") {
        const hostRect = this.getBoundingClientRect();
        const elRect = el.getBoundingClientRect();

        let left = rect.left - hostRect.left + (rect.width / 2) - (elRect.width / 2);
        let top;
        if (position === "above") {
            top = rect.top - hostRect.top - elRect.height - 8;
        } else {
            top = rect.bottom - hostRect.top + 8;
        }

        // 边界限制
        left = Math.max(4, Math.min(left, hostRect.width - elRect.width - 4));
        top = Math.max(4, top);

        el.style.left = `${left}px`;
        el.style.top = `${top}px`;
    }

    async _searchKB(type, query) {
        if (!this._api || !this._projectId) return;

        let result;
        try {
            if (type === "character") {
                result = await this._api.searchCharacter(this._projectId, query);
            } else if (type === "location") {
                result = await this._api.searchLocation(this._projectId, query);
            } else if (type === "foreshadowing") {
                result = await this._api.searchForeshadowing(this._projectId, query);
            }
        } catch (err) {
            result = { found: false, message: "查询失败" };
        }

        // 用选区位置作为卡片锚点（选区可能已消失，用鼠标位置兜底）
        const anchorRect = this._lastSelectionRect || { left: 100, top: 100, width: 0, height: 0 };
        this._showResultCard(type, result, anchorRect);
    }

    _showResultCard(type, result, anchorRect) {
        this._hideResultCard();

        const card = document.createElement("div");
        card.className = "kb-result-card";

        if (!result || !result.found) {
            card.innerHTML = `
                <div class="kb-result-header">查询结果</div>
                <div class="kb-result-body">
                    <div class="kb-result-empty">${this._escapeHtml(result?.message || "暂无记录")}</div>
                </div>
            `;
        } else if (result.multiple) {
            const items = (result.candidates || []).map((c) =>
                `• ${this._escapeHtml(c.name || c.id || "未知")}`
            ).join("<br>");
            card.innerHTML = `
                <div class="kb-result-header">找到多个匹配</div>
                <div class="kb-result-body">${items}</div>
            `;
        } else {
            card.innerHTML = this._renderCardContent(type, result.data);
        }

        this.appendChild(card);
        this._positionFloatingElement(card, anchorRect, "below");
        this._resultCard = card;

        // 5 秒后自动消失
        this._cardTimeout = setTimeout(() => this._hideResultCard(), 5000);
    }

    _hideResultCard() {
        if (this._cardTimeout) {
            clearTimeout(this._cardTimeout);
            this._cardTimeout = null;
        }
        if (this._resultCard) {
            this._resultCard.remove();
            this._resultCard = null;
        }
    }

    _renderCardContent(type, data) {
        if (type === "character") return this._renderCharacterCard(data);
        if (type === "location") return this._renderLocationCard(data);
        if (type === "foreshadowing") return this._renderForeshadowingCard(data);
        return `<div class="kb-result-empty">未知类型</div>`;
    }

    _renderCharacterCard(data) {
        const name = this._escapeHtml(data.name || "未知角色");
        const realm = this._escapeHtml(data.cultivation_realm || data.current_realm || "");
        const emotion = this._escapeHtml(data.current_emotion || "");
        const voice = this._escapeHtml((data.voice_keywords || []).join(", "));
        const goals = this._escapeHtml((data.active_goals || []).join(", "));
        const personality = this._escapeHtml(data.personality_core || "");

        return `
            <div class="kb-result-header">${name} ${realm ? `<span class="kb-result-tag">${realm}</span>` : ""}</div>
            <div class="kb-result-body">
                ${personality ? `<div class="kb-result-row"><span class="kb-result-label">性格</span><span class="kb-result-value">${personality}</span></div>` : ""}
                ${emotion ? `<div class="kb-result-row"><span class="kb-result-label">情绪</span><span class="kb-result-value">${emotion}</span></div>` : ""}
                ${voice ? `<div class="kb-result-row"><span class="kb-result-label">声线</span><span class="kb-result-value">${voice}</span></div>` : ""}
                ${goals ? `<div class="kb-result-row"><span class="kb-result-label">目标</span><span class="kb-result-value">${goals}</span></div>` : ""}
            </div>
        `;
    }

    _renderLocationCard(data) {
        const name = this._escapeHtml(data.name || data.node_id || "未知地点");
        const nodeType = this._escapeHtml(data.node_type || "");
        const scale = this._escapeHtml(data.scale || "");
        const desc = this._escapeHtml(data.description || data.short_description || "");
        const faction = this._escapeHtml((data.faction_control || []).join(", "));

        return `
            <div class="kb-result-header">${name} ${nodeType ? `<span class="kb-result-tag">${nodeType}</span>` : ""}</div>
            <div class="kb-result-body">
                ${scale ? `<div class="kb-result-row"><span class="kb-result-label">规模</span><span class="kb-result-value">${scale}</span></div>` : ""}
                ${faction ? `<div class="kb-result-row"><span class="kb-result-label">势力</span><span class="kb-result-value">${faction}</span></div>` : ""}
                ${desc ? `<div class="kb-result-desc">${desc}</div>` : ""}
            </div>
        `;
    }

    _renderForeshadowingCard(data) {
        // data 可能是数组（最多3条）
        const items = Array.isArray(data) ? data : [data];
        const sections = items.map((item) => {
            const desc = this._escapeHtml(item.description || "");
            const status = this._escapeHtml(item.status || "unknown");
            const triggerRange = this._escapeHtml(item.trigger_range || "");
            const statusClass = status === "triggered" ? "resolved" : "planted";
            return `
                <div class="kb-fs-item">
                    <div class="kb-fs-status">
                        <span class="kb-result-tag ${statusClass}">${status}</span>
                        ${triggerRange ? `<span class="kb-result-tag">${triggerRange}</span>` : ""}
                    </div>
                    <div class="kb-result-desc">${desc}</div>
                </div>
            `;
        }).join("");

        return `
            <div class="kb-result-header">伏笔</div>
            <div class="kb-result-body">${sections}</div>
        `;
    }
}

customElements.define("chapter-reader", ChapterReader);
