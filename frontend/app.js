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
    currentProject: null,
    currentChapter: 1,
    currentScene: 0,
    isGenerating: false,
    eventSource: null
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
                throw new Error(error.detail || `HTTP ${response.status}`);
            }

            return response.json();
        } catch (error) {
            lastError = error;
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

        // 更新项目列表显示
        updateProjectList(projects);
        
    } catch (error) {
        console.error('加载项目列表失败:', error);
        if (container) {
            container.innerHTML = '<p class="error" style="text-align:center;padding:20px;">加载项目失败，请刷新重试</p>';
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
        container.innerHTML = '<p class="empty">暂无项目，请创建新作品</p>';
        return;
    }
    
    container.innerHTML = projects.map(project => `
        <div class="project-card" data-id="${escapeHtml(project.id)}">
            <h3>${escapeHtml(project.name)}</h3>
            <p class="genre">${escapeHtml(project.genre)}</p>
            <p class="status">状态: ${escapeHtml(getStatusText(project.status))}</p>
            <p class="chapter">当前章节: ${escapeHtml(String(project.current_chapter))}</p>
            <div class="actions">
                <button onclick="openProject('${escapeHtml(project.id)}')">打开</button>
                <button onclick="deleteProject('${escapeHtml(project.id)}')" class="danger">删除</button>
            </div>
        </div>
    `).join('');
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
        await loadProjects();
        
    } catch (error) {
        showMessage('删除项目失败: ' + error.message, 'error');
    }
}

/**
 * 打开项目
 */
async function openProject(projectId) {
    AppState.currentProject = projectId;
    
    try {
        // 获取项目状态
        const status = await apiRequest(`/api/projects/${projectId}/status`);
        
        if (!status.initialized) {
            // 进入初始化流程
            showPanel('initialization-panel');
            await checkInitializationStatus(projectId);
        } else {
            // 进入写作界面
            showPanel('writing-panel');
            await loadWritingInterface(projectId);
        }
        
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
        // Bible: 已完成则禁用，否则启用
        if (bibleBtn) {
            bibleBtn.disabled = status.has_bible;
        }
        
        // 人物: 需要Bible完成，且人物未完成
        if (charBtn) {
            charBtn.disabled = !status.has_bible || status.has_characters;
        }
        
        // 大纲: 需要人物完成，且大纲未完成
        if (outlineBtn) {
            outlineBtn.disabled = !status.has_characters || status.has_outline;
        }
        
        // 如果全部完成，显示进入写作按钮
        if (status.initialized && enterWritingBtn) {
            enterWritingBtn.style.display = 'inline-flex';
        }
        
        console.log('初始化状态已更新:', {
            has_bible: status.has_bible,
            has_characters: status.has_characters,
            has_outline: status.has_outline,
            initialized: status.initialized
        });
        
    } catch (error) {
        console.error('检查初始化状态失败:', error);
        // 出错时也移除加载指示器
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
        // 使用流式生成
        if (typeof startStreamingGeneration === 'function') {
            await startStreamingGeneration('bible');
            showMessage('Bible 生成成功！', 'success');
        } else {
            // 后备方案：使用非流式API
            const result = await apiRequest(
                `/api/projects/${AppState.currentProject}/generate/bible`,
                { method: 'POST' }
            );
            showMessage('Bible 生成成功！');
            displayBible(result.data);
        }
        
        // 确保Bible显示出来
        await loadAndDisplayBible();
        
        // 刷新初始化状态（会正确设置按钮的 disabled 状态）
        await checkInitializationStatus(AppState.currentProject);
        // checkInitializationStatus 已根据 has_bible 设置按钮状态，不再手动重置
        
    } catch (error) {
        showMessage('生成 Bible 失败: ' + error.message, 'error');
        // 只在出错时恢复按钮
        if (btn) {
            btn.disabled = false;
            btn.textContent = '生成 Bible';
        }
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
        // 使用流式生成
        if (typeof startStreamingGeneration === 'function') {
            await startStreamingGeneration('characters');
            showMessage('人物生成成功！', 'success');
        } else {
            // 后备方案
            const result = await apiRequest(
                `/api/projects/${AppState.currentProject}/generate/characters`,
                { method: 'POST' }
            );
            showMessage('人物生成成功！');
        }

        // 刷新初始化状态（会正确设置按钮的 disabled 状态）
        await checkInitializationStatus(AppState.currentProject);

    } catch (error) {
        showMessage('生成人物失败: ' + error.message, 'error');
        // 只在出错时恢复按钮
        if (btn) {
            btn.disabled = false;
            btn.textContent = '生成人物';
        }
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
        // 使用流式生成
        if (typeof startStreamingGeneration === 'function') {
            await startStreamingGeneration('outline');
            showMessage('大纲生成成功！', 'success');
        } else {
            // 后备方案
            const result = await apiRequest(
                `/api/projects/${AppState.currentProject}/generate/outline`,
                { method: 'POST' }
            );
            showMessage('大纲生成成功！');
        }

        // 刷新初始化状态（会正确设置按钮的 disabled 状态）
        await checkInitializationStatus(AppState.currentProject);

    } catch (error) {
        showMessage('生成大纲失败: ' + error.message, 'error');
        // 只在出错时恢复按钮
        if (btn) {
            btn.disabled = false;
            btn.textContent = '生成大纲';
        }
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
        // 获取项目信息
        const project = await apiRequest(`/api/projects/${projectId}`);
        AppState.currentChapter = project.current_chapter || 1;
        
        // 加载当前章节规划
        await loadChapterPlan(projectId, AppState.currentChapter);
        
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
        
    } catch (error) {
        // 章节规划不存在，可能需要生成
        console.log('章节规划不存在:', error);
        document.getElementById('chapter-plan-display').innerHTML = `
            <p class="empty">第${escapeHtml(String(chapterNum))}章规划不存在，请先生成弧线规划</p>
        `;
    }
}

