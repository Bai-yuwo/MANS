/**
 * MANS Frontend —— 前端交互逻辑主文件
 *
 * 本文件是 MANS 单页应用（SPA）的前端核心，负责：
 *     1. 全局状态管理：当前项目、章节、场景、生成状态等。
 *     2. 项目生命周期：创建、列表展示、打开、删除。
 *     3. 初始化流程向导：Bible → 人物 → 大纲，三步骤顺序解锁。
 *     4. 弧线与章节规划：弧线列表、创建、智能推荐、章节规划生成。
 *     5. 写作界面：场景生成（含一键全章）、流式输出、编辑、重写、确认完稿。
 *     6. 知识库查看：Bible、人物、大纲、伏笔的可视化展示。
 *     7. 实时监控：SSE 日志流连接、过滤、自动滚动。
 *     8. Issue Pool：异步更新通知、悬浮角标、面板展示。
 *
 * 架构说明：
 *     - 所有函数直接挂载到 window 对象，供 HTML 内联事件调用。
 *     - API 通信统一通过 apiRequest() 封装，支持动态 API_BASE 与指数退避重试。
 *     - SSE 流式输出通过原生 fetch + ReadableStream 解析，不依赖 EventSource API
 *       （因需支持自定义请求头与 POST 方法）。
 *     - 状态持久化使用 localStorage，支持页面刷新后恢复项目与面板状态。
 */

// ============================================================
// 全局状态 (AppState)
// ============================================================

/**
 * AppState —— 前端运行时全局状态对象。
 *
 * 字段说明：
 *     currentProject: 当前选中项目的 UUID，从 localStorage 恢复或 null。
 *     currentChapter: 当前正在写作的章节编号，默认 1，持久化到 localStorage。
 *     currentScene:   当前正在生成的场景序号（0-based）。
 *     isGenerating:   是否正在进行 LLM 生成，用于禁用/启用按钮。
 *     projectInitialized: 当前项目是否已完成初始化（Bible + 人物 + 大纲）。
 *     currentAbortController: 当前活跃生成任务的 AbortController，用于中断流式请求。
 */
const AppState = {
    currentProject: localStorage.getItem('mans_current_project') || null,
    currentChapter: parseInt(localStorage.getItem('mans_current_chapter') || '1', 10),
    currentScene: 0,
    isGenerating: false,
    projectInitialized: false,
    currentAbortController: null
};

// ============================================================
// 设置管理
// ============================================================

/**
 * 从 localStorage 读取用户设置项。
 *
 * @param {string} key - 设置键名。
 * @param {*} defaultValue - 键不存在时的默认值。
 * @returns {*} 设置值或默认值。
 */
function getSetting(key, defaultValue) {
    const stored = localStorage.getItem('mans_settings');
    const settings = stored ? JSON.parse(stored) : {};
    return settings[key] !== undefined ? settings[key] : defaultValue;
}

/**
 * 获取 API 基础地址。
 *
 * 用户可通过设置面板配置反向代理地址。若未配置，使用相对路径（同域）。
 *
 * @returns {string} API 基础 URL，不含尾部斜杠。
 */
function getApiBase() {
    return getSetting('apiBase', '');
}

// ============================================================
// 网络请求与工具函数
// ============================================================

/**
 * 统一 API 请求封装（支持动态 API_BASE 与指数退避重试）。
 *
 * 重试策略：
 *     - 最大重试次数：由设置项 retries 控制（默认 3 次）。
 *     - 4xx 客户端错误（除 429 限流外）立即失败，不再重试。
 *     - 每次重试间隔递增：1s、2s、3s...
 *     - 自动附加 Content-Type: application/json 请求头。
 *
 * @param {string} url - API 路径（如 "/api/projects"）。
 * @param {object} options - fetch 选项对象（method、body、headers 等）。
 * @returns {Promise<object>} 解析后的 JSON 响应体。
 * @throws {Error} 所有重试耗尽后抛出最后一次错误。
 */
async function apiRequest(url, options = {}) {
    const base = getApiBase();
    const fullUrl = base ? base.replace(/\/$/, '') + (url.startsWith('/') ? url : '/' + url) : url;
    const maxRetries = getSetting('retries', 3);

    let lastError;
    for (let attempt = 0; attempt <= maxRetries; attempt++) {
        try {
            const response = await fetch(fullUrl, {
                headers: {
                    'Content-Type': 'application/json',
                    ...options.headers
                },
                ...options
            });

            if (!response.ok) {
                const error = await response.json().catch(() => ({ detail: '请求失败' }));
                const errMsg = error.detail || `HTTP ${response.status}`;
                const err = new Error(errMsg);
                err.status = response.status;
                throw err;
            }

            return response.json();
        } catch (error) {
            lastError = error;
            // 4xx 客户端错误（除 429 限流外）不重试
            if (error.status >= 400 && error.status < 500 && error.status !== 429) {
                break;
            }
            if (attempt < maxRetries) {
                await new Promise(r => setTimeout(r, 1000 * (attempt + 1)));
            }
        }
    }

    throw lastError;
}

/**
 * 显示 Toast 通知消息。
 *
 * 在屏幕右上角弹出彩色提示框，3 秒后自动滑出消失。
 * 同时向 console 输出日志，便于调试。
 *
 * @param {string} message - 通知文本。
 * @param {string} type - 类型：success（绿）/ error（红）/ warning（黄）/ info（蓝）。
 */
