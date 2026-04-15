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

// API 基础URL
const API_BASE = '';

// ============================================
// 工具函数
// ============================================

/**
 * 发送API请求
 */
async function apiRequest(url, options = {}) {
    const response = await fetch(url, {
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
}

/**
 * 显示消息
 */
function showMessage(message, type = 'info') {
    console.log(`[${type}] ${message}`);
    // TODO: 可以改为toast通知
    // alert(message);
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
    try {
        const data = await apiRequest('/api/projects');
        const projects = data.projects || [];
        
        const select = document.getElementById('works-select');
        if (select) {
            select.innerHTML = '<option value="">选择作品...</option>';
            projects.forEach(project => {
                const option = document.createElement('option');
                option.value = project.id;
                option.textContent = project.name;
                select.appendChild(option);
            });
        }
        
        // 更新项目列表显示
        updateProjectList(projects);
        
    } catch (error) {
        console.error('加载项目列表失败:', error);
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
        <div class="project-card" data-id="${project.id}">
            <h3>${project.name}</h3>
            <p class="genre">${project.genre}</p>
            <p class="status">状态: ${getStatusText(project.status)}</p>
            <p class="chapter">当前章节: ${project.current_chapter}</p>
            <div class="actions">
                <button onclick="openProject('${project.id}')">打开</button>
                <button onclick="deleteProject('${project.id}')" class="danger">删除</button>
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
        const result = await apiRequest('/api/projects', {
            method: 'POST',
            body: JSON.stringify(projectData)
        });
        
        showMessage('项目创建成功！');
        await loadProjects();
        return result.project_id;
        
    } catch (error) {
        showMessage('创建项目失败: ' + error.message, 'error');
        throw error;
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
    try {
        const status = await apiRequest(`/api/projects/${projectId}/status`);
        
        // 更新UI状态
        updateInitStepStatus('bible-step', status.has_bible);
        updateInitStepStatus('character-step', status.has_characters);
        updateInitStepStatus('outline-step', status.has_outline);
        
        // 显示/隐藏生成按钮
        const bibleBtn = document.getElementById('generate-bible-btn');
        const charBtn = document.getElementById('generate-characters-btn');
        const outlineBtn = document.getElementById('generate-outline-btn');
        
        if (bibleBtn) bibleBtn.disabled = status.has_bible;
        if (charBtn) charBtn.disabled = !status.has_bible || status.has_characters;
        if (outlineBtn) outlineBtn.disabled = !status.has_characters || status.has_outline;
        
        // 如果全部完成，显示进入写作按钮
        if (status.initialized) {
            const enterWritingBtn = document.getElementById('enter-writing-btn');
            if (enterWritingBtn) enterWritingBtn.style.display = 'block';
        }
        
    } catch (error) {
        console.error('检查初始化状态失败:', error);
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
 * 生成 Bible
 */
async function generateBible() {
    if (!AppState.currentProject) return;
    
    const btn = document.getElementById('generate-bible-btn');
    btn.disabled = true;
    btn.textContent = '生成中...';
    
    try {
        const result = await apiRequest(
            `/api/projects/${AppState.currentProject}/generate/bible`,
            { method: 'POST' }
        );
        
        showMessage('Bible 生成成功！');
        displayBible(result.data);
        await checkInitializationStatus(AppState.currentProject);
        
    } catch (error) {
        showMessage('生成 Bible 失败: ' + error.message, 'error');
        btn.disabled = false;
    }
    
    btn.textContent = '生成 Bible';
}

/**
 * 显示 Bible
 */
function displayBible(bibleData) {
    const container = document.getElementById('bible-display');
    if (!container) return;
    
    container.innerHTML = `
        <h3>${bibleData.world_name || '世界观设定'}</h3>
        <p>${bibleData.world_description || ''}</p>
        <div class="bible-sections">
            <details>
                <summary>战力体系</summary>
                <pre>${JSON.stringify(bibleData.combat_system, null, 2)}</pre>
            </details>
            <details>
                <summary>世界规则 (${bibleData.world_rules?.length || 0}条)</summary>
                <ul>
                    ${(bibleData.world_rules || []).map(r => `<li>${r.content}</li>`).join('')}
                </ul>
            </details>
            <details>
                <summary>势力分布</summary>
                <ul>
                    ${(bibleData.factions || []).map(f => `<li>${f.name}: ${f.description}</li>`).join('')}
                </ul>
            </details>
        </div>
    `;
}

/**
 * 生成人物
 */
async function generateCharacters() {
    if (!AppState.currentProject) return;
    
    const btn = document.getElementById('generate-characters-btn');
    btn.disabled = true;
    btn.textContent = '生成中...';
    
    try {
        const result = await apiRequest(
            `/api/projects/${AppState.currentProject}/generate/characters`,
            { method: 'POST' }
        );
        
        showMessage('人物生成成功！');
        await checkInitializationStatus(AppState.currentProject);
        
    } catch (error) {
        showMessage('生成人物失败: ' + error.message, 'error');
        btn.disabled = false;
    }
    
    btn.textContent = '生成人物';
}

/**
 * 生成大纲
 */
async function generateOutline() {
    if (!AppState.currentProject) return;
    
    const btn = document.getElementById('generate-outline-btn');
    btn.disabled = true;
    btn.textContent = '生成中...';
    
    try {
        const result = await apiRequest(
            `/api/projects/${AppState.currentProject}/generate/outline`,
            { method: 'POST' }
        );
        
        showMessage('大纲生成成功！');
        await checkInitializationStatus(AppState.currentProject);
        
    } catch (error) {
        showMessage('生成大纲失败: ' + error.message, 'error');
        btn.disabled = false;
    }
    
    btn.textContent = '生成大纲';
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
            <p class="empty">第${chapterNum}章规划不存在，请先生成弧线规划</p>
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
            <h3>${plan.title || `第${plan.chapter_number}章`}</h3>
            <p class="goal">本章目标: ${plan.chapter_goal || ''}</p>
            <p class="emotion">情绪走向: ${plan.emotional_arc || ''}</p>
        </div>
        <div class="scenes-list">
            ${(plan.scenes || []).map((scene, index) => `
                <div class="scene-card" data-index="${scene.scene_index}">
                    <div class="scene-header">
                        <span class="scene-number">场景 ${scene.scene_index + 1}</span>
                        <span class="scene-tone">${scene.emotional_tone || ''}</span>
                    </div>
                    <p class="scene-intent">${scene.intent || ''}</p>
                    <div class="scene-meta">
                        <span>视角: ${scene.pov_character || ''}</span>
                        <span>出场: ${(scene.present_characters || []).join(', ')}</span>
                        <span>字数: ~${scene.target_word_count || 1200}</span>
                    </div>
                    <div class="scene-actions">
                        <button onclick="generateScene(${scene.scene_index})" 
                                ${AppState.isGenerating ? 'disabled' : ''}>
                            ${AppState.isGenerating ? '生成中...' : '生成'}
                        </button>
                        <button onclick="editScene(${scene.scene_index})">编辑</button>
                    </div>
                    <div class="scene-content" id="scene-content-${scene.scene_index}"></div>
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
        contentDiv.innerHTML = `<div class="error">生成失败: ${error.message}</div>`;
        AppState.isGenerating = false;
    }
}

/**
 * 连接 SSE 流
 */
async function connectStream(sceneIndex, contentDiv) {
    const projectId = AppState.currentProject;
    const chapterNum = AppState.currentChapter;
    
    const eventSource = new EventSource(
        `/api/projects/${projectId}/stream/${chapterNum}/${sceneIndex}`
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
    
    contentDiv.innerHTML = `
        <textarea id="edit-scene-${sceneIndex}" rows="10">${currentText}</textarea>
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
    // 重新加载场景内容
    generateScene(sceneIndex); // 简化处理，实际应该重新加载已保存的内容
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
                        <h4>${data.bible.world_name || ''}</h4>
                        <p>${data.bible.world_description || ''}</p>
                    ` : '<p>未生成</p>'}
                </div>
            </details>
            
            <details ${data.characters ? 'open' : ''}>
                <summary>人物设定 ${data.characters ? `(${data.characters.characters?.length || 0})` : '✗'}</summary>
                <div class="kb-content">
                    ${data.characters ? `
                        <ul>
                            ${(data.characters.characters || []).map(c => `
                                <li>${c.name} - ${c.personality_core || ''}</li>
                            `).join('')}
                        </ul>
                    ` : '<p>未生成</p>'}
                </div>
            </details>
            
            <details ${data.outline ? 'open' : ''}>
                <summary>大纲 ${data.outline ? '✓' : '✗'}</summary>
                <div class="kb-content">
                    ${data.outline ? `
                        <pre>${JSON.stringify(data.outline, null, 2)}</pre>
                    ` : '<p>未生成</p>'}
                </div>
            </details>
            
            <details ${data.foreshadowing ? 'open' : ''}>
                <summary>伏笔 ${data.foreshadowing ? `(${data.foreshadowing.foreshadowing?.length || 0})` : '✗'}</summary>
                <div class="kb-content">
                    ${data.foreshadowing ? `
                        <ul>
                            ${(data.foreshadowing.foreshadowing || []).map(f => `
                                <li>[${f.status}] ${f.description?.substring(0, 50)}...</li>
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
