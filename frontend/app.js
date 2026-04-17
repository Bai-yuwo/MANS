/**
 * MANS Frontend - 前端交互逻辑
 *
 * 功能：
 * 1. 项目管理（创建、列表、删除）
 * 2. 初始化流程（Bible、人物、大纲生成）
 * 3. 写作界面（场景生成、流式显示、编辑）
 * 4. 知识库查看
 */

// ============================================
// 全局状态
// ============================================

const AppState = {
    currentProject: localStorage.getItem('mans_current_project') || null,
    currentChapter: parseInt(localStorage.getItem('mans_current_chapter') || '1', 10),
    currentScene: 0,
    isGenerating: false,
    projectInitialized: false
};

// ============================================
// 设置读取
// ============================================

function getSetting(key, defaultValue) {
    const stored = localStorage.getItem('mans_settings');
    const settings = stored ? JSON.parse(stored) : {};
    return settings[key] !== undefined ? settings[key] : defaultValue;
}

function getApiBase() {
    return getSetting('apiBase', '');
}

// ============================================
// 工具函数
// ============================================

/**
 * 发送API请求（支持动态 API_BASE 和重试）
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
 * 显示消息（Toast通知）
 */
function showMessage(message, type = 'info') {
    console.log(`[${type}] ${message}`);

    // 创建Toast通知
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

    // 根据类型设置背景色
    const colors = {
        'success': '#10b981',
        'error': '#ef4444',
        'warning': '#f59e0b',
        'info': '#3b82f6'
    };
    toast.style.backgroundColor = colors[type] || colors['info'];

    document.body.appendChild(toast);

    // 3秒后自动消失
    setTimeout(() => {
        toast.style.animation = 'slideOut 0.3s ease';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

/**
 * 检查异步知识库更新（生成/重写后调用）
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
        // 静默忽略轮询错误
    }
}

/**
 * 渲染 Issue Pool 悬浮角标
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
 * 刷新 Issue Pool 角标计数
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
        // ignore
    }
}

// 添加CSS动画
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
 * 打开创建项目模态框
 */
function openCreateModal() {
    const modal = document.getElementById('create-project-modal');
    if (modal) {
        modal.style.display = 'flex';
    }
}

/**
 * 关闭创建项目模态框
 */
function closeCreateModal() {
    const modal = document.getElementById('create-project-modal');
    if (modal) {
        modal.style.display = 'none';
    }
}

/**
 * 格式化字数
 */
function formatWordCount(count) {
    if (count >= 10000) {
        return (count / 10000).toFixed(1) + '万';
    }
    return count.toString();
}

// ============================================
// 状态持久化与导航
// ============================================

function updateSidebarState(hasProject) {
    document.querySelectorAll('.sidebar-item[data-requires-project="true"]').forEach(item => {
        item.classList.toggle('disabled', !hasProject);
    });
}

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
 * 渲染弧线列表
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

    // 按弧线序号排序
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
 * 检查弧线生成状态（动态列表）
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

// ============================================
// 项目管理
// ============================================

/**
 * 加载项目列表
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
 * 更新项目列表UI
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
                <button onclick="openProject('${escapeHtml(project.id)}')" class="mans-btn primary">打开</button>
                <button onclick="deleteProject('${escapeHtml(project.id)}')" class="mans-btn danger">删除</button>
            </div>
        </div>
    `;
    }).join('');
}

/**
 * 获取状态文本
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
 * 创建项目
 */
async function createProject(projectData) {
    try {
        // 防止重复提交：禁用创建按钮
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
        // 恢复按钮状态
        const submitBtn = document.querySelector('#create-project-form button[type="submit"]');
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.textContent = '创建';
        }
    }
}

/**
 * 删除项目
 */
async function deleteProject(projectId) {
    if (!confirm('确定要删除这个项目吗？此操作不可恢复。')) {
        return;
    }

    try {
        await apiRequest(`/api/projects/${projectId}`, {
            method: 'DELETE'
        });

        showMessage('项目已删除');
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
 * 打开项目
 */
async function openProject(projectId, options = {}) {
    AppState.currentProject = projectId;
    localStorage.setItem('mans_current_project', projectId);
    updateSidebarState(true);
    updateTopBarProject(projectId);
    updatePanelEmptyStates();

    try {
        // 获取项目状态
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

// ============================================
// 初始化流程
// ============================================

/**
 * 检查初始化状态
 */
async function checkInitializationStatus(projectId) {
    const steps = ['bible-step', 'character-step', 'outline-step'];

    try {
        const status = await apiRequest(`/api/projects/${projectId}/status`);

        // 更新UI状态 - 步骤完成标记
        updateInitStepStatus('bible-step', status.has_bible);
        updateInitStepStatus('character-step', status.has_characters);
        updateInitStepStatus('outline-step', status.has_outline);

        // 移除加载指示器
        steps.forEach(stepId => {
            const step = document.getElementById(stepId);
            if (step) {
                const spinners = step.querySelectorAll('.loading-spinner');
                spinners.forEach(s => s.remove());
            }
        });

        // 获取按钮
        const bibleBtn = document.getElementById('generate-bible-btn');
        const charBtn = document.getElementById('generate-characters-btn');
        const outlineBtn = document.getElementById('generate-outline-btn');
        const enterWritingBtn = document.getElementById('enter-writing-btn');

        // 更新按钮状态
        if (bibleBtn) {
            bibleBtn.disabled = status.has_bible;
        }

        if (charBtn) {
            charBtn.disabled = !status.has_bible || status.has_characters;
        }

        if (outlineBtn) {
            outlineBtn.disabled = !status.has_characters || status.has_outline;
        }

        // 如果全部完成，显示进入写作按钮
        if (status.initialized && enterWritingBtn) {
            enterWritingBtn.style.display = 'inline-flex';
        } else if (enterWritingBtn) {
            enterWritingBtn.style.display = 'none';
        }

        AppState.projectInitialized = status.initialized;

        // 如果初始化刚完成且用户仍在向导页，自动进入写作界面
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
 * 更新初始化步骤状态
 */
function updateInitStepStatus(stepId, completed) {
    const step = document.getElementById(stepId);
    if (step) {
        step.classList.toggle('completed', completed);
        step.classList.toggle('pending', !completed);
    }
}

/**
 * 生成 Bible（流式版本）
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
            showMessage('Bible 生成成功！');
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
 * 加载并显示Bible
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
 * 显示 Bible
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
 * 生成人物
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
            showMessage('人物生成成功！');
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
 * 生成大纲（流式版本）
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
            showMessage('大纲生成成功！');
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

// ============================================
// 写作界面
// ============================================

/**
 * 加载写作界面
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
 * 加载章节规划
 */
async function loadChapterPlan(projectId, chapterNum) {
    try {
        const plan = await apiRequest(
            `/api/projects/${projectId}/chapters/${chapterNum}/plan`
        );
        displayChapterPlan(plan);
        // 规划加载后，尝试加载已保存的草稿正文
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
 * 加载章节草稿正文并填充到场景卡片
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
                contentDiv.innerHTML = `<div class="generated-text">${escapeHtml(scene.text)}</div>`;
            }
        }
    } catch (error) {
        // 404 表示草稿不存在，属于正常情况，无需提示
        if (error.status !== 404) {
            console.error('加载章节草稿失败:', error);
        }
    }
}

/**
 * 显示章节规划
 */
function displayChapterPlan(plan) {
    const container = document.getElementById('chapter-plan-display');
    if (!container) return;

    container.innerHTML = buildChapterPlanHtml(plan, true);
}

/**
 * 构建章节规划 HTML（复用）
 */
function buildChapterPlanHtml(plan, showWritingActions = false) {
    const sceneCards = (plan.scenes || []).map((scene) => `
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
        <div class="scenes-list">
            ${sceneCards}
        </div>
    `;
}

/**
 * 加载章节规划到章节规划面板
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
 * 显示章节规划到章节规划面板
 */
function displayChapterPlanResult(plan) {
    const container = document.getElementById('chapter-plan-result');
    if (!container) return;

    container.innerHTML = buildChapterPlanHtml(plan, false);
}

/**
 * 生成场景
 */
/**
 * 同步所有场景生成按钮状态
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

async function generateScene(sceneIndex) {
    if (!AppState.currentProject || AppState.isGenerating) return;

    AppState.isGenerating = true;
    AppState.currentScene = sceneIndex;
    updateAllSceneButtons();

    const contentDiv = document.getElementById(`scene-content-${sceneIndex}`);
    contentDiv.classList.add('locked');
    contentDiv.innerHTML = '<div class="generating">正在生成...</div>';

    try {
        // 直接连接 SSE 流开始生成
        await connectStream(sceneIndex, contentDiv);
        // 生成完成后检查异步知识库更新
        checkAsyncUpdates(AppState.currentChapter);
    } catch (error) {
        contentDiv.innerHTML = `<div class="error">生成失败: ${escapeHtml(error.message)}</div>`;
    } finally {
        AppState.isGenerating = false;
        contentDiv.classList.remove('locked');
        updateAllSceneButtons();
    }
}

/**
 * 连接流式生成（fetch + ReadableStream）
 * @returns {Promise<void>}
 */
async function connectStream(sceneIndex, contentDiv) {
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
        body: JSON.stringify({ temperature })
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
                        contentDiv.innerHTML = `<div class="generating">开始生成场景 ${data.scene_index + 1}：${escapeHtml(data.intent)}</div>`;
                        break;
                    }
                    case 'token': {
                        const data = JSON.parse(currentEvent.data);
                        generatedText += data.content;
                        contentDiv.innerHTML = `<div class="generated-text">${escapeHtml(generatedText)}</div>`;
                        contentDiv.scrollTop = contentDiv.scrollHeight;
                        break;
                    }
                    case 'progress': {
                        // 可选：在UI中显示进度
                        break;
                    }
                    case 'scene_complete': {
                        const data = JSON.parse(currentEvent.data);
                        showMessage(`场景生成完成！字数: ${data.word_count}`);
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
                        // ping 等忽略
                        break;
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
                buffer = lines.pop(); // 保留不完整的行

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
 * 打开重写反馈模态框
 */
function rewriteScene(sceneIndex) {
    if (AppState.isGenerating) {
        showMessage('AI 正在生成中，请稍后再操作', 'warning');
        return;
    }
    const html = `
        <div>
            <p style="margin-bottom: 12px; color: var(--text-secondary);">描述当前场景的问题或修改方向：</p>
            <textarea id="rewrite-feedback-${sceneIndex}" rows="4"
                style="width: 100%; background: var(--bg-dark); border: 1px solid var(--bg-hover); border-radius: 6px; padding: 8px; color: var(--text-primary);"
                placeholder="例如：节奏太快，缺少环境描写..."></textarea>
            <div style="margin-top: 12px; text-align: right;">
                <button class="mans-btn primary" onclick="startRewrite(${sceneIndex})">开始重写</button>
            </div>
        </div>
    `;
    showModal('重写场景', html);
}

/**
 * 开始流式重写
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
 * 连接重写流
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
                        contentDiv.innerHTML = `<div class="generated-text">${escapeHtml(generatedText)}</div>`;
                        contentDiv.scrollTop = contentDiv.scrollHeight;
                        break;
                    }
                    case 'progress': break;
                    case 'scene_complete': {
                        const data = JSON.parse(currentEvent.data);
                        showMessage(`场景重写完成！字数: ${data.word_count}`);
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
 * HTML转义
 */
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/**
 * 编辑场景
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
        </div>
        <div class="save-status" id="save-status-${sceneIndex}"></div>
    `;

    const textarea = document.getElementById(`edit-scene-${sceneIndex}`);

    const triggerAutoSave = () => {
        const statusDiv = document.getElementById(`save-status-${sceneIndex}`);
        if (statusDiv) {
            statusDiv.textContent = '正在保存...';
            statusDiv.className = 'save-status saving';
        }
        saveSceneEdit(sceneIndex, { silent: true }).then(() => {
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
 * 保存场景编辑
 */
async function saveSceneEdit(sceneIndex, options = {}) {
    const { silent = false } = options;
    const contentDiv = document.getElementById(`scene-content-${sceneIndex}`);
    if (contentDiv && contentDiv._autoSaveTimer) {
        clearTimeout(contentDiv._autoSaveTimer);
        contentDiv._autoSaveTimer = null;
    }

    const textarea = document.getElementById(`edit-scene-${sceneIndex}`);
    if (!textarea) return; // 编辑器已关闭，跳过
    const newText = textarea.value;

    try {
        await apiRequest(
            `/api/projects/${AppState.currentProject}/chapters/${AppState.currentChapter}/scenes/${sceneIndex}`,
            {
                method: 'PUT',
                body: JSON.stringify({ text: newText })
            }
        );

        if (!silent) {
            showMessage('场景已保存');
        }
        if (contentDiv) {
            contentDiv.innerHTML = `<div class="generated-text">${escapeHtml(newText)}</div>`;
        }
    } catch (error) {
        if (!silent) {
            showMessage('保存失败: ' + error.message, 'error');
        }
        throw error;
    }
}

/**
 * 取消场景编辑
 */
function cancelSceneEdit(sceneIndex) {
    const contentDiv = document.getElementById(`scene-content-${sceneIndex}`);
    if (contentDiv && contentDiv._autoSaveTimer) {
        clearTimeout(contentDiv._autoSaveTimer);
        contentDiv._autoSaveTimer = null;
    }
    const originalContent = contentDiv.dataset.originalContent || '';
    contentDiv.innerHTML = `<div class="generated-text">${escapeHtml(originalContent)}</div>`;
    delete contentDiv.dataset.originalContent;
}

/**
 * 上下文探针：查看当前场景注入上下文摘要
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
 * 显示通用模态框
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

function closeDynamicModal() {
    const modal = document.getElementById('dynamic-modal');
    if (modal) modal.remove();
}

/**
 * 确认章节完稿
 */
async function confirmChapter() {
    if (!AppState.currentProject) return;

    if (!confirm('确认本章已完成？确认后将进入下一章。')) {
        return;
    }

    try {
        const result = await apiRequest(
            `/api/projects/${AppState.currentProject}/chapters/${AppState.currentChapter}/confirm`,
            { method: 'POST' }
        );

        showMessage(`第${AppState.currentChapter}章已确认！字数: ${result.word_count}`);
        // 刷新导出列表（完稿后会在 exports/ 生成只读 md）
        await loadExports();

        AppState.currentChapter++;
        localStorage.setItem('mans_current_chapter', String(AppState.currentChapter));
        await loadChapterPlan(AppState.currentProject, AppState.currentChapter);

    } catch (error) {
        showMessage('确认章节失败: ' + error.message, 'error');
    }
}

/**
 * 加载已导出的文件列表
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
        const rows = data.exports.map(exp => `
            <div class="export-item" onclick="viewExport('${escapeHtml(exp.filename)}')"
                 style="display:flex;justify-content:space-between;align-items:center;padding:8px 12px;
                        border-bottom:1px solid var(--bg-hover);cursor:pointer;"
                 onmouseenter="this.style.background='var(--bg-hover)';"
                 onmouseleave="this.style.background='transparent';"
            >
                <span>📄 ${escapeHtml(exp.filename)}</span>
                <span style="color:var(--text-secondary);font-size:12px;">${exp.size} 字</span>
            </div>
        `).join('');
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
 * 查看导出的只读文件内容
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

// ============================================
// 知识库查看
// ============================================

/**
 * 加载知识库
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
 * 格式化大纲为可读 HTML
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
 * 显示知识库
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

// ============================================
// UI 控制
// ============================================

/**
 * 显示面板
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
 * 切换侧边栏
 */
function toggleSidebar() {
    const sidebar = document.querySelector('.sidebar');
    if (sidebar) {
        sidebar.classList.toggle('collapsed');
    }
}

// ============================================
// 初始化
// ============================================

document.addEventListener('DOMContentLoaded', () => {
    // 恢复 sidebar 激活状态
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

    // 加载设置
    loadSettings();

    // 自动恢复项目状态
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

    // 温度滑块实时更新
    const tempSlider = document.getElementById('setting-temperature');
    const tempValue = document.getElementById('temperature-value');
    if (tempSlider && tempValue) {
        tempSlider.addEventListener('input', () => {
            tempValue.textContent = tempSlider.value;
        });
    }

    // 绑定顶部菜单按钮
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

    // 绑定创建项目表单
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

    // 绑定创建弧线表单
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

    // 绑定初始化按钮
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

    // 绑定侧边栏导航
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

// 导出全局函数（供HTML内联事件使用）
window.openProject = openProject;
window.deleteProject = deleteProject;
window.openCreateModal = openCreateModal;
window.closeCreateModal = closeCreateModal;
window.generateScene = generateScene;
window.editScene = editScene;
window.saveSceneEdit = saveSceneEdit;
window.cancelSceneEdit = cancelSceneEdit;
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

// ============================================
// 弧线/章节规划 UI
// ============================================

async function openCreateArcModal() {
    const modal = document.getElementById('create-arc-modal');
    if (!modal) return;

    // 获取已有弧线列表以计算建议章节范围
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

function closeCreateArcModal() {
    const modal = document.getElementById('create-arc-modal');
    if (modal) modal.style.display = 'none';
    const form = document.getElementById('create-arc-form');
    if (form) form.reset();
}

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
        // 直接生成完整弧线规划
        await generateArc(arcNumber);
    } catch (error) {
        showMessage('创建弧线失败: ' + error.message, 'error');
    }
}

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
 * 生成弧线规划
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
 * 生成章节规划
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

// ============================================
// 系统设置
// ============================================

/**
 * 保存设置
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
 * 重置设置
 */
function resetSettings() {
    const defaults = { apiBase: '', temperature: 0.7, retries: 3, logLevel: 'INFO' };
    localStorage.setItem('mans_settings', JSON.stringify(defaults));
    loadSettings();
    showMessage('设置已重置为默认值', 'success');
}

/**
 * 加载设置
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

// ============================================
// 实时监控（控制台日志）
// ============================================

const ConsoleState = {
    logs: [],
    filterLevel: 'INFO',
    connected: false,
    eventSource: null,
    autoScroll: true
};

const LOG_LEVEL_ORDER = { 'DEBUG': 0, 'INFO': 1, 'WARNING': 2, 'ERROR': 3 };

/**
 * 向控制台添加日志
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
 * 清空控制台
 */
function clearConsoleLog() {
    const container = document.getElementById('console-output');
    if (container) container.innerHTML = '';
    ConsoleState.logs = [];
}

/**
 * 设置日志过滤级别
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
 * 更新连接状态UI
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
 * 连接日志流（SSE）
 * 接口: /api/projects/{project_id}/stream/logs
 */
function connectLogStream() {
    if (!AppState.currentProject) {
        showMessage('请先选择项目', 'warning');
        return;
    }

    if (ConsoleState.connected && ConsoleState.eventSource) {
        showMessage('日志流已连接', 'info');
        return;
    }

    // 安装全局错误捕获（仅一次）
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

        const level = document.getElementById('console-log-level')?.value || 'INFO';
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
                // 非 JSON 数据直接显示
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
 * 断开日志流
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
 * 刷新监控面板（兼容旧调用）
 */
async function refreshMonitor() {
    if (!AppState.currentProject) {
        showMessage('请先选择项目', 'warning');
        return;
    }
    // 若尚未连接，自动尝试连接
    if (!ConsoleState.connected) {
        connectLogStream();
    } else {
        appendConsoleLog('INFO', '手动刷新监控数据');
    }
}

window.generateArc = generateArc;
window.generateChapterPlan = generateChapterPlan;
window.saveSettings = saveSettings;
window.resetSettings = resetSettings;
window.refreshMonitor = refreshMonitor;
window.createArc = createArc;
window.deleteArc = deleteArc;
window.suggestNextArc = suggestNextArc;

/**
 * 加载 Issues 到 Issue Pool 面板
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