function showMessage(message, type = 'info') {
    console.log(`[${type}] ${message}`);

    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    toast.style.cssText = `
        position: fixed;
        top: 20px;
        right: 20px;
        padding: 16px 24px;
        border-radius: 8px;
        color: white;
        font-size: 14px;
        font-weight: 500;
        z-index: 10000;
        box-shadow: 0 4px 12px rgba(0,0,0,0.15);
        animation: slideIn 0.3s ease;
        max-width: 400px;
        word-wrap: break-word;
    `;

    const colors = {
        'success': '#10b981',
        'error': '#ef4444',
        'warning': '#f59e0b',
        'info': '#3b82f6'
    };
    toast.style.backgroundColor = colors[type] || colors['info'];

    document.body.appendChild(toast);

    setTimeout(() => {
        toast.style.animation = 'slideOut 0.3s ease';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

/**
 * 检查异步知识库更新状态（场景生成/重写完成后调用）。
 *
 * 轮询 /api/projects/.../chapters/{chapterNum}/updates 接口，
 * 若检测到新的 implicit_issues，弹出警告通知并刷新 Issue 角标。
 *
 * @param {number} chapterNum - 章节编号。
 */
async function checkAsyncUpdates(chapterNum) {
    if (!AppState.currentProject) return;
    try {
        const data = await apiRequest(
            `/api/projects/${AppState.currentProject}/chapters/${chapterNum}/updates`
        );
        if (data.has_new_issues) {
            showMessage(
                `知识库更新：检测到 ${data.implicit_issues.length} 个新问题，请查看 Issue Pool`,
                'warning'
            );
        } else if (data.updates_count > 0) {
            showMessage('知识库已同步更新', 'info');
        }
        refreshIssueBadge();
    } catch (e) {
        // 静默忽略轮询错误，避免干扰用户正常写作
    }
}

/**
 * 渲染或复用 Issue Pool 悬浮角标 DOM 元素。
 *
 * 角标固定在屏幕右下角，点击可跳转到 Issue Pool 面板。
 * 仅在项目打开且存在待处理 Issues 时显示。
 *
 * @returns {HTMLElement} 角标 DOM 元素。
 */
function renderIssueBadge() {
    let badge = document.getElementById('issue-badge');
    if (!badge) {
        badge = document.createElement('div');
        badge.id = 'issue-badge';
        badge.style.cssText = `
            position: fixed;
            bottom: 24px;
            right: 24px;
            width: 48px;
            height: 48px;
            border-radius: 50%;
            background: var(--error);
            color: white;
            display: none;
            align-items: center;
            justify-content: center;
            font-weight: bold;
            font-size: 16px;
            cursor: pointer;
            z-index: 3000;
            box-shadow: 0 4px 12px rgba(0,0,0,0.3);
            transition: transform 0.2s;
        `;
        badge.addEventListener('click', () => {
            document.querySelector('.sidebar-item[data-panel="issues"]')?.click();
        });
        badge.addEventListener('mouseenter', () => {
            badge.style.transform = 'scale(1.1)';
        });
        badge.addEventListener('mouseleave', () => {
            badge.style.transform = 'scale(1)';
        });
        document.body.appendChild(badge);
    }
    return badge;
}

/**
 * 刷新 Issue Pool 角标的数字计数。
 *
 * 从后端获取 issue 总数，更新角标显示；若数量为 0 则隐藏角标。
 * 数字上限为 99+。
 */
async function refreshIssueBadge() {
    if (!AppState.currentProject) {
        const badge = document.getElementById('issue-badge');
        if (badge) badge.style.display = 'none';
        return;
    }
    try {
        const data = await apiRequest(`/api/projects/${AppState.currentProject}/issues`);
        const count = data.total || 0;
        const badge = renderIssueBadge();
        badge.textContent = count > 99 ? '99+' : String(count);
        badge.style.display = count > 0 ? 'flex' : 'none';
    } catch (e) {
        // 忽略，避免网络波动时角标闪烁
    }
}

// 注入 Toast 所需的 CSS 动画（若尚未存在）
if (!document.getElementById('toast-styles')) {
    const style = document.createElement('style');
    style.id = 'toast-styles';
    style.textContent = `
        @keyframes slideIn {
            from { transform: translateX(400px); opacity: 0; }
            to { transform: translateX(0); opacity: 1; }
        }
        @keyframes slideOut {
            from { transform: translateX(0); opacity: 1; }
            to { transform: translateX(400px); opacity: 0; }
        }
    `;
    document.head.appendChild(style);
}

/**
 * 打开"创建项目"模态框。
 */
function openCreateModal() {
    const modal = document.getElementById('create-project-modal');
    if (modal) {
        modal.style.display = 'flex';
    }
}

/**
 * 关闭"创建项目"模态框。
 */
function closeCreateModal() {
    const modal = document.getElementById('create-project-modal');
    if (modal) {
        modal.style.display = 'none';
    }
}

/**
 * 格式化字数显示（中文习惯）。
 *
 * 超过 10000 字时以"万"为单位显示一位小数，否则显示原始数字。
 *
 * @param {number} count - 字数。
 * @returns {string} 格式化后的字符串。
 */
function formatWordCount(count) {
    if (count >= 10000) {
        return (count / 10000).toFixed(1) + '万';
    }
    return count.toString();
}

// ============================================================
// 状态持久化与导航
// ============================================================

/**
 * 更新侧边栏的项目依赖状态。
 *
 * 带有 data-requires-project="true" 的菜单项，在没有选中项目时置为 disabled。
 *
 * @param {boolean} hasProject - 是否已选中项目。
 */
function updateSidebarState(hasProject) {
    document.querySelectorAll('.sidebar-item[data-requires-project="true"]').forEach(item => {
        item.classList.toggle('disabled', !hasProject);
    });
}

/**
 * 更新顶部导航栏的项目名称显示。
 *
 * 从后端获取项目详情，将 name 显示在顶栏中央；
 * 若获取失败则回退到显示 projectId。
 *
 * @param {string|null} projectId - 项目 UUID。
 */
function updateTopBarProject(projectId) {
    const bar = document.getElementById('top-bar-project');
    const nameEl = document.getElementById('current-project-name');
    if (!bar || !nameEl) return;

    if (!projectId) {
        bar.classList.remove('visible');
        nameEl.textContent = '';
        return;
    }

    apiRequest(`/api/projects/${projectId}`).then(project => {
        nameEl.textContent = project.name || projectId;
        bar.classList.add('visible');
    }).catch(() => {
        nameEl.textContent = projectId;
        bar.classList.add('visible');
    });
}

/**
 * 渲染弧线列表到弧线规划面板。
 *
 * 按弧线序号升序排列，区分"已生成"（is_placeholder=false）与"占位符"状态，
 * 分别显示"重新生成"和"生成规划"按钮。
 *
 * @param {Array} arcs - 弧线数据数组。
 */
function renderArcList(arcs) {
    const container = document.getElementById('arc-list-dynamic');
    if (!container) return;

    if (!arcs || arcs.length === 0) {
        container.innerHTML = `<div class="panel-empty" style="min-height:120px;">
            <p class="panel-empty-text">暂无弧线，点击上方「创建新弧线」或「智能推荐」开始规划</p>
        </div>`;
        return;
    }

    arcs.sort((a, b) => (a.arc_number || 0) - (b.arc_number || 0));

    container.innerHTML = arcs.map(arc => {
        const range = arc.chapter_range || [];
        const rangeText = range.length === 2 ? `第 ${range[0]} ~ ${range[1]} 章` : '';
        const isGenerated = !arc.is_placeholder;
        const btnClass = isGenerated ? 'mans-btn' : 'mans-btn primary';
        const btnText = isGenerated ? '重新生成' : '生成规划';
        return `
            <div class="arc-card" data-arc="${arc.arc_number}">
                <div class="arc-card-title">弧线 ${arc.arc_number}${arc.title ? '：' + escapeHtml(arc.title) : ''}</div>
                <div class="arc-card-meta" style="font-size:12px;color:var(--text-secondary);margin-bottom:6px;">${escapeHtml(rangeText)}</div>
                <div class="arc-card-desc">${escapeHtml(arc.description || '')}</div>
                <div class="arc-card-actions">
                    <button class="${btnClass}" onclick="generateArc(${arc.arc_number})">${btnText}</button>
                    ${isGenerated ? '<span class="arc-status-badge">已生成</span>' : ''}
                    <button class="mans-btn danger" style="margin-left:auto;" onclick="deleteArc(${arc.arc_number})">删除</button>
                </div>
            </div>
        `;
    }).join('');
}

/**
 * 检查并刷新弧线列表状态（从后端拉取最新数据）。
 *
 * @param {string} projectId - 项目 UUID。
 */
async function checkArcStatus(projectId) {
    try {
        const data = await apiRequest(`/api/projects/${projectId}/arcs`);
        renderArcList(data.arcs || []);
    } catch (e) {
        console.error('加载弧线列表失败:', e);
        renderArcList([]);
    }
}

/**
 * 更新各面板的空状态显示。
 *
 * 无项目时显示空状态插画与提示文本；有项目时显示实际内容区域。
 */
function updatePanelEmptyStates() {
    const hasProject = !!AppState.currentProject;
    const pairs = [
        ['arc-empty', 'arc-content'],
        ['chapter-plan-empty', 'chapter-plan-content'],
        ['issues-empty', 'issues-content'],
        ['writing-empty', 'writing-content'],
        ['knowledge-empty', 'knowledge-content'],
        ['monitor-empty', 'monitor-content']
    ];

    pairs.forEach(([emptyId, contentId]) => {
        const emptyEl = document.getElementById(emptyId);
        const contentEl = document.getElementById(contentId);
        if (emptyEl) emptyEl.style.display = hasProject ? 'none' : 'flex';
        if (contentEl) contentEl.style.display = hasProject ? 'block' : 'none';
    });
}

/**
 * 返回作品列表视图，清理当前项目状态。
 *
 * 清除 localStorage 中的项目相关键，重置 UI 到初始状态。
 */
function backToWorks() {
    AppState.currentProject = null;
    AppState.currentChapter = 1;
    localStorage.removeItem('mans_current_project');
    localStorage.removeItem('mans_current_panel');
    localStorage.removeItem('mans_current_chapter');
    updateSidebarState(false);
    updateTopBarProject(null);
    updatePanelEmptyStates();
    showPanel('works');
    loadProjects();
}

// ============================================================
// 项目管理
// ============================================================

/**
 * 加载项目列表（带加载动画）。
 *
 * 调用 /api/projects 获取全部项目，通过 updateProjectList() 渲染到 DOM。
 */
async function loadProjects() {
    const container = document.getElementById('project-list');
    if (container) {
        container.innerHTML = '<div class="loading-overlay" style="position:relative;min-height:100px;display:flex;align-items:center;justify-content:center;"><div class="loading-spinner"></div><span class="loading-text">加载项目中...</span></div>';
    }

    try {
        const data = await apiRequest('/api/projects');
        const projects = data.projects || [];
        updateProjectList(projects);
    } catch (error) {
        console.error('加载项目列表失败:', error);
        if (container) {
            container.innerHTML = '<p class="empty" style="text-align:center;padding:20px;">加载项目失败，请刷新重试</p>';
        }
    }
}

/**
 * 渲染项目列表卡片到 DOM。
 *
 * 每个项目卡片展示：名称、题材标签、状态（带颜色）、当前章节、操作按钮（打开/删除）。
 *
 * @param {Array} projects - 项目元数据数组。
 */
function updateProjectList(projects) {
    const container = document.getElementById('project-list');
    if (!container) return;

    if (projects.length === 0) {
        container.innerHTML = '<div class="panel-empty" style="padding:40px;"><p class="panel-empty-text">暂无项目，请创建新作品</p></div>';
        return;
    }

    container.innerHTML = projects.map(project => {
        const statusColor = project.status === 'completed' ? 'var(--success)' :
                           project.status === 'writing' ? 'var(--primary-light)' : 'var(--warning)';
        return `
        <div class="project-card" data-id="${escapeHtml(project.id)}">
            <h3>${escapeHtml(project.name)}</h3>
            <span class="genre">${escapeHtml(project.genre)}</span>
            <div class="meta-row">
                <span class="status" style="color:${statusColor}">${escapeHtml(getStatusText(project.status))}</span>
                <span class="chapter">当前章节: ${escapeHtml(String(project.current_chapter))}</span>
            </div>
            <div class="actions">
                <button onclick="openProject('${escapeJsString(project.id)}')" class="mans-btn primary">打开</button>
                <button onclick="deleteProject('${escapeJsString(project.id)}')" class="mans-btn danger">删除</button>
            </div>
        </div>
    `;
    }).join('');
}

/**
 * 将项目状态码转换为中文可读文本。
 *
 * @param {string} status - 状态码：initializing / writing / paused / completed。
 * @returns {string} 中文状态文本。
 */
function getStatusText(status) {
    const statusMap = {
        'initializing': '初始化中',
        'writing': '写作中',
        'paused': '已暂停',
        'completed': '已完成'
    };
    return statusMap[status] || status;
}

/**
 * 创建新项目。
 *
 * 提交表单数据到 /api/projects，成功后刷新项目列表。
 * 提交期间禁用提交按钮并将文本改为"创建中..."，防止重复提交。
 *
 * @param {object} projectData - 项目数据字典。
 * @returns {Promise<string>} 新项目的 project_id。
 * @throws {Error} 创建失败时抛出。
 */
async function createProject(projectData) {
    try {
        const submitBtn = document.querySelector('#create-project-form button[type="submit"]');
        if (submitBtn) {
            submitBtn.disabled = true;
            submitBtn.textContent = '创建中...';
        }

        const result = await apiRequest('/api/projects', {
            method: 'POST',
            body: JSON.stringify(projectData)
        });

        showMessage('项目创建成功！', 'success');
        await loadProjects();
        return result.project_id;

    } catch (error) {
        showMessage('创建项目失败: ' + error.message, 'error');
        throw error;
    } finally {
        const submitBtn = document.querySelector('#create-project-form button[type="submit"]');
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.textContent = '创建';
        }
    }
}

/**
 * 删除项目（需用户二次确认）。
 *
 * 若删除的是当前打开项目，自动返回作品列表；否则仅刷新列表。
 *
 * @param {string} projectId - 项目 UUID。
 */
async function deleteProject(projectId) {
    if (!confirm('确定要删除这个项目吗？此操作不可恢复。')) {
        return;
    }

    try {
        await apiRequest(`/api/projects/${projectId}`, {
            method: 'DELETE'
        });

        showMessage('项目已删除', 'success');
        if (AppState.currentProject === projectId) {
            backToWorks();
        } else {
            await loadProjects();
        }

    } catch (error) {
        showMessage('删除项目失败: ' + error.message, 'error');
    }
}

/**
 * 打开项目并进入对应界面。
 *
 * 流程：
 *     1. 更新全局状态与 localStorage。
 *     2. 启用侧边栏项目相关菜单。
 *     3. 查询项目初始化状态。
 *     4. 未初始化 → 进入初始化向导面板；已初始化 → 进入写作面板。
 *     5. 刷新 Issue Pool 角标。
 *
 * @param {string} projectId - 项目 UUID。
 * @param {object} options - 可选参数，skipPanelSwitch=true 时不自动切换面板（用于页面恢复）。
 */
async function openProject(projectId, options = {}) {
    AppState.currentProject = projectId;
    localStorage.setItem('mans_current_project', projectId);
    updateSidebarState(true);
    updateTopBarProject(projectId);
    updatePanelEmptyStates();

    try {
        const status = await apiRequest(`/api/projects/${projectId}/status`);
        AppState.projectInitialized = status.initialized;

        if (!options.skipPanelSwitch) {
            if (!status.initialized) {
                showPanel('initialization-panel');
                await checkInitializationStatus(projectId);
            } else {
                showPanel('writing-panel');
                await loadWritingInterface(projectId);
            }
        }

        refreshIssueBadge();

    } catch (error) {
        showMessage('打开项目失败: ' + error.message, 'error');
    }
}

// ============================================================
// 初始化流程
// ============================================================

/**
 * 检查并更新初始化向导的各步骤状态。
 *
 * 从后端获取 has_bible / has_characters / has_outline / initialized，
 * 更新三个步骤（bible-step / character-step / outline-step）的完成标记与按钮禁用状态。
 * 若全部完成且用户仍在向导页，自动进入写作界面。
 *
 * @param {string} projectId - 项目 UUID。
 */
async function checkInitializationStatus(projectId) {
    const steps = ['bible-step', 'character-step', 'outline-step'];

    try {
        const status = await apiRequest(`/api/projects/${projectId}/status`);

        // 更新步骤完成标记
        updateInitStepStatus('bible-step', status.has_bible);
        updateInitStepStatus('character-step', status.has_characters);
        updateInitStepStatus('outline-step', status.has_outline);

        // 移除所有步骤中的加载动画
        steps.forEach(stepId => {
            const step = document.getElementById(stepId);
            if (step) {
                const spinners = step.querySelectorAll('.loading-spinner');
                spinners.forEach(s => s.remove());
            }
        });

        // 根据完成状态禁用/启用按钮（必须按顺序完成）
        const bibleBtn = document.getElementById('generate-bible-btn');
        const charBtn = document.getElementById('generate-characters-btn');
        const outlineBtn = document.getElementById('generate-outline-btn');
        const enterWritingBtn = document.getElementById('enter-writing-btn');

        if (bibleBtn) {
            bibleBtn.disabled = status.has_bible;
        }

        if (charBtn) {
            charBtn.disabled = !status.has_bible || status.has_characters;
        }

        if (outlineBtn) {
            outlineBtn.disabled = !status.has_characters || status.has_outline;
        }

        // 全部完成时显示"进入写作"按钮
        if (status.initialized && enterWritingBtn) {
            enterWritingBtn.style.display = 'inline-flex';
        } else if (enterWritingBtn) {
            enterWritingBtn.style.display = 'none';
        }

        AppState.projectInitialized = status.initialized;

        // 若初始化刚完成且用户仍在向导页，自动跳转写作界面
        const initPanel = document.getElementById('initialization-panel');
        if (status.initialized && initPanel && initPanel.classList.contains('active')) {
            showPanel('writing-panel');
            loadWritingInterface(projectId);
        }

        console.log('初始化状态已更新:', {
            has_bible: status.has_bible,
            has_characters: status.has_characters,
            has_outline: status.has_outline,
            initialized: status.initialized
        });

    } catch (error) {
        console.error('检查初始化状态失败:', error);
        steps.forEach(stepId => {
            const step = document.getElementById(stepId);
            if (step) {
                const spinners = step.querySelectorAll('.loading-spinner');
                spinners.forEach(s => s.remove());
            }
        });
    }
}

/**
 * 更新单个初始化步骤的 UI 状态。
 *
 * @param {string} stepId - 步骤元素 ID。
 * @param {boolean} completed - 是否已完成。
 */
function updateInitStepStatus(stepId, completed) {
    const step = document.getElementById(stepId);
    if (step) {
        step.classList.toggle('completed', completed);
        step.classList.toggle('pending', !completed);
    }
}

/**
 * 触发 Bible 生成。
 *
 * 若存在流式生成面板（startStreamingGeneration），使用 SSE 流式版本；
 * 否则回退到传统 POST 接口。生成完成后刷新初始化状态。
 */
async function generateBible() {
    if (!AppState.currentProject) {
        showMessage('请先选择一个项目', 'warning');
        return;
    }

    const btn = document.getElementById('generate-bible-btn');
    if (btn) {
        btn.disabled = true;
        btn.textContent = '生成中...';
    }

    try {
        if (typeof startStreamingGeneration === 'function') {
            await startStreamingGeneration('bible');
            showMessage('Bible 生成成功！', 'success');
        } else {
            const result = await apiRequest(
                `/api/projects/${AppState.currentProject}/generate/bible`,
                { method: 'POST' }
            );
            showMessage('Bible 生成成功！', 'success');
            displayBible(result.data);
        }

        await loadAndDisplayBible();
    } catch (error) {
        showMessage('生成 Bible 失败: ' + error.message, 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = '生成 Bible';
        }
        await checkInitializationStatus(AppState.currentProject);
    }
}

/**
 * 从后端加载 Bible 数据并展示。
 */
async function loadAndDisplayBible() {
    try {
        const bible = await apiRequest(`/api/projects/${AppState.currentProject}/bible`);
        if (bible && !bible.error) {
            displayBible(bible);
        }
    } catch (error) {
        console.error('加载Bible失败:', error);
    }
}

/**
 * 将 Bible 数据渲染为可折叠的 HTML 展示。
 *
 * @param {object} bibleData - Bible 字典数据。
 */
function displayBible(bibleData) {
    const container = document.getElementById('bible-display');
    if (!container) return;

    container.innerHTML = `
        <h3>${escapeHtml(bibleData.world_name || '世界观设定')}</h3>
        <p>${escapeHtml(bibleData.world_description || '')}</p>
        <div class="bible-sections">
            <details>
                <summary>战力体系</summary>
                <pre>${escapeHtml(JSON.stringify(bibleData.combat_system, null, 2))}</pre>
            </details>
            <details>
                <summary>世界规则 (${bibleData.world_rules?.length || 0}条)</summary>
                <ul>
                    ${(bibleData.world_rules || []).map(r => `<li>${escapeHtml(r.content)}</li>`).join('')}
                </ul>
            </details>
            <details>
                <summary>势力分布</summary>
                <ul>
                    ${(bibleData.factions || []).map(f => `<li>${escapeHtml(f.name)}: ${escapeHtml(f.description)}</li>`).join('')}
                </ul>
            </details>
        </div>
    `;
}

/**
 * 触发人物设定生成。
 *
 * 前置条件：Bible 已生成。生成完成后刷新初始化状态。
 */
async function generateCharacters() {
    if (!AppState.currentProject) {
        showMessage('请先选择一个项目', 'warning');
        return;
    }

    const btn = document.getElementById('generate-characters-btn');
    if (btn) {
        btn.disabled = true;
        btn.textContent = '生成中...';
    }

    try {
        if (typeof startStreamingGeneration === 'function') {
            await startStreamingGeneration('characters');
            showMessage('人物生成成功！', 'success');
        } else {
            await apiRequest(
                `/api/projects/${AppState.currentProject}/generate/characters`,
                { method: 'POST' }
            );
            showMessage('人物生成成功！', 'success');
        }
    } catch (error) {
        showMessage('生成人物失败: ' + error.message, 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = '生成人物';
        }
        await checkInitializationStatus(AppState.currentProject);
    }
}

/**
 * 触发全局大纲生成。
 *
 * 前置条件：Bible 和人物设定已生成。生成完成后刷新初始化状态。
 */
async function generateOutline() {
    if (!AppState.currentProject) {
        showMessage('请先选择一个项目', 'warning');
        return;
    }

    const btn = document.getElementById('generate-outline-btn');
    if (btn) {
        btn.disabled = true;
        btn.textContent = '生成中...';
    }

    try {
        if (typeof startStreamingGeneration === 'function') {
            await startStreamingGeneration('outline');
            showMessage('大纲生成成功！', 'success');
        } else {
            await apiRequest(
                `/api/projects/${AppState.currentProject}/generate/outline`,
                { method: 'POST' }
            );
            showMessage('大纲生成成功！', 'success');
        }
    } catch (error) {
        showMessage('生成大纲失败: ' + error.message, 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = '生成大纲';
        }
        await checkInitializationStatus(AppState.currentProject);
    }
}

// ============================================================
// 写作界面
// ============================================================

/**
 * 加载写作界面数据。
 *
 * 获取项目当前章节号，加载章节规划与导出列表。
 *
 * @param {string} projectId - 项目 UUID。
 */
async function loadWritingInterface(projectId) {
    try {
        const project = await apiRequest(`/api/projects/${projectId}`);
        AppState.currentChapter = project.current_chapter || AppState.currentChapter || 1;
        localStorage.setItem('mans_current_chapter', String(AppState.currentChapter));
        await loadChapterPlan(projectId, AppState.currentChapter);
        await loadExports();
    } catch (error) {
        console.error('加载写作界面失败:', error);
    }
}

/**
 * 加载指定章节的规划并展示。
 *
 * 规划加载成功后，自动尝试加载已保存的草稿正文填充到场景卡片。
 *
 * @param {string} projectId - 项目 UUID。
 * @param {number} chapterNum - 章节编号。
 */
async function loadChapterPlan(projectId, chapterNum) {
    try {
        const plan = await apiRequest(
            `/api/projects/${projectId}/chapters/${chapterNum}/plan`
        );
        displayChapterPlan(plan);
        // 加载已保存的草稿正文（若存在）
        await loadChapterDraft(projectId, chapterNum);
    } catch (error) {
        console.log('章节规划不存在:', error);
        const container = document.getElementById('chapter-plan-display');
        if (container) {
            container.innerHTML = `
                <div class="panel-empty">
                    <p class="panel-empty-text">第${escapeHtml(String(chapterNum))}章规划不存在，请先生成弧线规划</p>
                </div>
            `;
        }
    }
}

/**
 * 加载章节草稿正文并填充到各场景卡片。
 *
 * 遍历草稿中的 scenes 数组，将每个 scene.text 写入对应 scene-content-{index} 元素。
 * 404 表示草稿尚未生成，属于正常情况，静默处理。
 *
 * @param {string} projectId - 项目 UUID。
 * @param {number} chapterNum - 章节编号。
 */
async function loadChapterDraft(projectId, chapterNum) {
    try {
        const draft = await apiRequest(
            `/api/projects/${projectId}/chapters/${chapterNum}/draft`
        );
        const scenes = draft.scenes || [];
        for (const scene of scenes) {
            const contentDiv = document.getElementById(`scene-content-${scene.scene_index}`);
            if (contentDiv && scene.text) {
                contentDiv.innerHTML = `<div class="generated-text">${escapeHtml(formatNovelText(scene.text))}</div>`;
            }
        }
    } catch (error) {
        // 404 表示草稿不存在，属于正常情况
        if (error.status !== 404) {
            console.error('加载章节草稿失败:', error);
        }
    }
}

/**
 * 渲染章节规划到写作面板。
 *
 * @param {object} plan - 章节规划数据。
 */
function displayChapterPlan(plan) {
    const container = document.getElementById('chapter-plan-display');
    if (!container) return;

    container.innerHTML = buildChapterPlanHtml(plan, true);
}

/**
 * 构建章节规划的 HTML 字符串（写作面板与章节规划面板复用）。
 *
 * @param {object} plan - 章节规划数据。
 * @param {boolean} showWritingActions - 是否显示生成/编辑/探针/重写按钮（写作面板需要）。
 * @returns {string} HTML 字符串。
 */
function buildChapterPlanHtml(plan, showWritingActions = false) {
    const scenes = plan.scenes || [];
    const sceneCards = scenes.map((scene) => `
        <div class="scene-card" data-index="${escapeHtml(String(scene.scene_index))}">
            <div class="scene-header">
                <span class="scene-number">场景 ${scene.scene_index + 1}</span>
                <span class="scene-tone">${escapeHtml(scene.emotional_tone || '')}</span>
            </div>
            <p class="scene-intent">${escapeHtml(scene.intent || '')}</p>
            <div class="scene-meta">
                <span>视角: ${escapeHtml(scene.pov_character || '')}</span>
                <span>出场: ${(scene.present_characters || []).map(c => escapeHtml(c)).join(', ')}</span>
                <span>字数: ~${escapeHtml(String(scene.target_word_count || 1200))}</span>
            </div>
            ${showWritingActions ? `
            <div class="scene-actions">
                <button onclick="generateScene(${escapeHtml(String(scene.scene_index))})"
                        ${AppState.isGenerating ? 'disabled' : ''}>
                    ${AppState.isGenerating ? '生成中...' : '生成'}
                </button>
                <button onclick="editScene(${escapeHtml(String(scene.scene_index))})">编辑</button>
                <button onclick="probeContext(${escapeHtml(String(scene.scene_index))})" title="查看注入上下文">探针</button>
                <button onclick="rewriteScene(${escapeHtml(String(scene.scene_index))})" title="基于反馈重写">重写</button>
            </div>
            <div class="scene-content" id="scene-content-${escapeHtml(String(scene.scene_index))}"></div>
            ` : ''}
        </div>
    `).join('');

    return `
        <div class="chapter-header">
            <h3>${escapeHtml(plan.title || `第${plan.chapter_number}章`)}</h3>
            <p class="goal">本章目标: ${escapeHtml(plan.chapter_goal || '')}</p>
            <p class="emotion">情绪走向: ${escapeHtml(plan.emotional_arc || '')}</p>
        </div>
        ${showWritingActions && scenes.length > 0 ? `
        <div class="chapter-batch-actions" style="margin-bottom:16px;display:flex;gap:10px;align-items:center;">
            <button class="mans-btn primary" onclick="generateAllScenes()"
                    ${AppState.isGenerating ? 'disabled' : ''}>
                ${AppState.isGenerating ? '生成中...' : '一键生成全章'}
            </button>
            <span style="color:var(--text-secondary);font-size:13px;">
                共 ${scenes.length} 个场景，将按顺序依次生成
            </span>
        </div>
        ` : ''}
        <div class="scenes-list">
            ${sceneCards}
        </div>
    `;
}

/**
 * 加载章节规划到独立的"章节规划"面板（非写作面板）。
 *
 * @param {string} projectId - 项目 UUID。
 * @param {number} chapterNum - 章节编号。
 */
async function loadChapterPlanForPanel(projectId, chapterNum) {
    const container = document.getElementById('chapter-plan-result');
    if (!container) return;

    container.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;gap:8px;padding:20px;"><div class="loading-spinner small"></div><span style="color:var(--text-secondary);">加载中...</span></div>';

    try {
        const plan = await apiRequest(
            `/api/projects/${projectId}/chapters/${chapterNum}/plan`
        );
        displayChapterPlanResult(plan);
    } catch (error) {
        container.innerHTML = `
            <div class="panel-empty">
                <p class="panel-empty-text">第${escapeHtml(String(chapterNum))}章规划不存在，请先生成弧线规划或点击上方按钮生成</p>
            </div>
        `;
    }
}

/**
 * 渲染章节规划到章节规划面板（只读展示，无操作按钮）。
 *
 * @param {object} plan - 章节规划数据。
 */
function displayChapterPlanResult(plan) {
    const container = document.getElementById('chapter-plan-result');
    if (!container) return;

    container.innerHTML = buildChapterPlanHtml(plan, false);
}

/**
 * 同步所有场景生成按钮的禁用状态与文本。
 *
 * 在生成开始/结束时统一更新，避免各个按钮状态不一致。
 */
function updateAllSceneButtons() {
    document.querySelectorAll('.scene-actions button').forEach(btn => {
        const onClick = btn.getAttribute('onclick') || '';
        if (onClick.startsWith('generateScene')) {
            btn.disabled = AppState.isGenerating;
            btn.textContent = AppState.isGenerating ? '生成中...' : '生成';
        } else if (onClick.startsWith('editScene')) {
            btn.disabled = AppState.isGenerating;
        }
    });
}

/**
 * 一键生成全章所有场景（无人值守，顺序执行）。
 *
 * 流程：
 *     1. 从 DOM 中读取当前章节的所有场景索引。
 *     2. 跳过已有内容的场景（避免覆盖）。
 *     3. 逐个调用 generateScene()，场景间间隔 500ms 让系统喘息。
 *     4. 支持 AbortController 中断：用户可在任意时刻取消后续生成。
 *
 * 注意：每个场景的生成失败不会阻断后续场景，最终报告成功/失败/跳过数量。
 */
async function generateAllScenes() {
    if (!AppState.currentProject || AppState.isGenerating) return;

    // 提取当前章节所有场景索引
    const sceneCards = document.querySelectorAll('.scene-card');
    const sceneIndices = Array.from(sceneCards).map(card => {
        const idxAttr = card.getAttribute('data-index');
        return idxAttr ? parseInt(idxAttr, 10) : null;
    }).filter(idx => idx !== null);

    if (sceneIndices.length === 0) {
        showMessage('当前章节无场景可生成', 'warning');
        return;
    }

    if (!confirm(`将按顺序生成 ${sceneIndices.length} 个场景，期间请勿关闭页面。确认开始？`)) {
        return;
    }

    // 创建专用的 AbortController，用于一键生成全章的中断控制
    if (AppState.currentAbortController) {
        AppState.currentAbortController.abort();
    }
    AppState.currentAbortController = new AbortController();
    const controller = AppState.currentAbortController;

    let successCount = 0;
    let failCount = 0;
    let cancelled = false;

    for (const sceneIndex of sceneIndices) {
        if (controller.signal.aborted) {
            cancelled = true;
            break;
        }

        // 跳过已有内容的场景（避免覆盖已生成的内容）
        const contentDiv = document.getElementById(`scene-content-${sceneIndex}`);
        const hasContent = contentDiv && contentDiv.querySelector('.generated-text');
        if (hasContent) {
            console.log(`场景 ${sceneIndex + 1} 已有内容，跳过`);
            continue;
        }

        try {
            await generateScene(sceneIndex);
            successCount++;
        } catch (error) {
            if (error.name === 'AbortError' || controller.signal.aborted) {
                cancelled = true;
                break;
            }
            failCount++;
            showMessage(`场景 ${sceneIndex + 1} 生成失败: ${error.message}`, 'error');
        }

        // 每个场景之间短暂停顿，降低对后端的同时压力
        if (sceneIndex !== sceneIndices[sceneIndices.length - 1]) {
            await new Promise(r => setTimeout(r, 500));
        }
    }

    AppState.currentAbortController = null;

    if (cancelled) {
        showMessage('全章生成已取消', 'warning');
    } else {
        showMessage(
            `全章生成完成！成功: ${successCount}，失败: ${failCount}，跳过: ${sceneIndices.length - successCount - failCount}`,
            failCount > 0 ? 'warning' : 'success'
        );
    }
}

/**
 * 生成单个场景（流式输出）。
 *
 * 流程：
 *     1. 设置全局生成状态，禁用相关按钮。
 *     2. 创建 AbortController 供用户中断。
 *     3. 调用 connectStream() 建立 SSE 连接并逐 token 渲染。
 *     4. 生成完成后检查异步知识库更新。
 *     5. 清理状态，恢复按钮。
 *
 * @param {number} sceneIndex - 场景序号（0-based）。
 */
async function generateScene(sceneIndex) {
    if (!AppState.currentProject || AppState.isGenerating) return;

    AppState.isGenerating = true;
    AppState.currentScene = sceneIndex;
    updateAllSceneButtons();

    // 中断之前的流式请求（如果存在）
    if (AppState.currentAbortController) {
        AppState.currentAbortController.abort();
    }
    AppState.currentAbortController = new AbortController();

    const contentDiv = document.getElementById(`scene-content-${sceneIndex}`);
    contentDiv.classList.add('locked');
    contentDiv.innerHTML = '<div class="generating">正在生成...</div>';

    try {
        // 直接连接 SSE 流，传入 AbortController signal 支持中断
        await connectStream(sceneIndex, contentDiv, AppState.currentAbortController.signal);
        // 生成完成后检查异步知识库更新
        checkAsyncUpdates(AppState.currentChapter);
    } catch (error) {
        if (error.name === 'AbortError') {
            contentDiv.innerHTML = '<div class="error">生成已取消</div>';
        } else {
            contentDiv.innerHTML = `<div class="error">生成失败: ${escapeHtml(error.message)}</div>`;
        }
    } finally {
        AppState.isGenerating = false;
        AppState.currentAbortController = null;
        contentDiv.classList.remove('locked');
        updateAllSceneButtons();
    }
}

/**
 * 连接场景生成的 SSE 流（fetch + ReadableStream 手动解析）。
 *
 * 不使用浏览器原生 EventSource，原因：
 *     1. 需要 POST 请求（EventSource 仅支持 GET）。
 *     2. 需要自定义请求头（如 Content-Type: application/json）。
 *     3. 需要 AbortController 支持主动中断。
 *
 * SSE 协议解析：
 *     数据以 "event: xxx\ndata: {...}\n\n" 格式传输。
 *     本函数维护 buffer，按行分割，解析 event 与 data 字段，空行触发事件分发。
 *
 * 事件处理：
 *     start          —— 显示场景意图与目标字数
 *     token          —— 追加文本并实时渲染（调用 formatNovelText 清洗 Markdown）
 *     progress       —— 预留，当前仅忽略
 *     scene_complete —— Toast 通知生成完成
 *     error          —— 拒绝 Promise，触发错误处理
 *     done           —— 解析正常结束
 *
 * 智能滚动：仅在用户处于内容区底部 50px 内时自动下拉，避免打断阅读。
 *
 * @param {number} sceneIndex - 场景序号。
 * @param {HTMLElement} contentDiv - 内容渲染容器。
 * @param {AbortSignal|null} signal - 可选的 AbortController signal。
 * @returns {Promise<void>}
 */
async function connectStream(sceneIndex, contentDiv, signal = null) {
    const projectId = AppState.currentProject;
    const chapterNum = AppState.currentChapter;

    const temperature = getSetting('temperature', 0.75);
    const base = getApiBase().replace(/\/$/, '');
    const streamUrl = base
        ? `${base}/api/projects/${projectId}/stream/${chapterNum}/${sceneIndex}`
        : `/api/projects/${projectId}/stream/${chapterNum}/${sceneIndex}`;

    const response = await fetch(streamUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ temperature }),
        signal
    });

    if (!response.ok) {
        const errorText = await response.text();
        throw new Error(`HTTP ${response.status}: ${errorText}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let generatedText = '';
    let hasError = false;

    return new Promise((resolve, reject) => {
        let currentEvent = { event: 'message', data: '' };

        /**
         * 分发已解析完成的 SSE 事件。
         */
        const dispatchEvent = () => {
            try {
                switch (currentEvent.event) {
                    case 'start': {
                        const data = JSON.parse(currentEvent.data);
                        contentDiv.innerHTML = `<div class="generating">开始生成场景 ${data.scene_index + 1}：${escapeHtml(data.intent)}</div>`;
                        break;
                    }
                    case 'token': {
                        const data = JSON.parse(currentEvent.data);
                        generatedText += data.content;
                        // 智能滚动：仅在用户处于底部时自动下拉
                        const isNearBottom = (
                            contentDiv.scrollHeight - contentDiv.scrollTop - contentDiv.clientHeight
                        ) < 50;
                        contentDiv.innerHTML = `<div class="generated-text">${escapeHtml(formatNovelText(generatedText))}</div>`;
                        if (isNearBottom) {
                            contentDiv.scrollTop = contentDiv.scrollHeight;
                        }
                        break;
                    }
                    case 'progress': {
                        // 预留：可在 UI 中显示进度条或 token 计数
                        break;
                    }
                    case 'scene_complete': {
                        const data = JSON.parse(currentEvent.data);
                        showMessage(`场景生成完成！字数: ${data.word_count}`, 'success');
                        break;
                    }
                    case 'error': {
                        hasError = true;
                        let msg = '生成出错';
                        try {
                            const data = JSON.parse(currentEvent.data);
                            msg = data.message || msg;
                        } catch {}
                        contentDiv.innerHTML += `<div class="error">错误: ${escapeHtml(msg)}</div>`;
                        reject(new Error(msg));
                        break;
                    }
                    case 'done': {
                        if (!hasError) resolve();
                        break;
                    }
                    default:
                        // ping 等无需处理的事件
                        break;
                }
            } catch (e) {
                console.error('流事件处理错误:', e);
            }
            currentEvent = { event: 'message', data: '' };
        };

        /**
         * 递归读取响应流数据块并解析 SSE 事件。
         */
        const processChunk = () => {
            reader.read().then(({ done, value }) => {
                if (done) {
                    if (!hasError) resolve();
                    return;
                }

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop(); // 保留不完整的最后一行

                for (const line of lines) {
                    if (line.startsWith('event:')) {
                        currentEvent.event = line.slice(6).trim();
                    } else if (line.startsWith('data:')) {
                        currentEvent.data = line.slice(5).trim();
                    } else if (line === '') {
                        dispatchEvent();
                    }
                }

                processChunk();
            }).catch(err => {
                hasError = true;
                reject(err);
            });
        };

        processChunk();
    });
}

/**
 * 打开场景重写反馈模态框。
 *
 * 显示警告提示：重新生成不会自动撤销知识库更新；如需回滚请先使用"回滚知识库"按钮。
 *
 * @param {number} sceneIndex - 场景序号。
 */
function rewriteScene(sceneIndex) {
    if (AppState.isGenerating) {
        showMessage('AI 正在生成中，请稍后再操作', 'warning');
        return;
    }
    const html = `
        <div>
            <p style="margin-bottom: 12px; color: var(--warning); font-size: 13px;">
                注意：重新生成不会自动撤销已更新的知识库设定（如人物状态、世界规则等）。
                如需回滚，请在重写前使用「回滚知识库」按钮。
            </p>
            <p style="margin-bottom: 12px; color: var(--text-secondary);">描述当前场景的问题或修改方向：</p>
            <textarea id="rewrite-feedback-${sceneIndex}" rows="4"
                style="width: 100%; background: var(--bg-dark); border: 1px solid var(--bg-hover); border-radius: 6px; padding: 8px; color: var(--text-primary);"
                placeholder="例如：节奏太快，缺少环境描写..."></textarea>
            <div style="margin-top: 12px; display: flex; justify-content: space-between; align-items: center;">
                <button class="mans-btn" onclick="rollbackSceneKnowledge(${sceneIndex})" title="回滚该场景产生的知识库更新">回滚知识库</button>
                <button class="mans-btn primary" onclick="startRewrite(${sceneIndex})">开始重写</button>
            </div>
        </div>
    `;
    showModal('重写场景', html);
}

/**
 * 回滚某场景产生的知识库更新。
 *
 * 调用后端 /rollback 接口，清理该场景上次引入的所有状态变更。
 *
 * @param {number} sceneIndex - 场景序号。
 */
async function rollbackSceneKnowledge(sceneIndex) {
    if (!AppState.currentProject) return;
    try {
        const result = await apiRequest(
            `/api/projects/${AppState.currentProject}/chapters/${AppState.currentChapter}/scenes/${sceneIndex}/rollback`,
            { method: 'POST' }
        );
        showMessage(result.message || '知识库已回滚', 'success');
        refreshIssueBadge();
    } catch (error) {
        showMessage('回滚失败: ' + error.message, 'error');
    }
}

/**
 * 开始流式重写场景。
 *
 * 读取反馈文本，关闭模态框，进入生成状态并连接重写 SSE 流。
 *
 * @param {number} sceneIndex - 场景序号。
 */
async function startRewrite(sceneIndex) {
    const feedback = document.getElementById(`rewrite-feedback-${sceneIndex}`)?.value.trim();
    if (!feedback) {
        showMessage('请输入反馈意见', 'warning');
        return;
    }
    closeDynamicModal();

    AppState.isGenerating = true;
    AppState.currentScene = sceneIndex;
    updateAllSceneButtons();

    const contentDiv = document.getElementById(`scene-content-${sceneIndex}`);
    contentDiv.classList.add('locked');
    contentDiv.innerHTML = '<div class="generating">正在重写...</div>';

    try {
        await connectRewriteStream(sceneIndex, feedback, contentDiv);
        checkAsyncUpdates(AppState.currentChapter);
    } catch (error) {
        contentDiv.innerHTML = `<div class="error">重写失败: ${escapeHtml(error.message)}</div>`;
    } finally {
        AppState.isGenerating = false;
        contentDiv.classList.remove('locked');
        updateAllSceneButtons();
    }
}

/**
 * 连接场景重写的 SSE 流。
 *
 * 与 connectStream 逻辑基本一致，但请求 endpoint 为 regenerate，
 * 并额外提交 feedback 参数。
 *
 * @param {number} sceneIndex - 场景序号。
 * @param {string} feedback - 用户反馈文本。
 * @param {HTMLElement} contentDiv - 内容渲染容器。
 * @returns {Promise<void>}
 */
async function connectRewriteStream(sceneIndex, feedback, contentDiv) {
    const projectId = AppState.currentProject;
    const chapterNum = AppState.currentChapter;

    const temperature = getSetting('temperature', 0.75);
    const base = getApiBase().replace(/\/$/, '');
    const streamUrl = base
        ? `${base}/api/projects/${projectId}/chapters/${chapterNum}/scenes/${sceneIndex}/regenerate`
        : `/api/projects/${projectId}/chapters/${chapterNum}/scenes/${sceneIndex}/regenerate`;

    const response = await fetch(streamUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ temperature, feedback })
    });

    if (!response.ok) {
        const errorText = await response.text();
        throw new Error(`HTTP ${response.status}: ${errorText}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let generatedText = '';
    let hasError = false;

    return new Promise((resolve, reject) => {
        let currentEvent = { event: 'message', data: '' };

        const dispatchEvent = () => {
            try {
                switch (currentEvent.event) {
                    case 'start': {
                        const data = JSON.parse(currentEvent.data);
                        contentDiv.innerHTML = `<div class="generating">开始重写场景 ${data.scene_index + 1}：${escapeHtml(data.intent)}</div>`;
                        break;
                    }
                    case 'token': {
                        const data = JSON.parse(currentEvent.data);
                        generatedText += data.content;
                        const isNearBottom = (
                            contentDiv.scrollHeight - contentDiv.scrollTop - contentDiv.clientHeight
                        ) < 50;
                        contentDiv.innerHTML = `<div class="generated-text">${escapeHtml(formatNovelText(generatedText))}</div>`;
                        if (isNearBottom) {
                            contentDiv.scrollTop = contentDiv.scrollHeight;
                        }
                        break;
                    }
                    case 'progress': break;
                    case 'scene_complete': {
                        const data = JSON.parse(currentEvent.data);
                        showMessage(`场景重写完成！字数: ${data.word_count}`, 'success');
                        break;
                    }
                    case 'error': {
                        hasError = true;
                        let msg = '重写出错';
                        try {
                            const data = JSON.parse(currentEvent.data);
                            msg = data.message || msg;
                        } catch {}
                        contentDiv.innerHTML += `<div class="error">错误: ${escapeHtml(msg)}</div>`;
                        reject(new Error(msg));
                        break;
                    }
                    case 'done': {
                        if (!hasError) resolve();
                        break;
                    }
                    default: break;
                }
            } catch (e) {
                console.error('流事件处理错误:', e);
            }
            currentEvent = { event: 'message', data: '' };
        };

        const processChunk = () => {
            reader.read().then(({ done, value }) => {
                if (done) {
                    if (!hasError) resolve();
                    return;
                }
                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop();
                for (const line of lines) {
                    if (line.startsWith('event:')) {
                        currentEvent.event = line.slice(6).trim();
                    } else if (line.startsWith('data:')) {
                        currentEvent.data = line.slice(5).trim();
                    } else if (line === '') {
                        dispatchEvent();
                    }
                }
                processChunk();
            }).catch(err => {
                hasError = true;
                reject(err);
            });
        };

        processChunk();
    });
}

/**
 * 网文排版格式化：清洗 Markdown 标记并规范换行。
 *
 * 格式化规则：
 *     1. 去除 **加粗** 标记。
 *     2. 去除 # 标题标记。
 *     3. 去除 - / * 列表标记。
 *     4. 折叠三个及以上连续换行为双换行（保持段落分隔但不空洞）。
 *     5. 去除行首行尾多余空白。
 *
 * 与 CSS text-indent: 2em 配合，营造沉浸式中文小说阅读体验。
 *
 * @param {string} text - 原始文本（可能含 Markdown 污染）。
 * @returns {string} 格式化后的干净文本。
 */
function formatNovelText(text) {
    if (!text) return '';
    return text
        .replace(/\*\*(.*?)\*\*/g, '$1')
        .replace(/^#{1,6}\s+/gm, '')
        .replace(/^[-*+]\s+/gm, '')
        .replace(/\n{3,}/g, '\n\n')
        .trim();
}

/**
 * HTML 实体转义（防止 XSS）。
 *
 * 通过创建临时 DOM 元素利用浏览器的原生转义能力，
 * 比字符串替换更可靠（覆盖所有 HTML 特殊字符）。
 *
 * @param {string} text - 原始文本。
 * @returns {string} 转义后的安全 HTML 字符串。
 */
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/**
 * 转义字符串中的 JavaScript 特殊字符（用于内联事件处理器）。
 *
 * 将单引号转义为 \\'，反斜杠转义为 \\\\，防止 onclick="func('...')" 中的引号冲突。
 *
 * @param {string} text - 原始文本。
 * @returns {string} 转义后的安全字符串。
 */
function escapeJsString(text) {
    return String(text).replace(/\\/g, '\\\\').replace(/'/g, "\\'");
}

/**
 * 进入场景编辑模式。
 *
 * 将场景内容区替换为 textarea，支持：
 *     1. 手动保存（保存按钮）。
 *     2. 自动保存（输入停止 2 秒后触发，带防抖）。
 *     3. 取消编辑（恢复原内容）。
 *     4. 同步知识库（将修改后的文本提交给 UpdateExtractor 分析）。
 *
 * 自动保存的闭包捕获：在设置定时器的那一刻冻结 projectId 和 chapterNum，
 * 防止用户在防抖期间切换章节导致保存到错误位置。
 *
 * @param {number} sceneIndex - 场景序号。
 */
function editScene(sceneIndex) {
    if (AppState.isGenerating) {
        showMessage('AI 正在生成中，请稍后再编辑', 'warning');
        return;
    }

    const contentDiv = document.getElementById(`scene-content-${sceneIndex}`);
    const currentText = contentDiv.textContent || '';

    contentDiv.dataset.originalContent = currentText;
    contentDiv._autoSaveTimer = null;

    contentDiv.innerHTML = `
        <textarea id="edit-scene-${sceneIndex}" rows="10">${escapeHtml(currentText)}</textarea>
        <div class="edit-actions">
            <button onclick="saveSceneEdit(${sceneIndex})">保存</button>
            <button onclick="cancelSceneEdit(${sceneIndex})">取消</button>
            <button class="mans-btn" onclick="syncSceneKnowledge(${sceneIndex})" title="将当前修改同步到知识库">同步知识库</button>
        </div>
        <div class="save-status" id="save-status-${sceneIndex}"></div>
    `;

    const textarea = document.getElementById(`edit-scene-${sceneIndex}`);

    // 闭包捕获：冻结当前 projectId 与 chapterNum，防止防抖期间切换上下文
    const capturedProjectId = AppState.currentProject;
    const capturedChapterNum = AppState.currentChapter;

    const triggerAutoSave = () => {
        const statusDiv = document.getElementById(`save-status-${sceneIndex}`);
        if (statusDiv) {
            statusDiv.textContent = '正在保存...';
            statusDiv.className = 'save-status saving';
        }
        saveSceneEdit(sceneIndex, { silent: true, projectId: capturedProjectId, chapterNum: capturedChapterNum }).then(() => {
            if (statusDiv) {
                statusDiv.textContent = '已自动保存';
                statusDiv.className = 'save-status saved';
                setTimeout(() => {
                    if (statusDiv && statusDiv.textContent === '已自动保存') {
                        statusDiv.textContent = '';
                    }
                }, 3000);
            }
        }).catch(() => {
            if (statusDiv) {
                statusDiv.textContent = '自动保存失败';
                statusDiv.className = 'save-status error';
            }
        });
    };

    textarea.addEventListener('input', () => {
        if (contentDiv._autoSaveTimer) clearTimeout(contentDiv._autoSaveTimer);
        const statusDiv = document.getElementById(`save-status-${sceneIndex}`);
        if (statusDiv) {
            statusDiv.textContent = '有未保存的更改';
            statusDiv.className = 'save-status';
        }
        contentDiv._autoSaveTimer = setTimeout(triggerAutoSave, 2000);
    });
}

/**
 * 保存场景编辑内容。
 *
 * @param {number} sceneIndex - 场景序号。
 * @param {object} options - 可选参数：
 *     silent: 是否静默（不弹 Toast）。
 *     projectId / chapterNum: 用于闭包穿透防护，优先于全局状态。
 *
 * @throws {Error} 保存失败时抛出（silent 模式下仍抛出，供自动保存捕获）。
 */
async function saveSceneEdit(sceneIndex, options = {}) {
    const { silent = false, projectId = null, chapterNum = null } = options;
    const contentDiv = document.getElementById(`scene-content-${sceneIndex}`);
    if (contentDiv && contentDiv._autoSaveTimer) {
        clearTimeout(contentDiv._autoSaveTimer);
        contentDiv._autoSaveTimer = null;
    }

    const textarea = document.getElementById(`edit-scene-${sceneIndex}`);
    if (!textarea) return; // 编辑器已关闭，跳过
    const newText = textarea.value;

    // 优先使用传入的参数，防止闭包期间用户切换章节导致保存到错误位置
    const effectiveProjectId = projectId || AppState.currentProject;
    const effectiveChapterNum = chapterNum || AppState.currentChapter;

    try {
        await apiRequest(
            `/api/projects/${effectiveProjectId}/chapters/${effectiveChapterNum}/scenes/${sceneIndex}`,
            {
                method: 'PUT',
                body: JSON.stringify({ text: newText })
            }
        );

        if (!silent) {
            showMessage('场景已保存', 'success');
        }
        if (contentDiv) {
            contentDiv.innerHTML = `<div class="generated-text">${escapeHtml(formatNovelText(newText))}</div>`;
        }
    } catch (error) {
        if (!silent) {
            showMessage('保存失败: ' + error.message, 'error');
        }
        throw error;
    }
}

/**
 * 手动同步场景修改到知识库。
 *
 * 调用后端的 /extract 接口，触发 UpdateExtractor 分析当前编辑器中的文本，
 * 提取人物状态变化、新伏笔等信息并更新知识库。
 *
 * @param {number} sceneIndex - 场景序号。
 */
async function syncSceneKnowledge(sceneIndex) {
    if (!AppState.currentProject) return;

    const textarea = document.getElementById(`edit-scene-${sceneIndex}`);
    if (!textarea) {
        showMessage('编辑器已关闭，无法同步', 'warning');
        return;
    }

    const statusDiv = document.getElementById(`save-status-${sceneIndex}`);
    if (statusDiv) {
        statusDiv.textContent = '正在分析文本并同步知识库...';
        statusDiv.className = 'save-status saving';
    }

    try {
        const result = await apiRequest(
            `/api/projects/${AppState.currentProject}/chapters/${AppState.currentChapter}/scenes/${sceneIndex}/extract`,
            { method: 'POST' }
        );

        if (statusDiv) {
            statusDiv.textContent = '知识库同步完成';
            statusDiv.className = 'save-status saved';
            setTimeout(() => { if (statusDiv) statusDiv.textContent = ''; }, 3000);
        }
        showMessage('知识库已同步', 'success');
        refreshIssueBadge();
    } catch (error) {
        if (statusDiv) {
            statusDiv.textContent = '同步失败';
            statusDiv.className = 'save-status error';
        }
        showMessage('同步知识库失败: ' + error.message, 'error');
    }
}

/**
 * 取消场景编辑，恢复原内容。
 *
 * @param {number} sceneIndex - 场景序号。
 */
function cancelSceneEdit(sceneIndex) {
    const contentDiv = document.getElementById(`scene-content-${sceneIndex}`);
    if (contentDiv && contentDiv._autoSaveTimer) {
        clearTimeout(contentDiv._autoSaveTimer);
        contentDiv._autoSaveTimer = null;
    }
    const originalContent = contentDiv.dataset.originalContent || '';
    contentDiv.innerHTML = `<div class="generated-text">${escapeHtml(formatNovelText(originalContent))}</div>`;
    delete contentDiv.dataset.originalContent;
}

/**
 * 上下文探针：查看当前场景注入上下文的摘要信息。
 *
 * 调用后端 /context 接口，展示 Writer 实际接收到的上下文组成：
 * 场景意图、本章目标、前文预览、出场人物、世界规则数量、
 * 活跃伏笔数量、风格参考、Token 使用情况等。
 *
 * @param {number} sceneIndex - 场景序号。
 */
async function probeContext(sceneIndex) {
    if (!AppState.currentProject) return;
    try {
        const data = await apiRequest(
            `/api/projects/${AppState.currentProject}/chapters/${AppState.currentChapter}/scenes/${sceneIndex}/context`
        );
        const html = `
            <div style="max-height: 70vh; overflow-y: auto;">
                <div style="margin-bottom: 12px;">
                    <strong>场景意图:</strong> ${escapeHtml(data.scene_intent)}
                </div>
                <div style="margin-bottom: 12px;">
                    <strong>本章目标:</strong> ${escapeHtml(data.chapter_goal)}
                </div>
                <div style="margin-bottom: 12px;">
                    <strong>前文预览:</strong>
                    <pre style="white-space: pre-wrap; background: var(--bg-dark); padding: 8px; border-radius: 6px;">${escapeHtml(data.previous_text_preview)}</pre>
                </div>
                <div style="margin-bottom: 12px;">
                    <strong>出场人物 (${data.present_characters.length}):</strong>
                    <ul>${data.present_characters.map(c => `<li>${escapeHtml(c.name)} — ${escapeHtml(c.current_location || '')} — ${escapeHtml(c.current_emotion || '')}</li>`).join('')}</ul>
                </div>
                <div style="margin-bottom: 12px;">
                    <strong>相关世界规则:</strong> ${data.relevant_world_rules_count} 条
                </div>
                <div style="margin-bottom: 12px;">
                    <strong>活跃伏笔:</strong> ${data.active_foreshadowing_count} 条
                </div>
                <div style="margin-bottom: 12px;">
                    <strong>风格参考:</strong>
                    <pre style="white-space: pre-wrap; background: var(--bg-dark); padding: 8px; border-radius: 6px;">${escapeHtml(data.style_reference_preview)}</pre>
                </div>
                <div style="margin-bottom: 12px;">
                    <strong>相似场景参考:</strong> ${data.similar_scenes_count} 条
                </div>
                <div>
                    <strong>Token 使用:</strong> ${data.total_tokens_used} / ${data.total_tokens_used + data.token_budget_remaining}
                    <span style="color: var(--text-secondary); margin-left: 8px;">(剩余 ${data.token_budget_remaining})</span>
                </div>
            </div>
        `;
        showModal(`场景 ${sceneIndex + 1} 注入上下文`, html);
    } catch (error) {
        showMessage('获取上下文失败: ' + error.message, 'error');
    }
}

/**
 * 显示通用模态框（动态创建 DOM）。
 *
 * 每次调用会移除已存在的动态模态框并创建新的，
 * 确保内容始终最新且不存在 z-index 冲突。
 *
 * @param {string} title - 模态框标题。
 * @param {string} htmlContent - 模态框主体 HTML。
 */
function showModal(title, htmlContent) {
    const existing = document.getElementById('dynamic-modal');
    if (existing) existing.remove();

    const modal = document.createElement('div');
    modal.id = 'dynamic-modal';
    modal.className = 'modal';
    modal.style.display = 'flex';
    modal.innerHTML = `
        <div class="modal-content" style="max-width: 720px; width: 95%;">
            <div class="modal-header">
                <h3>${escapeHtml(title)}</h3>
                <button class="close-btn" onclick="closeDynamicModal()">&times;</button>
            </div>
            <div style="padding: 10px 0;">
                ${htmlContent}
            </div>
        </div>
    `;
    document.body.appendChild(modal);
}

/**
 * 关闭动态模态框。
 */
function closeDynamicModal() {
    const modal = document.getElementById('dynamic-modal');
    if (modal) modal.remove();
}

/**
 * 确认章节完稿，进入下一章。
 *
 * 完稿前安全流程：
 *     1. 中止任何正在进行的生成，防止流式写入干扰。
 *     2. 强制保存所有活跃编辑器中的内容（清除防抖定时器并同步写入）。
 *     3. 用户二次确认。
 *     4. 调用后端 /confirm 接口完成合并、导出、向量同步。
 *     5. 加载下一章规划。
 */
async function confirmChapter() {
    if (!AppState.currentProject) return;

    // 第1步：中止任何正在进行的生成
    if (AppState.currentAbortController) {
        AppState.currentAbortController.abort();
        AppState.currentAbortController = null;
    }

    // 第2步：强制保存所有活跃编辑器
    const pendingSaves = [];
    document.querySelectorAll('.scene-content').forEach(contentDiv => {
        const sceneIndex = parseInt(contentDiv.id.replace('scene-content-', ''), 10);
        if (isNaN(sceneIndex)) return;

        if (contentDiv._autoSaveTimer) {
            clearTimeout(contentDiv._autoSaveTimer);
            contentDiv._autoSaveTimer = null;
        }

        const textarea = document.getElementById(`edit-scene-${sceneIndex}`);
        if (textarea) {
            pendingSaves.push(
                saveSceneEdit(sceneIndex, { silent: true })
                    .catch(err => {
                        console.error(`强制保存场景 ${sceneIndex} 失败:`, err);
                        throw err;
                    })
            );
        }
    });

    if (pendingSaves.length > 0) {
        try {
            await Promise.all(pendingSaves);
            showMessage('已强制保存未落盘的编辑内容', 'info');
        } catch (e) {
            showMessage('强制保存失败: ' + e.message, 'error');
            return;
        }
    }

    // 第3步：用户二次确认
    if (!confirm('确认本章已完成？确认后将进入下一章。')) {
        return;
    }

    try {
        const result = await apiRequest(
            `/api/projects/${AppState.currentProject}/chapters/${AppState.currentChapter}/confirm`,
            { method: 'POST' }
        );

        showMessage(`第${AppState.currentChapter}章已确认！字数: ${result.word_count}`, 'success');
        await loadExports();

        AppState.currentChapter++;
        localStorage.setItem('mans_current_chapter', String(AppState.currentChapter));
        await loadChapterPlan(AppState.currentProject, AppState.currentChapter);

    } catch (error) {
        showMessage('确认章节失败: ' + error.message, 'error');
    }
}

/**
 * 加载已导出完稿文件列表。
 *
 * 扫描后端 exports/ 目录，展示文件名、大小、点击可预览。
 */
async function loadExports() {
    if (!AppState.currentProject) return;
    const panel = document.getElementById('exports-panel');
    panel.style.display = 'block';

    try {
        const data = await apiRequest(`/api/projects/${AppState.currentProject}/exports`);
        if (!data.exports || data.exports.length === 0) {
            panel.innerHTML = `
                <div class="panel-empty" style="padding: 12px;">
                    <p style="color: var(--text-secondary); font-size: 13px;">
                        暂无已导出文件。确认完稿后，系统将自动生成只读发行版。
                    </p>
                </div>
            `;
            return;
        }
        const rows = data.exports.map(exp => {
            const sizeBytes = exp.size || 0;
            const sizeText = sizeBytes < 1024 ? `${sizeBytes} B` : `${(sizeBytes / 1024).toFixed(1)} KB`;
            return `
            <div class="export-item" onclick="viewExport('${escapeJsString(exp.filename)}')"
                 style="display:flex;justify-content:space-between;align-items:center;padding:8px 12px;
                        border-bottom:1px solid var(--bg-hover);cursor:pointer;"
                 onmouseenter="this.style.background='var(--bg-hover)';"
                 onmouseleave="this.style.background='transparent';"
            >
                <span>📄 ${escapeHtml(exp.filename)}</span>
                <span style="color:var(--text-secondary);font-size:12px;">${sizeText}</span>
            </div>
        `}).join('');
        panel.innerHTML = `
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                <strong style="font-size:14px;">已导出完稿</strong>
                <button class="mans-btn sm" onclick="document.getElementById('exports-panel').style.display='none'">收起</button>
            </div>
            <div style="max-height:240px;overflow-y:auto;">${rows}</div>
        `;
    } catch (error) {
        showMessage('加载导出列表失败: ' + error.message, 'error');
    }
}

/**
 * 查看导出的只读文件内容。
 *
 * @param {string} filename - 导出文件名。
 */
async function viewExport(filename) {
    if (!AppState.currentProject) return;
    try {
        const data = await apiRequest(
            `/api/projects/${AppState.currentProject}/exports/${encodeURIComponent(filename)}`
        );
        const html = `
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
                <span style="color:var(--warning);font-size:12px;">⚠ 只读发行版，任何修改均不会被系统同步</span>
            </div>
            <pre style="white-space:pre-wrap;background:var(--bg-dark);padding:12px;border-radius:6px;
                        max-height:60vh;overflow-y:auto;line-height:1.8;font-size:14px;">${escapeHtml(data.content)}</pre>
        `;
        showModal(filename, html);
    } catch (error) {
        showMessage('读取导出文件失败: ' + error.message, 'error');
    }
}

// ============================================================
// 知识库查看
// ============================================================

/**
 * 加载并展示项目知识库。
 *
 * 并行请求 Bible、人物、大纲、伏笔四个接口，全部完成后调用 displayKnowledgeBase() 渲染。
 *
 * @param {string} projectId - 项目 UUID。
 */
async function loadKnowledgeBase(projectId) {
    try {
        const [bible, characters, outline, foreshadowing] = await Promise.all([
            apiRequest(`/api/projects/${projectId}/bible`).catch(() => null),
            apiRequest(`/api/projects/${projectId}/characters`).catch(() => null),
            apiRequest(`/api/projects/${projectId}/outline`).catch(() => null),
            apiRequest(`/api/projects/${projectId}/foreshadowing`).catch(() => null)
        ]);

        displayKnowledgeBase({ bible, characters, outline, foreshadowing });

    } catch (error) {
        console.error('加载知识库失败:', error);
    }
}

/**
 * 将大纲数据格式化为可读的 HTML。
 *
 * 渲染结构：
 *     - 四幕卡片网格（第一幕 / 第二幕上 / 第二幕下 / 第三幕）
 *     - 核心冲突
 *     - 剧情风格
 *     - 关键转折点（时间线样式）
 *     - 结局
 *     - 全局伏笔列表（带类型标签与重要性标签）
 *
 * @param {object} outline - 大纲字典数据。
 * @returns {string} HTML 字符串。
 */
function formatOutlineHtml(outline) {
    if (!outline) return '<p>未生成</p>';

    const threeAct = outline.three_act_structure || {};
    const acts = [
        { key: 'act1', label: '第一幕' },
        { key: 'act2a', label: '第二幕（上）' },
        { key: 'act2b', label: '第二幕（下）' },
        { key: 'act3', label: '第三幕' }
    ];

    let actsHtml = acts.map(act => {
        const data = threeAct[act.key] || {};
        const range = data.chapter_range || [];
        const directions = (data.key_directions || []).map(d => `<li>${escapeHtml(d)}</li>`).join('');
        return `
            <div class="outline-act-card">
                <div class="outline-act-header">
                    <strong>${escapeHtml(data.name || act.label)}</strong>
                    <span class="outline-act-range">第 ${range[0] || '?'} ~ ${range[1] || '?'} 章</span>
                </div>
                <p class="outline-act-desc">${escapeHtml(data.description || '')}</p>
                ${directions ? `<ul class="outline-act-directions">${directions}</ul>` : ''}
            </div>
        `;
    }).join('');

    const mainConflict = outline.main_conflict || {};
    const conflictHtml = `
        <div class="outline-section">
            <h5>核心冲突</h5>
            <p><strong>冲突焦点：</strong>${escapeHtml(mainConflict.central_conflict || '')}</p>
            <p><strong>主角目标：</strong>${escapeHtml(mainConflict.protagonist_goal || '')}</p>
            <p><strong>对抗力量：</strong>${escapeHtml(mainConflict.antagonist_force || '')}</p>
            <p><strong>失败代价：</strong>${escapeHtml(mainConflict.stakes || '')}</p>
        </div>
    `;

    const storyPattern = outline.story_pattern || {};
    const patternHtml = `
        <div class="outline-section">
            <h5>剧情风格</h5>
            <p><strong>成长曲线：</strong>${escapeHtml(storyPattern.growth_curve || '')}
               <strong>节奏模式：</strong>${escapeHtml(storyPattern.rhythm_mode || '')}
               <strong>亮点密度：</strong>${escapeHtml(storyPattern.highlight_density || '')}</p>
            <p>${escapeHtml(storyPattern.description || '')}</p>
        </div>
    `;

    const turningPoints = outline.turning_points || [];
    const turningHtml = turningPoints.length ? `
        <div class="outline-section">
            <h5>关键转折点</h5>
            <div class="outline-timeline">
                ${turningPoints.map(tp => `
                    <div class="outline-timeline-item">
                        <span class="outline-timeline-chapter">第 ${tp.chapter || '?'} 章</span>
                        <div class="outline-timeline-content">
                            <strong>${escapeHtml(tp.name || '')}</strong>
                            <p>${escapeHtml(tp.description || '')}</p>
                        </div>
                    </div>
                `).join('')}
            </div>
        </div>
    ` : '';

    const ending = outline.ending || {};
    const endingHtml = `
        <div class="outline-section">
            <h5>结局</h5>
            <p><strong>方向：</strong>${escapeHtml(ending.direction || '')}</p>
            <p><strong>类型：</strong>${escapeHtml(ending.resolution_type || '')}</p>
        </div>
    `;

    const foreshadowing = outline.foreshadowing_list || [];
    const foreshadowingHtml = foreshadowing.length ? `
        <div class="outline-section">
            <h5>全局伏笔</h5>
            <ul class="outline-foreshadowing-list">
                ${foreshadowing.map(fs => `
                    <li>
                        <span class="fs-tag ${escapeHtml(fs.type || '')}">${escapeHtml(fs.type || '')}</span>
                        <span class="fs-importance ${escapeHtml(fs.importance || '')}">${escapeHtml(fs.importance || '')}</span>
                        <span class="fs-desc">${escapeHtml(fs.description || '')}</span>
                        <span class="fs-acts">${escapeHtml(fs.planted_act || '')} → ${escapeHtml(fs.resolution_act || '')}</span>
                    </li>
                `).join('')}
            </ul>
        </div>
    ` : '';

    return `
        <div class="outline-readable">
            <div class="outline-acts-grid">${actsHtml}</div>
            ${conflictHtml}
            ${patternHtml}
            ${turningHtml}
            ${endingHtml}
            ${foreshadowingHtml}
        </div>
    `;
}

/**
 * 渲染知识库面板内容。
 *
 * 使用 details/summary 实现可折叠的四大知识库区块：
 * Bible、人物、大纲、伏笔。已生成的区块默认展开。
 *
 * @param {object} data - 包含 bible、characters、outline、foreshadowing 的对象。
 */
function displayKnowledgeBase(data) {
    const container = document.getElementById('knowledge-display');
    if (!container) return;

    container.innerHTML = `
        <div class="kb-sections">
            <details ${data.bible ? 'open' : ''}>
                <summary>世界观 Bible ${data.bible ? '✓' : '✗'}</summary>
                <div class="kb-content">
                    ${data.bible ? `
                        <h4>${escapeHtml(data.bible.world_name || '')}</h4>
                        <p>${escapeHtml(data.bible.world_description || '')}</p>
                    ` : '<p>未生成</p>'}
                </div>
            </details>

            <details ${data.characters ? 'open' : ''}>
                <summary>人物设定 ${data.characters ? `(${data.characters.characters?.length || 0})` : '✗'}</summary>
                <div class="kb-content">
                    ${data.characters ? `
                        <ul>
                            ${(data.characters.characters || []).map(c => `
                                <li>${escapeHtml(c.name || '')} - ${escapeHtml(c.personality_core || '')}</li>
                            `).join('')}
                        </ul>
                    ` : '<p>未生成</p>'}
                </div>
            </details>

            <details ${data.outline ? 'open' : ''}>
                <summary>大纲 ${data.outline ? '✓' : '✗'}</summary>
                <div class="kb-content">
                    ${data.outline ? formatOutlineHtml(data.outline) : '<p>未生成</p>'}
                </div>
            </details>

            <details ${data.foreshadowing ? 'open' : ''}>
                <summary>伏笔 ${data.foreshadowing ? `(${data.foreshadowing.foreshadowing?.length || 0})` : '✗'}</summary>
                <div class="kb-content">
                    ${data.foreshadowing ? `
                        <ul>
                            ${(data.foreshadowing.foreshadowing || []).map(f => `
                                <li>[${escapeHtml(f.status || '')}] ${escapeHtml((f.description || '').substring(0, 50))}...</li>
                            `).join('')}
                        </ul>
                    ` : '<p>未生成</p>'}
                </div>
            </details>
        </div>
    `;
}

// ============================================================
// UI 面板控制
// ============================================================

/**
 * 显示指定面板，隐藏其他面板。
 *
 * 同时更新侧边栏激活状态与 localStorage 持久化，
 * 支持页面刷新后恢复到上次所在面板。
 *
 * @param {string} panelId - 面板元素 ID。
 */
function showPanel(panelId) {
    document.querySelectorAll('.content-panel').forEach(panel => {
        panel.classList.remove('active');
    });

    const panel = document.getElementById(panelId);
    if (panel) {
        panel.classList.add('active');
    }

    document.querySelectorAll('.sidebar-item').forEach(item => {
        item.classList.remove('active');
        if (item.dataset.panel === panelId) {
            item.classList.add('active');
        }
    });

    localStorage.setItem('mans_current_panel', panelId);
}

/**
 * 切换侧边栏展开/收起状态。
 */
function toggleSidebar() {
    const sidebar = document.querySelector('.sidebar');
    if (sidebar) {
        sidebar.classList.toggle('collapsed');
    }
}

// ============================================================
// 应用初始化与事件绑定
// ============================================================

document.addEventListener('DOMContentLoaded', () => {
    // 恢复上次激活的面板状态
    const savedPanel = localStorage.getItem('mans_current_panel') || 'works';
    document.querySelectorAll('.sidebar-item').forEach(item => {
        item.classList.remove('active');
        if (item.dataset.panel === savedPanel) {
            item.classList.add('active');
        }
    });

    const hasProject = !!AppState.currentProject;
    updateSidebarState(hasProject);
    updatePanelEmptyStates();
    if (hasProject) {
        updateTopBarProject(AppState.currentProject);
    }

    // 加载项目列表
    loadProjects();

    // 加载用户设置
    loadSettings();

    // 自动恢复项目状态（页面刷新后）
    if (AppState.currentProject) {
        openProject(AppState.currentProject, { skipPanelSwitch: true }).then(() => {
            showPanel(savedPanel);
            if (savedPanel === 'knowledge') {
                loadKnowledgeBase(AppState.currentProject);
            } else if (savedPanel === 'monitor') {
                refreshMonitor();
            } else if (savedPanel === 'issues') {
                loadIssuesForPanel();
            } else if (savedPanel === 'arc') {
                checkArcStatus(AppState.currentProject);
            } else if (savedPanel === 'chapter-plan') {
                loadChapterPlanForPanel(AppState.currentProject, AppState.currentChapter);
            }
        });
    } else {
        showPanel('works');
    }

    // 页面卸载时中断所有 pending 的流式请求，防止僵尸连接
    window.addEventListener('beforeunload', () => {
        if (AppState.currentAbortController) {
            AppState.currentAbortController.abort();
        }
    });

    // 温度滑块实时更新显示值
    const tempSlider = document.getElementById('setting-temperature');
    const tempValue = document.getElementById('temperature-value');
    if (tempSlider && tempValue) {
        tempSlider.addEventListener('input', () => {
            tempValue.textContent = tempSlider.value;
        });
    }

    // 绑定顶部汉堡菜单（切换侧边栏）
    const topBarIcon = document.querySelector('.top-bar-icon');
    if (topBarIcon) {
        topBarIcon.addEventListener('click', (e) => {
            e.preventDefault();
            toggleSidebar();
        });
    }

    // 绑定返回作品列表按钮
    const backBtn = document.getElementById('back-to-works-btn');
    if (backBtn) {
        backBtn.addEventListener('click', (e) => {
            e.preventDefault();
            backToWorks();
        });
    }

    // 绑定创建作品按钮
    const addBtn = document.getElementById('works-add-btn');
    if (addBtn) {
        addBtn.addEventListener('click', (e) => {
            e.preventDefault();
            openCreateModal();
        });
    }

    // 绑定创建项目表单提交
    const createForm = document.getElementById('create-project-form');
    if (createForm) {
        createForm.addEventListener('submit', async (e) => {
            e.preventDefault();

            const formData = new FormData(createForm);
            const projectData = {
                name: formData.get('name'),
                genre: formData.get('genre') || '玄幻',
                core_idea: formData.get('core_idea'),
                protagonist_seed: formData.get('protagonist_seed'),
                target_length: formData.get('target_length') || '中篇(10-50万)',
                tone: formData.get('tone') || '',
                style_reference: formData.get('style_reference') || '',
                forbidden_elements: (formData.get('forbidden_elements') || '').split(',').filter(Boolean)
            };

            try {
                await createProject(projectData);
                createForm.reset();
                closeCreateModal();
            } catch (error) {
                console.error('创建项目失败:', error);
            }
        });
    }

    // 绑定创建弧线表单提交
    const createArcForm = document.getElementById('create-arc-form');
    if (createArcForm) {
        createArcForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const submitBtn = createArcForm.querySelector('button[type="submit"]');
            if (submitBtn) {
                submitBtn.disabled = true;
                submitBtn.textContent = '创建中...';
            }
            const title = document.getElementById('arc-create-title').value;
            const start = document.getElementById('arc-create-start').value;
            const end = document.getElementById('arc-create-end').value;
            const desc = document.getElementById('arc-create-desc').value;
            try {
                await createArc(title, start, end, desc);
                closeCreateArcModal();
            } catch (error) {
                console.error('创建弧线失败:', error);
            } finally {
                if (submitBtn) {
                    submitBtn.disabled = false;
                    submitBtn.textContent = '创建并生成';
                }
            }
        });
    }

    // 绑定初始化向导按钮
    const bibleBtn = document.getElementById('generate-bible-btn');
    if (bibleBtn) {
        bibleBtn.addEventListener('click', (e) => {
            e.preventDefault();
            generateBible();
        });
    }

    const charBtn = document.getElementById('generate-characters-btn');
    if (charBtn) {
        charBtn.addEventListener('click', (e) => {
            e.preventDefault();
            generateCharacters();
        });
    }

    const outlineBtn = document.getElementById('generate-outline-btn');
    if (outlineBtn) {
        outlineBtn.addEventListener('click', (e) => {
            e.preventDefault();
            generateOutline();
        });
    }

    const enterBtn = document.getElementById('enter-writing-btn');
    if (enterBtn) {
        enterBtn.addEventListener('click', (e) => {
            e.preventDefault();
            if (AppState.currentProject) {
                showPanel('writing-panel');
                loadWritingInterface(AppState.currentProject);
            }
        });
    }

    // 绑定写作界面按钮
    const confirmBtn = document.getElementById('confirm-chapter-btn');
    if (confirmBtn) {
        confirmBtn.addEventListener('click', (e) => {
            e.preventDefault();
            confirmChapter();
        });
    }

    const kbBtn = document.getElementById('view-kb-btn');
    if (kbBtn) {
        kbBtn.addEventListener('click', (e) => {
            e.preventDefault();
            if (AppState.currentProject) {
                showPanel('knowledge-panel');
                loadKnowledgeBase(AppState.currentProject);
            }
        });
    }

    // 绑定侧边栏导航点击
    document.querySelectorAll('.sidebar-item').forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();

            if (item.classList.contains('disabled')) {
                showMessage('请先选择或创建一个作品', 'warning');
                return;
            }

            const panel = item.dataset.panel;
            if (AppState.currentProject && !AppState.projectInitialized && panel !== 'works' && panel !== 'settings') {
                showMessage('请先完成项目初始化向导', 'warning');
                return;
            }

            document.querySelectorAll('.sidebar-item').forEach(i => i.classList.remove('active'));
            item.classList.add('active');

            if (panel) {
                showPanel(panel + '-panel');
                if (panel === 'arc' && AppState.currentProject) {
                    checkArcStatus(AppState.currentProject);
                } else if (panel === 'monitor' && AppState.currentProject) {
                    refreshMonitor();
                } else if (panel === 'issues' && AppState.currentProject) {
                    loadIssuesForPanel();
                } else if (panel === 'knowledge' && AppState.currentProject) {
                    loadKnowledgeBase(AppState.currentProject);
                } else if (panel === 'chapter-plan' && AppState.currentProject) {
                    loadChapterPlanForPanel(AppState.currentProject, AppState.currentChapter);
                }
            }
        });
    });
});

