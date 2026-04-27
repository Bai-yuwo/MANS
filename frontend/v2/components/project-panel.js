/**
 * project-panel.js — 左栏项目导航 Web Component
 *
 * 职责:
 *   - 展示项目列表
 *   - 创建新项目
 *   - 切换当前项目(触发 project-selected 自定义事件)
 */

class ProjectPanel extends HTMLElement {
    constructor() {
        super();
        this.client = new MANSApiClient();
        this.projects = [];
        this.selectedProjectId = null;
    }

    connectedCallback() {
        this.innerHTML = `
            <div class="panel-title">项目导航</div>
            <div class="panel-content">
                <button class="btn btn-primary" id="btn-new-project" style="width:100%;margin-bottom:12px;">+ 新建项目</button>
                <div id="project-list"></div>
            </div>
            <div id="new-project-form" class="hidden" style="padding:12px;border-top:1px solid var(--border);">
                <div class="form-group">
                    <label>作品名称</label>
                    <input type="text" id="inp-name" placeholder="输入作品名称">
                </div>
                <div class="form-group">
                    <label>题材</label>
                    <input type="text" id="inp-genre" placeholder="玄幻" value="玄幻">
                </div>
                <div class="form-group">
                    <label>核心创意</label>
                    <textarea id="inp-core-idea" placeholder="一句话概括故事灵魂"></textarea>
                </div>
                <div class="form-group">
                    <label>主角起点</label>
                    <input type="text" id="inp-protagonist" placeholder="如:山村少年，天生废灵根">
                </div>
                <button class="btn btn-primary" id="btn-create" style="width:100%;">创建</button>
                <button class="btn btn-secondary" id="btn-cancel" style="width:100%;margin-top:6px;">取消</button>
            </div>
        `;

        this.querySelector("#btn-new-project").addEventListener("click", () => this._showForm());
        this.querySelector("#btn-create").addEventListener("click", () => this._createProject());
        this.querySelector("#btn-cancel").addEventListener("click", () => this._hideForm());

        this.loadProjects();
    }

    async loadProjects() {
        try {
            const data = await this.client.listProjects();
            this.projects = data.projects || [];
            this._renderList();

            // 刷新后自动恢复上次选中的项目
            const lastId = localStorage.getItem("mans:lastProjectId");
            if (lastId && this.projects.find(p => p.id === lastId)) {
                this.selectProject(lastId);
            }
        } catch (e) {
            console.error("加载项目列表失败", e);
        }
    }

    _renderList() {
        const container = this.querySelector("#project-list");
        if (!this.projects.length) {
            container.innerHTML = `<div style="color:var(--text-muted);font-size:12px;text-align:center;padding:20px;">暂无项目</div>`;
            return;
        }
        container.innerHTML = this.projects.map(p => `
            <div class="card ${p.id === this.selectedProjectId ? 'active' : ''}" data-id="${p.id}">
                <div style="display:flex;justify-content:space-between;align-items:flex-start;">
                    <div class="card-title" style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;">${this._esc(p.name)}</div>
                    <button class="btn-delete-project" data-id="${p.id}" title="删除项目">×</button>
                </div>
                <div class="card-meta">
                    <span class="stage-badge ${p.stage?.toLowerCase()}">${p.stage}</span>
                    ${p.genre} · 第${p.current_chapter}章
                </div>
            </div>
        `).join("");

        container.querySelectorAll(".card").forEach(card => {
            card.addEventListener("click", (e) => {
                // 点击删除按钮时不触发项目切换
                if (e.target.closest('.btn-delete-project')) return;
                const pid = card.dataset.id;
                this.selectProject(pid);
            });
        });

        container.querySelectorAll(".btn-delete-project").forEach(btn => {
            btn.addEventListener("click", (e) => {
                e.stopPropagation();
                const pid = btn.dataset.id;
                this._deleteProject(pid);
            });
        });
    }

    selectProject(projectId) {
        this.selectedProjectId = projectId;
        this._renderList();
        const project = this.projects.find(p => p.id === projectId);
        this.dispatchEvent(new CustomEvent("project-selected", {
            detail: { projectId, project },
            bubbles: true,
        }));
    }

    _showForm() {
        this.querySelector("#new-project-form").classList.remove("hidden");
    }

    _hideForm() {
        this.querySelector("#new-project-form").classList.add("hidden");
    }

    async _createProject() {
        const name = this.querySelector("#inp-name").value.trim();
        const genre = this.querySelector("#inp-genre").value.trim();
        const core_idea = this.querySelector("#inp-core-idea").value.trim();
        const protagonist_seed = this.querySelector("#inp-protagonist").value.trim();

        if (!name) { alert("请输入作品名称"); return; }

        try {
            const result = await this.client.createProject({
                name, genre, core_idea, protagonist_seed,
            });
            this._hideForm();
            await this.loadProjects();
            this.selectProject(result.project_id);
        } catch (e) {
            alert("创建失败: " + e.message);
        }
    }

    async _deleteProject(projectId) {
        const project = this.projects.find(p => p.id === projectId);
        const name = project ? project.name : projectId;

        const confirmed = confirm(`确定要删除项目「${name}」吗？\n\n此操作不可恢复，所有世界观、角色、大纲和章节数据都将被永久删除。`);
        if (!confirmed) return;

        try {
            await this.client.deleteProject(projectId);
            // 如果删除的是当前选中的项目，清除选中状态
            if (this.selectedProjectId === projectId) {
                this.selectedProjectId = null;
                localStorage.removeItem("mans:lastProjectId");
                this.dispatchEvent(new CustomEvent("project-selected", {
                    detail: { projectId: null, project: null },
                    bubbles: true,
                }));
            }
            await this.loadProjects();
        } catch (e) {
            alert("删除失败: " + e.message);
        }
    }

    _esc(s) {
        const div = document.createElement("div");
        div.textContent = s;
        return div.innerHTML;
    }
}

customElements.define("project-panel", ProjectPanel);
