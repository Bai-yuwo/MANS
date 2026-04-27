/**
 * chapter-reader.js — 章节阅读/编辑组件
 *
 * 展示当前章节的拼接正文（final > draft），支持翻页和编辑保存。
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
        this._api = typeof MANSApiClient !== "undefined" ? new MANSApiClient() : null;
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
        } catch (err) {
            this._content = {
                chapter_number: this._chapterNumber,
                title: `第${this._chapterNumber}章`,
                full_text: "",
                scene_texts: [],
                is_final: false,
                error: err.message,
            };
        }

        this._loading = false;
        this._render();
    }

    // --------------------------------------------------------
    // 保存
    // --------------------------------------------------------
    async _saveContent() {
        if (!this._projectId || !this._api) return;
        const textarea = this.querySelector(".chapter-editor");
        if (!textarea) return;

        const fullText = textarea.value;
        const saveBtn = this.querySelector(".btn-save");
        if (saveBtn) {
            saveBtn.textContent = "保存中...";
            saveBtn.disabled = true;
        }

        try {
            await this._api.saveChapterContent(this._projectId, this._chapterNumber, {
                full_text: fullText,
                scene_texts: this._content?.scene_texts || [],
            });
            this._editing = false;
            if (this._content) {
                this._content.full_text = fullText;
            }
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

        let bodyHtml = "";
        if (this._loading) {
            bodyHtml = `<div class="chapter-loading">加载中...</div>`;
        } else if (hasError) {
            bodyHtml = `<div class="chapter-error">${this._escapeHtml(hasError)}</div>`;
        } else if (this._editing) {
            bodyHtml = `
                <textarea class="chapter-editor" placeholder="章节内容...">${this._escapeHtml(fullText)}</textarea>
            `;
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

        this.innerHTML = `
            <div class="chapter-reader">
                <div class="chapter-header">
                    <div class="chapter-nav">
                        <button class="btn-nav" ${this._chapterNumber <= 1 ? 'disabled' : ''}>上一章</button>
                        <span class="chapter-title">${this._escapeHtml(title)}</span>
                        <button class="btn-nav">下一章</button>
                    </div>
                    <div class="chapter-actions">
                        ${statusBadge}
                        ${editBtn}
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

        if (prevBtn) prevBtn.addEventListener("click", () => this._prevChapter());
        if (nextBtn) nextBtn.addEventListener("click", () => this._nextChapter());
        if (editBtnEl) editBtnEl.addEventListener("click", () => this._toggleEdit());
        if (saveBtnEl) saveBtnEl.addEventListener("click", () => this._saveContent());
    }

    _escapeHtml(text) {
        if (!text) return "";
        const div = document.createElement("div");
        div.textContent = text;
        return div.innerHTML;
    }

    _formatText(text) {
        if (!text) return "";
        // 将换行转为 <p> 段落
        return text
            .split(/\n\n+/)
            .map((p) => `<p>${this._escapeHtml(p).replace(/\n/g, "<br>")}</p>`)
            .join("");
    }
}

customElements.define("chapter-reader", ChapterReader);