// ============================================================
// 全局函数导出（供 HTML 内联事件处理器调用）
// ============================================================

window.openProject = openProject;
window.deleteProject = deleteProject;
window.openCreateModal = openCreateModal;
window.closeCreateModal = closeCreateModal;
window.generateScene = generateScene;
window.generateAllScenes = generateAllScenes;
window.editScene = editScene;
window.saveSceneEdit = saveSceneEdit;
window.cancelSceneEdit = cancelSceneEdit;
window.syncSceneKnowledge = syncSceneKnowledge;
window.toggleSidebar = toggleSidebar;
window.connectLogStream = connectLogStream;
window.disconnectLogStream = disconnectLogStream;
window.clearConsoleLog = clearConsoleLog;
window.setConsoleFilter = setConsoleFilter;
window.checkArcStatus = checkArcStatus;
window.openCreateArcModal = openCreateArcModal;
window.closeCreateArcModal = closeCreateArcModal;
window.suggestArcDetail = suggestArcDetail;
window.probeContext = probeContext;
window.closeDynamicModal = closeDynamicModal;
window.rewriteScene = rewriteScene;
window.loadExports = loadExports;
window.viewExport = viewExport;

// ============================================================
// 弧线/章节规划 UI
// ============================================================

/**
 * 打开创建弧线模态框。
 *
 * 自动计算建议章节范围：接续已有弧线末尾 +1，默认长度 50 章。
 */