/**
 * 显示章节规划
 */
function displayChapterPlan(plan) {
    const container = document.getElementById('chapter-plan-display');
    if (!container) return;
    
    container.innerHTML = `
        <div class="chapter-header">
            <h3>${escapeHtml(plan.title || `第${plan.chapter_number}章`)}</h3>
            <p class="goal">本章目标: ${escapeHtml(plan.chapter_goal || '')}</p>
            <p class="emotion">情绪走向: ${escapeHtml(plan.emotional_arc || '')}</p>
        </div>
        <div class="scenes-list">
            ${(plan.scenes || []).map((scene, index) => `
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
                    <div class="scene-actions">
                        <button onclick="generateScene(${escapeHtml(String(scene.scene_index))})"
                                ${AppState.isGenerating ? 'disabled' : ''}>
                            ${AppState.isGenerating ? '生成中...' : '生成'}
                        </button>
                        <button onclick="editScene(${escapeHtml(String(scene.scene_index))})">编辑</button>
                    </div>
                    <div class="scene-content" id="scene-content-${escapeHtml(String(scene.scene_index))}"></div>
                </div>
            `).join('')}
        </div>
    `;
}

/**
 * 生成场景
 */
async function generateScene(sceneIndex) {
    if (!AppState.currentProject || AppState.isGenerating) return;
    
    AppState.isGenerating = true;
    AppState.currentScene = sceneIndex;
    
    const contentDiv = document.getElementById(`scene-content-${sceneIndex}`);
    contentDiv.innerHTML = '<div class="generating">正在生成...</div>';
    
    try {
        // 创建写作任务
        await apiRequest(
            `/api/projects/${AppState.currentProject}/chapters/${AppState.currentChapter}/scenes/${sceneIndex}/write`,
            { method: 'POST' }
        );
        
        // 连接 SSE 流
        await connectStream(sceneIndex, contentDiv);
        
    } catch (error) {
        contentDiv.innerHTML = `<div class="error">生成失败: ${escapeHtml(error.message)}</div>`;
        AppState.isGenerating = false;
    }
}