async function openCreateArcModal() {
    const modal = document.getElementById('create-arc-modal');
    if (!modal) return;

    // 获取已有弧线列表计算建议范围
    let lastEnd = 0;
    try {
        const data = await apiRequest(`/api/projects/${AppState.currentProject}/arcs`);
        const arcs = data.arcs || [];
        for (const arc of arcs) {
            const cr = arc.chapter_range || [];
            if (cr.length >= 2 && cr[1] > lastEnd) lastEnd = cr[1];
        }
    } catch (e) {
        lastEnd = 0;
    }

    const nextStart = lastEnd > 0 ? lastEnd + 1 : 1;
    const nextEnd = nextStart + 49;

    const startInput = document.getElementById('arc-create-start');
    const endInput = document.getElementById('arc-create-end');
    if (startInput) startInput.value = nextStart;
    if (endInput) endInput.value = nextEnd;

    modal.style.display = 'flex';
}

/**
 * 关闭创建弧线模态框并重置表单。
 */
function closeCreateArcModal() {
    const modal = document.getElementById('create-arc-modal');
    if (modal) modal.style.display = 'none';
    const form = document.getElementById('create-arc-form');
    if (form) form.reset();
}

/**
 * 智能提示：基于已有大纲和弧线，为当前弧线推荐名称与描述。
 *
 * 调用后端 /arcs/suggest 接口，自动填充到创建弧线模态框。
 */
async function suggestArcDetail() {
    if (!AppState.currentProject) return;
    const btn = document.getElementById('arc-suggest-detail-btn');
    const startInput = document.getElementById('arc-create-start');
    const endInput = document.getElementById('arc-create-end');
    const titleInput = document.getElementById('arc-create-title');
    const descInput = document.getElementById('arc-create-desc');

    const start = parseInt(startInput?.value || '1', 10);
    const end = parseInt(endInput?.value || '50', 10);

    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="loading-spinner small"></span> 提示中...';
    }

    try {
        const data = await apiRequest(`/api/projects/${AppState.currentProject}/arcs/suggest`, {
            method: 'POST',
            body: JSON.stringify({ chapter_range: [start, end] })
        });
        const suggestion = data.suggestion || {};
        if (titleInput && !titleInput.value) titleInput.value = suggestion.title || '';
        if (descInput) descInput.value = suggestion.description || '';
        showMessage('已智能填充走向与名称', 'success');
    } catch (error) {
        showMessage('获取智能提示失败: ' + error.message, 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = `<svg viewBox="0 0 24 24" style="width:12px;height:12px;fill:currentColor;vertical-align:middle;margin-right:4px;">
                <path d="M9 21c0 .55.45 1 1 1h4c.55 0 1-.45 1-1v-1H9v1zm3-19C8.14 2 5 5.14 5 9c0 2.38 1.19 4.47 3 5.74V17c0 .55.45 1 1 1h6c.55 0 1-.45 1-1v-2.26c1.81-1.27 3-3.36 3-5.74 0-3.86-3.14-7-7-7zm2.85 11.1l-.85.6V16h-4v-2.3l-.85-.6A4.997 4.997 0 0 1 7 9c0-2.76 2.24-5 5-5s5 2.24 5 5c0 1.63-.8 3.16-2.15 4.1z"/>
            </svg>智能提示`;
        }
    }
}