/**
 * 连接 SSE 流
 */
async function connectStream(sceneIndex, contentDiv) {
    const projectId = AppState.currentProject;
    const chapterNum = AppState.currentChapter;
    
    const temperature = getSetting('temperature', 0.75);
    const eventSource = new EventSource(
        `/api/projects/${projectId}/stream/${chapterNum}/${sceneIndex}?temperature=${encodeURIComponent(temperature)}`
    );
    
    AppState.eventSource = eventSource;
    
    let generatedText = '';
    
    eventSource.onmessage = (event) => {
        const data = JSON.parse(event.data);
        
        switch (data.type) {
            case 'token':
                // 追加 token
                generatedText += data.data;
                contentDiv.innerHTML = `<div class="generated-text">${escapeHtml(generatedText)}</div>`;
                // 自动滚动到底部
                contentDiv.scrollTop = contentDiv.scrollHeight;
                break;
                
            case 'scene_complete':
                showMessage(`场景生成完成！字数: ${data.data.word_count}`);
                break;
                
            case 'error':
                contentDiv.innerHTML += `<div class="error">错误: ${data.data.message}</div>`;
                break;
                
            case 'done':
                eventSource.close();
                AppState.isGenerating = false;
                AppState.eventSource = null;
                break;
        }
    };
    
    eventSource.onerror = (error) => {
        console.error('SSE 错误:', error);
        eventSource.close();
        AppState.isGenerating = false;
        AppState.eventSource = null;
        contentDiv.innerHTML += '<div class="error">连接中断</div>';
    };
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
    const contentDiv = document.getElementById(`scene-content-${sceneIndex}`);
    const currentText = contentDiv.textContent || '';

    // 保存原始内容，用于取消时恢复
    contentDiv.dataset.originalContent = currentText;

    contentDiv.innerHTML = `
        <textarea id="edit-scene-${sceneIndex}" rows="10">${escapeHtml(currentText)}</textarea>
        <div class="edit-actions">
            <button onclick="saveSceneEdit(${sceneIndex})">保存</button>
            <button onclick="cancelSceneEdit(${sceneIndex})">取消</button>
        </div>
    `;
}

/**
 * 保存场景编辑
 */
async function saveSceneEdit(sceneIndex) {
    const textarea = document.getElementById(`edit-scene-${sceneIndex}`);
    const newText = textarea.value;
    
    try {
        await apiRequest(
            `/api/projects/${AppState.currentProject}/chapters/${AppState.currentChapter}/scenes/${sceneIndex}`,
            {
                method: 'PUT',
                body: JSON.stringify({ text: newText })
            }
        );
        
        showMessage('场景已保存');
        const contentDiv = document.getElementById(`scene-content-${sceneIndex}`);
        contentDiv.innerHTML = `<div class="generated-text">${escapeHtml(newText)}</div>`;
        
    } catch (error) {
        showMessage('保存失败: ' + error.message, 'error');
    }
}

/**
 * 取消场景编辑
 */