/**
 * 创建弧线并立即生成规划。
 *
 * 先创建占位符弧线，成功后调用 generateArc() 触发 LLM 生成完整规划。
 *
 * @param {string} title - 弧线名称。
 * @param {string|number} startChapter - 起始章节。
 * @param {string|number} endChapter - 结束章节。
 * @param {string} description - 弧线描述。
 */
async function createArc(title, startChapter, endChapter, description) {
    if (!AppState.currentProject) return;
    let arcNumber;
    try {
        const result = await apiRequest(`/api/projects/${AppState.currentProject}/arcs`, {
            method: 'POST',
            body: JSON.stringify({
                title: title || '',
                chapter_range: [parseInt(startChapter, 10), parseInt(endChapter, 10)],
                description
            })
        });
        arcNumber = result.arc_number;
        showMessage(`弧线 ${arcNumber} 创建成功，开始生成规划...`, 'success');
        await checkArcStatus(AppState.currentProject);
        await generateArc(arcNumber);
    } catch (error) {
        showMessage('创建弧线失败: ' + error.message, 'error');
    }
}

/**
 * 删除弧线（需用户二次确认）。
 *
 * @param {number} arcNumber - 弧线序号。
 */
async function deleteArc(arcNumber) {
    if (!AppState.currentProject) return;
    if (!confirm(`确定要删除弧线 ${arcNumber} 吗？`)) return;
    try {
        await apiRequest(`/api/projects/${AppState.currentProject}/arcs/${arcNumber}`, {
            method: 'DELETE'
        });
        showMessage('弧线已删除', 'success');
        await checkArcStatus(AppState.currentProject);
    } catch (error) {
        showMessage('删除弧线失败: ' + error.message, 'error');
    }
}

/**
 * 智能推荐下一条弧线。
 *
 * 调用后端 /arcs/suggest 接口（无 chapter_range 约束），
 * 在弧线面板展示推荐卡片，用户可选择"采纳并创建"或"忽略"。
 */
async function suggestNextArc() {
    if (!AppState.currentProject) return;
    const btn = document.getElementById('suggest-arc-btn');
    const resultBox = document.getElementById('arc-suggest-result');
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="loading-spinner small"></span> 推荐中...';
    }
    try {
        const data = await apiRequest(`/api/projects/${AppState.currentProject}/arcs/suggest`, {
            method: 'POST'
        });
        const suggestion = data.suggestion || {};
        if (resultBox) {
            resultBox.style.display = 'block';
            resultBox.innerHTML = `
                <div style="background:var(--bg-hover);border:1px solid var(--border-subtle);border-radius:8px;padding:12px;">
                    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                        <strong>智能推荐：${escapeHtml(suggestion.title || '')}</strong>
                        <span style="font-size:12px;color:var(--text-secondary);">第 ${suggestion.chapter_range?.[0] || '?'} ~ ${suggestion.chapter_range?.[1] || '?'} 章</span>
                    </div>
                    <p style="margin:0 0 10px 0;font-size:13px;color:var(--text-secondary);">${escapeHtml(suggestion.description || '')}</p>
                    <div style="display:flex;gap:8px;">
                        <button class="mans-btn primary sm" onclick="createArc('${escapeHtml(suggestion.title || '').replace(/'/g, "\\'")}', ${suggestion.chapter_range?.[0] || 1}, ${suggestion.chapter_range?.[1] || 10}, '${escapeHtml(suggestion.description || '').replace(/'/g, "\\'")}')">采纳并创建</button>
                        <button class="mans-btn sm" onclick="document.getElementById('arc-suggest-result').style.display='none'">忽略</button>
                    </div>
                </div>
            `;
        }
    } catch (error) {
        showMessage('获取推荐失败: ' + error.message, 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = `<svg viewBox="0 0 24 24" style="width:14px;height:14px;fill:currentColor;vertical-align:middle;margin-right:4px;">
                <path d="M9 21c0 .55.45 1 1 1h4c.55 0 1-.45 1-1v-1H9v1zm3-19C8.14 2 5 5.14 5 9c0 2.38 1.19 4.47 3 5.74V17c0 .55.45 1 1 1h6c.55 0 1-.45 1-1v-2.26c1.81-1.27 3-3.36 3-5.74 0-3.86-3.14-7-7-7zm2.85 11.1l-.85.6V16h-4v-2.3l-.85-.6A4.997 4.997 0 0 1 7 9c0-2.76 2.24-5 5-5s5 2.24 5 5c0 1.63-.8 3.16-2.15 4.1z"/>
            </svg>智能推荐`;
        }
    }
}

/**
 * 生成指定弧线的完整规划。
 *
 * 调用后端 /generate/arc 接口，生成完成后刷新弧线列表。
 *
 * @param {number} arcNumber - 弧线序号。
 */
async function generateArc(arcNumber) {
    if (!AppState.currentProject) return;

    const card = document.querySelector(`.arc-card[data-arc="${arcNumber}"]`);
    const btn = card ? card.querySelector('button[onclick^="generateArc"]') : null;
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="loading-spinner small"></span> 生成中...';
    }

    let success = false;
    try {
        if (typeof startStreamingGeneration === 'function') {
            await startStreamingGeneration('arc', { arc_number: arcNumber });
            showMessage(`弧线 ${arcNumber} 规划生成成功！`, 'success');
            success = true;
        } else {
            const temperature = getSetting('temperature', 0.7);
            const result = await apiRequest(
                `/api/projects/${AppState.currentProject}/generate/arc?arc_number=${arcNumber}&temperature=${encodeURIComponent(temperature)}`,
                { method: 'POST' }
            );
            showMessage(`弧线 ${arcNumber} 规划生成成功！`, 'success');
            success = true;
            return result;
        }
    } catch (error) {
        showMessage('生成弧线规划失败: ' + error.message, 'error');
    } finally {
        if (AppState.currentProject) {
            await checkArcStatus(AppState.currentProject);
        }
    }
}

/**
 * 生成指定章节的规划。
 *
 * 调用后端 /generate/chapter 接口，成功后刷新写作面板与章节规划面板。
 *
 * @param {number} chapterNumber - 章节编号。
 */