function cancelSceneEdit(sceneIndex) {
    const contentDiv = document.getElementById(`scene-content-${sceneIndex}`);
    // 恢复原始内容
    const originalContent = contentDiv.dataset.originalContent || '';
    contentDiv.innerHTML = `<div class="generated-text">${escapeHtml(originalContent)}</div>`;
    // 清除保存的原始内容
    delete contentDiv.dataset.originalContent;
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
        
        // 进入下一章
        AppState.currentChapter++;
        await loadChapterPlan(AppState.currentProject, AppState.currentChapter);
        
    } catch (error) {
        showMessage('确认章节失败: ' + error.message, 'error');
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
        // 并行加载所有知识库
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
                    ${data.outline ? `
                        <pre>${escapeHtml(JSON.stringify(data.outline, null, 2))}</pre>
                    ` : '<p>未生成</p>'}
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
    // 隐藏所有面板
    document.querySelectorAll('.content-panel').forEach(panel => {
        panel.classList.remove('active');
    });
    
    // 显示指定面板
    const panel = document.getElementById(panelId);
    if (panel) {
        panel.classList.add('active');
    }
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
    // 加载项目列表
    loadProjects();
    
    // 加载设置
    loadSettings();
    
    // 温度滑块实时更新
    const tempSlider = document.getElementById('setting-temperature');
    const tempValue = document.getElementById('temperature-value');
    if (tempSlider && tempValue) {
        tempSlider.addEventListener('input', () => {
            tempValue.textContent = tempSlider.value;
        });
    }
    
    // 绑定顶部菜单按钮（侧边栏收起/展开）
    const topBarIcon = document.querySelector('.top-bar-icon');
    if (topBarIcon) {
        topBarIcon.addEventListener('click', (e) => {
            e.preventDefault();
            toggleSidebar();
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
    
    // 绑定初始化按钮
    const bibleBtn = document.getElementById('generate-bible-btn');
    if (bibleBtn) {
        bibleBtn.addEventListener('click', (e) => {
            e.preventDefault();
            generateBible();
        });
    }
    
    const charBtn = document.getElementById('generate-characters-btn');
    if (charBtn) charBtn.addEventListener('click', (e) => { e.preventDefault(); generateCharacters(); });
    
    const outlineBtn = document.getElementById('generate-outline-btn');
    if (outlineBtn) outlineBtn.addEventListener('click', (e) => { e.preventDefault(); generateOutline(); });
    
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
            // 移除所有active类
            document.querySelectorAll('.sidebar-item').forEach(i => i.classList.remove('active'));
            // 添加active到当前项
            item.classList.add('active');
            
            const panel = item.dataset.panel;
            if (panel) {
                showPanel(panel + '-panel');
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

// ============================================
// 弧线/章节规划 UI
// ============================================

/**
 * 生成弧线规划
 */
async function generateArc(arcNumber) {
    if (!AppState.currentProject) return;
    
    const btn = document.getElementById(`generate-arc-${arcNumber}-btn`);
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="loading-spinner small"></span> 生成中...';
    }
    
    try {
        const temperature = getSetting('temperature', 0.7);
        const result = await apiRequest(
            `/api/projects/${AppState.currentProject}/generate/arc?arc_number=${arcNumber}&temperature=${encodeURIComponent(temperature)}`,
            { method: 'POST' }
        );
        showMessage(`弧线 ${arcNumber} 规划生成成功！`, 'success');
        return result;
    } catch (error) {
        showMessage('生成弧线规划失败: ' + error.message, 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = `生成弧线 ${arcNumber}`;
        }
    }
}

/**
 * 生成章节规划
 */
async function generateChapterPlan(chapterNumber) {
    if (!AppState.currentProject) return;

    const btn = document.getElementById(`generate-chapter-${chapterNumber}-btn`);
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
        // 刷新章节规划显示
        await loadChapterPlan(AppState.currentProject, chapterNumber);
        return result;
    } catch (error) {
        showMessage('生成章节规划失败: ' + error.message, 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = `生成第${chapterNumber}章规划`;
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
// 实时监控
// ============================================

/**
 * 刷新监控面板
 */
async function refreshMonitor() {
    if (!AppState.currentProject) {
        showMessage('请先选择项目', 'warning');
        return;
    }
    
    try {
        // 加载 Issues
        const issuesData = await apiRequest(`/api/projects/${AppState.currentProject}/issues`);
        displayIssues(issuesData.issues || []);
        
        // 加载伏笔
        const fsData = await apiRequest(`/api/projects/${AppState.currentProject}/foreshadowing`);
        displayForeshadowing(fsData.foreshadowing || []);
        
        // 加载项目状态
        const status = await apiRequest(`/api/projects/${AppState.currentProject}/status`);
        const statChapters = document.getElementById('stat-chapters');
        const statWords = document.getElementById('stat-words');
        const statIssues = document.getElementById('stat-issues');
        
        if (statChapters) statChapters.textContent = status.current_chapter || 0;
        if (statWords) statWords.textContent = '-';
        if (statIssues) statIssues.textContent = (issuesData.issues || []).length;
        
        showMessage('监控数据已刷新', 'success');
    } catch (error) {
        showMessage('刷新监控失败: ' + error.message, 'error');
    }
}

/**
 * 显示 Issues 列表
 */
function displayIssues(issues) {
    const container = document.getElementById('issues-list');
    if (!container) return;
    
    if (issues.length === 0) {
        container.innerHTML = '<p style="color: var(--text-secondary); text-align: center; padding: 12px;">暂无待处理 Issues</p>';
        return;
    }
    
    const urgencyColors = {
        'critical': 'var(--error)',
        'major': 'var(--warning)',
        'medium': 'var(--info)',
        'minor': 'var(--text-secondary)'
    };

    container.innerHTML = issues.map(issue => `
        <div style="display: flex; align-items: center; gap: 12px; padding: 10px; margin-bottom: 8px;
                    background: var(--bg-dark); border-radius: 6px; border-left: 3px solid ${urgencyColors[issue.urgency] || 'var(--text-secondary)'};">
            <span style="font-size: 11px; color: var(--text-secondary); min-width: 80px;">${escapeHtml(issue.type || '')}</span>
            <span style="flex: 1; font-size: 13px;">${escapeHtml(issue.description || '')}</span>
            <span style="font-size: 11px; padding: 2px 8px; border-radius: 4px;
                         background: ${issue.status === 'resolved' ? 'rgba(34,197,94,0.15)' : 'rgba(234,179,8,0.15)'};
                         color: ${issue.status === 'resolved' ? 'var(--success)' : 'var(--warning)'};">${escapeHtml(issue.status || '')}</span>
        </div>
    `).join('');
}

/**
 * 显示伏笔状态
 */
function displayForeshadowing(foreshadowing) {
    const container = document.getElementById('foreshadowing-monitor');
    if (!container) return;
    
    if (foreshadowing.length === 0) {
        container.innerHTML = '<p style="color: var(--text-secondary); text-align: center; padding: 12px;">暂无伏笔</p>';
        return;
    }
    
    const statusColors = {
        'planted': 'var(--info)',
        'hinted': 'var(--warning)',
        'triggered': 'var(--primary-light)',
        'resolved': 'var(--success)'
    };
    
    container.innerHTML = foreshadowing.map(fs => `
        <div style="display: flex; align-items: center; gap: 12px; padding: 10px; margin-bottom: 8px;
                    background: var(--bg-dark); border-radius: 6px;">
            <span style="font-size: 11px; padding: 2px 8px; border-radius: 4px; min-width: 60px; text-align: center;
                         background: rgba(99,102,241,0.15); color: ${statusColors[fs.status] || 'var(--text-secondary)'};">${escapeHtml(fs.status || 'active')}</span>
            <span style="flex: 1; font-size: 13px;">${escapeHtml((fs.description || '').substring(0, 80))}${(fs.description || '').length > 80 ? '...' : ''}</span>
            <span style="font-size: 11px; color: var(--text-secondary);">${escapeHtml(fs.type || '')}</span>
        </div>
    `).join('');
}

window.generateArc = generateArc;
window.generateChapterPlan = generateChapterPlan;
window.saveSettings = saveSettings;
window.resetSettings = resetSettings;
window.refreshMonitor = refreshMonitor;

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
                             color: ${issue.status === 'resolved' ? 'var(--success)' : 'var(--warning)'};">${escapeHtml(issue.status || '')}</span>
            </div>
        `).join('');
    } catch (error) {
        if (container) {
            container.innerHTML = '<p class="error" style="text-align:center;">加载 Issues 失败</p>';
        }
    }
}

window.loadIssuesForPanel = loadIssuesForPanel;