async function generateChapterPlan(chapterNumber) {
    if (!AppState.currentProject) return;

    const btn = event?.target;
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="loading-spinner small"></span> 生成中...';
    }

    try {
        const temperature = getSetting('temperature', 0.7);
        const result = await apiRequest(
            `/api/projects/${AppState.currentProject}/generate/chapter?chapter_number=${chapterNumber}&temperature=${encodeURIComponent(temperature)}`,
            { method: 'POST' }
        );
        showMessage(`第 ${chapterNumber} 章规划生成成功！`, 'success');
        await loadChapterPlan(AppState.currentProject, chapterNumber);
        await loadChapterPlanForPanel(AppState.currentProject, chapterNumber);
        return result;
    } catch (error) {
        showMessage('生成章节规划失败: ' + error.message, 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = '生成章节规划';
        }
    }
}

// ============================================================
// 系统设置
// ============================================================

/**
 * 保存用户设置到 localStorage。
 *
 * 设置项：apiBase（服务地址）、temperature（生成温度）、retries（最大重试次数）、logLevel（日志级别）。
 */
function saveSettings() {
    const settings = {
        apiBase: document.getElementById('setting-api-base')?.value || '',
        temperature: parseFloat(document.getElementById('setting-temperature')?.value || '0.7'),
        retries: parseInt(document.getElementById('setting-retries')?.value || '3'),
        logLevel: document.getElementById('setting-log-level')?.value || 'INFO'
    };
    localStorage.setItem('mans_settings', JSON.stringify(settings));
    showMessage('设置已保存', 'success');
}

/**
 * 重置所有设置为默认值。
 */
function resetSettings() {
    const defaults = { apiBase: '', temperature: 0.7, retries: 3, logLevel: 'INFO' };
    localStorage.setItem('mans_settings', JSON.stringify(defaults));
    loadSettings();
    showMessage('设置已重置为默认值', 'success');
}

/**
 * 从 localStorage 加载设置并填充到设置面板表单。
 */
function loadSettings() {
    const stored = localStorage.getItem('mans_settings');
    const settings = stored ? JSON.parse(stored) : { apiBase: '', temperature: 0.7, retries: 3, logLevel: 'INFO' };

    const apiBase = document.getElementById('setting-api-base');
    const temperature = document.getElementById('setting-temperature');
    const tempValue = document.getElementById('temperature-value');
    const retries = document.getElementById('setting-retries');
    const logLevel = document.getElementById('setting-log-level');

    if (apiBase) apiBase.value = settings.apiBase || '';
    if (temperature) temperature.value = settings.temperature ?? 0.7;
    if (tempValue) tempValue.textContent = settings.temperature ?? 0.7;
    if (retries) retries.value = settings.retries ?? 3;
    if (logLevel) logLevel.value = settings.logLevel || 'INFO';
}

// ============================================================
// 实时监控（控制台日志 SSE）
// ============================================================

/**
 * 控制台状态对象。
 *
 * 字段说明：
 *     logs: 本地缓存的日志条目数组。
 *     filterLevel: 当前过滤级别（DEBUG/INFO/WARNING/ERROR）。
 *     connected: 是否已连接到后端 SSE 日志流。
 *     eventSource: 当前 EventSource 实例。
 *     autoScroll: 是否自动滚动到底部。
 */
const ConsoleState = {
    logs: [],
    filterLevel: 'INFO',
    connected: false,
    eventSource: null,
    autoScroll: true
};

/**
 * 日志级别优先级映射（用于过滤判断）。
 */
const LOG_LEVEL_ORDER = { 'DEBUG': 0, 'INFO': 1, 'WARNING': 2, 'ERROR': 3 };

/**
 * 向监控控制台追加一条日志。
 *
 * 根据当前过滤级别决定是否隐藏低优先级日志。
 * 若用户勾选了"自动滚动"，追加后自动滚动到底部。
 *
 * @param {string} level - 日志级别。
 * @param {string} message - 日志消息。
 * @param {string|null} time - 时间戳（可选，默认当前时间）。
 */
function appendConsoleLog(level, message, time = null) {
    const timestamp = time || new Date().toLocaleTimeString('zh-CN');
    const container = document.getElementById('console-output');
    if (!container) return;

    const logEntry = { level, message, timestamp };
    ConsoleState.logs.push(logEntry);

    const row = document.createElement('div');
    row.className = `console-log ${level.toLowerCase()}`;
    row.dataset.level = level;
    row.innerHTML = `
        <span class="console-log-time">${timestamp}</span>
        <span class="console-log-level">${level}</span>
        <span class="console-log-msg">${escapeHtml(message)}</span>
    `;

    // 根据过滤级别决定是否隐藏
    if (LOG_LEVEL_ORDER[level] < LOG_LEVEL_ORDER[ConsoleState.filterLevel]) {
        row.classList.add('hidden');
    }

    container.appendChild(row);

    // 自动滚动
    const autoScroll = document.getElementById('console-auto-scroll');
    if (autoScroll && autoScroll.checked) {
        container.scrollTop = container.scrollHeight;
    }
}

/**
 * 清空监控控制台所有日志。
 */
function clearConsoleLog() {
    const container = document.getElementById('console-output');
    if (container) container.innerHTML = '';
    ConsoleState.logs = [];
}

/**
 * 设置控制台日志过滤级别。
 *
 * 更新 ConsoleState.filterLevel，并遍历现有日志行显示/隐藏。
 *
 * @param {string} level - 目标过滤级别（DEBUG/INFO/WARNING/ERROR）。
 */
function setConsoleFilter(level) {
    ConsoleState.filterLevel = level;
    const container = document.getElementById('console-output');
    if (!container) return;

    container.querySelectorAll('.console-log').forEach(row => {
        const rowLevel = row.dataset.level;
        if (LOG_LEVEL_ORDER[rowLevel] < LOG_LEVEL_ORDER[level]) {
            row.classList.add('hidden');
        } else {
            row.classList.remove('hidden');
        }
    });
}

/**
 * 更新控制台连接状态 UI。
 *
 * @param {string|null} status - CSS 类名：connected / connecting / error。
 * @param {string} text - 状态文本。
 */
function updateConsoleConnection(status, text) {
    const el = document.getElementById('console-connection-status');
    const btn = document.getElementById('console-connect-btn');
    if (el) {
        el.className = 'console-connection-status';
        if (status) el.classList.add(status);
        el.textContent = text;
    }
    if (btn) {
        btn.textContent = ConsoleState.connected ? '断开' : '连接';
        btn.onclick = ConsoleState.connected ? disconnectLogStream : connectLogStream;
    }
}

/**
 * 连接后端日志 SSE 流。
 *
 * 首次连接时安装全局错误捕获代理：
 *     1. 代理 console.error，将前端 JS 错误也显示在监控面板。
 *     2. 捕获 window.onerror 同步错误。
 *     3. 捕获 unhandledrejection Promise 拒绝。
 *
 * SSE 连接特性：
 *     - 使用浏览器原生 EventSource（GET 请求，适合只读日志流）。
 *     - 连接错误时仅报告一次，让浏览器自动重连（EventSource 内置机制）。
 *     - 心跳由后端通过 ping 事件维持。
 *
 * @param {string} level - 初始日志过滤级别，默认 INFO。
 */
function connectLogStream(level = 'INFO') {
    if (!AppState.currentProject) {
        showMessage('请先选择项目', 'warning');
        return;
    }

    if (ConsoleState.connected && ConsoleState.eventSource) {
        showMessage('日志流已连接', 'info');
        return;
    }

    // 首次连接时安装全局错误捕获（仅一次）
    if (!ConsoleState._errorHandlersInstalled) {
        ConsoleState._errorHandlersInstalled = true;

        // 代理 console.error
        const originalConsoleError = console.error;
        console.error = function(...args) {
            originalConsoleError.apply(console, args);
            const message = args.map(a => {
                if (a instanceof Error) return a.stack || a.message;
                if (typeof a === 'object') try { return JSON.stringify(a); } catch (e) { return String(a); }
                return String(a);
            }).join(' ');
            appendConsoleLog('ERROR', `[Console] ${message}`);
        };

        // 捕获未处理的同步错误
        window.onerror = function(message, source, lineno, colno, error) {
            const detail = error && error.stack ? error.stack : `${message} at ${source}:${lineno}:${colno}`;
            appendConsoleLog('ERROR', `[Uncaught] ${detail}`);
        };

        // 捕获未处理的 Promise 拒绝
        window.addEventListener('unhandledrejection', function(event) {
            const reason = event.reason;
            const message = reason instanceof Error ? (reason.stack || reason.message) : String(reason);
            appendConsoleLog('ERROR', `[UnhandledRejection] ${message}`);
        });
    }

    try {
        updateConsoleConnection('connecting', '连接中...');

        const base = getApiBase().replace(/\/$/, '');
        const url = base
            ? `${base}/api/projects/${AppState.currentProject}/stream/logs?level=${encodeURIComponent(level)}`
            : `/api/projects/${AppState.currentProject}/stream/logs?level=${encodeURIComponent(level)}`;
        const es = new EventSource(url);
        ConsoleState.eventSource = es;

        // 仅记录一次错误，不关闭 EventSource，让浏览器自动重连
        let errorReported = false;

        es.onopen = () => {
            appendConsoleLog('INFO', '日志流连接成功');
            ConsoleState.connected = true;
            errorReported = false;
            updateConsoleConnection('connected', '已连接');
        };

        es.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                if (data.level && data.message) {
                    appendConsoleLog(data.level, data.message, data.time);
                } else if (data.message) {
                    appendConsoleLog('INFO', data.message, data.time);
                }
            } catch (e) {
                appendConsoleLog('INFO', event.data);
            }
        };

        es.addEventListener('log', (event) => {
            try {
                const data = JSON.parse(event.data);
                if (data.level && data.message) {
                    appendConsoleLog(data.level, data.message, data.time);
                }
            } catch (e) {
                appendConsoleLog('INFO', event.data);
            }
        });

        es.addEventListener('ping', () => {
            // 心跳保活，无需处理
        });

        es.onerror = (error) => {
            if (!errorReported) {
                console.error('日志流连接错误:', error);
                updateConsoleConnection('error', '连接中断，尝试重连...');
                appendConsoleLog('ERROR', '日志流连接中断，正在自动重连...');
                errorReported = true;
            }
            ConsoleState.connected = false;
            // 保留 eventSource 引用，浏览器会自动重连
        };

    } catch (error) {
        updateConsoleConnection('error', '连接失败');
        showMessage('连接日志流失败: ' + error.message, 'error');
    }
}

/**
 * 断开日志 SSE 流连接。
 */
function disconnectLogStream() {
    if (ConsoleState.eventSource) {
        ConsoleState.eventSource.close();
        ConsoleState.eventSource = null;
    }
    if (ConsoleState.mockInterval) {
        clearInterval(ConsoleState.mockInterval);
        ConsoleState.mockInterval = null;
    }
    ConsoleState.connected = false;
    updateConsoleConnection('', '未连接');
    appendConsoleLog('INFO', '日志流已断开');
}

/**
 * 刷新监控面板（兼容旧调用）。
 *
 * 若尚未连接日志流，自动尝试连接；否则追加一条手动刷新标记。
 */
async function refreshMonitor() {
    if (!AppState.currentProject) {
        showMessage('请先选择项目', 'warning');
        return;
    }
    if (!ConsoleState.connected) {
        connectLogStream();
    } else {
        appendConsoleLog('INFO', '手动刷新监控数据');
    }
}

// 补充导出监控相关函数到 window
window.generateArc = generateArc;
window.generateChapterPlan = generateChapterPlan;
window.saveSettings = saveSettings;
window.resetSettings = resetSettings;
window.refreshMonitor = refreshMonitor;
window.createArc = createArc;
window.deleteArc = deleteArc;
window.suggestNextArc = suggestNextArc;
window.rollbackSceneKnowledge = rollbackSceneKnowledge;
window.startRewrite = startRewrite;

/**
 * 加载 Issues 到 Issue Pool 面板。
 *
 * 从后端 /issues 接口获取全部 issues，按紧急程度渲染为带颜色边框的卡片列表。
 */
async function loadIssuesForPanel() {
    if (!AppState.currentProject) {
        showMessage('请先选择项目', 'warning');
        return;
    }

    const container = document.getElementById('issues-panel-list');
    if (container) {
        container.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;gap:8px;padding:20px;"><div class="loading-spinner small"></div><span style="color:var(--text-secondary);">加载中...</span></div>';
    }

    try {
        const data = await apiRequest(`/api/projects/${AppState.currentProject}/issues`);
        const issues = data.issues || [];

        if (!container) return;

        if (issues.length === 0) {
            container.innerHTML = '<p style="color: var(--text-secondary); text-align: center; padding: 12px;">暂无待处理 Issues，一切正常！</p>';
            return;
        }

        const urgencyColors = {
            'critical': 'var(--error)',
            'major': 'var(--warning)',
            'medium': 'var(--info)',
            'minor': 'var(--text-secondary)'
        };

        container.innerHTML = issues.map(issue => `
            <div style="display: flex; align-items: center; gap: 12px; padding: 12px; margin-bottom: 8px;
                        background: var(--bg-dark); border-radius: 6px; border-left: 3px solid ${urgencyColors[issue.urgency] || 'var(--text-secondary)'};">
                <span style="font-size: 11px; color: var(--text-secondary); min-width: 80px;">${escapeHtml(issue.type || '')}</span>
                <span style="flex: 1; font-size: 13px;">${escapeHtml(issue.description || '')}</span>
                <span style="font-size: 11px; padding: 2px 8px; border-radius: 4px;
                             background: ${issue.status === 'resolved' ? 'rgba(34,197,94,0.15)' : 'rgba(234,179,8,0.15)'};
                             color: ${issue.status === 'resolved' ? 'var(--success)' : 'var(--warning)'}">${escapeHtml(issue.status || '')}</span>
            </div>
        `).join('');
    } catch (error) {
        if (container) {
            container.innerHTML = '<p class="error" style="text-align:center;">加载 Issues 失败</p>';
        }
    }
}

window.loadIssuesForPanel = loadIssuesForPanel;
