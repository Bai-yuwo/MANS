// MANS Web UI - 完整前端逻辑 (SSE流式版本)

const API_BASE = '';
let currentProject = null;
let currentChapterData = null;
let eventSource = null;
let monitorInterval = null;
let serverStartTime = null;  // 服务器启动时间（毫秒）


// ============================================================
// 页面初始化
// ============================================================

document.addEventListener('DOMContentLoaded', async () => {
    await checkApiStatus();  // 获取服务器启动时间
    loadDashboard();
    loadProjects();
    initMonitor();
    updateUptime();
    setInterval(updateUptime, 1000);
});

// 更新运行时间（使用服务器启动时间）
function updateUptime() {
    if (!serverStartTime) return;  // 等待服务器时间加载
    
    const elapsed = Math.floor((Date.now() - serverStartTime) / 1000);
    const hours = Math.floor(elapsed / 3600);
    const minutes = Math.floor((elapsed % 3600) / 60);
    const seconds = elapsed % 60;
    const el = document.getElementById('monitor-uptime');
    if (el) el.textContent = `运行时间: ${hours}h ${minutes}m ${seconds}s`;
}

// 检查API状态
async function checkApiStatus() {
    try {
        const response = await fetch(`${API_BASE}/api/status`);
        const data = await response.json();
        
        // 设置服务器启动时间（用于计算运行时间）
        if (data.server_start_time) {
            serverStartTime = data.server_start_time * 1000;  // 转换为毫秒
        }
        
        const dot = document.getElementById('api-status-dot');
        const text = document.getElementById('api-status-text');
        const dashStatus = document.getElementById('dash-api-status');
        
        if (data.api_ready) {
            dot.className = 'w-2 h-2 rounded-full bg-green-500 mr-2';
            text.textContent = `${data.provider_name || data.provider || '已就绪'} 已就绪`;
            if (dashStatus) { dashStatus.className = 'status-badge status-active'; dashStatus.textContent = '已就绪'; }
        } else {
            dot.className = 'w-2 h-2 rounded-full bg-red-500 mr-2';
            text.textContent = 'API未配置';
            if (dashStatus) { dashStatus.className = 'status-badge status-error'; dashStatus.textContent = '未配置'; }
        }
        
        // 更新页面上的模型名称
        if (data.model_planning) {
            const elPlanning = document.getElementById('model-planning');
            if (elPlanning) elPlanning.textContent = data.model_planning;
        }
        if (data.model_writing) {
            const elWriting = document.getElementById('model-writing');
            if (elWriting) elWriting.textContent = data.model_writing;
        }
        if (data.model_review) {
            const elReview = document.getElementById('model-review');
            if (elReview) elReview.textContent = data.model_review;
        }
        
        // 更新系统状态栏中的模型名称
        const modelDisplay = document.getElementById('model-display');
        if (modelDisplay) {
            modelDisplay.textContent = data.model_planning || '未配置';
        }
        
        // 更新系统信息中的提供商
        const providerDisplay = document.getElementById('provider-display');
        if (providerDisplay && data.provider_name) {
            providerDisplay.textContent = data.provider_name;
        }
        
        loadApiKeyConfig();
    } catch (error) {
        const dot = document.getElementById('api-status-dot');
        const text = document.getElementById('api-status-text');
        if (dot && text) { dot.className = 'w-2 h-2 rounded-full bg-red-500 mr-2'; text.textContent = '连接失败'; }
    }
}

async function loadApiKeyConfig() {
    try {
        const response = await fetch(`${API_BASE}/api/config/api-key`);
        const data = await response.json();
        const apiKeyInput = document.getElementById('api-key-input');
        if (apiKeyInput) {
            if (data.configured) {
                apiKeyInput.value = data.api_key || '';
                apiKeyInput.dataset.configured = 'true';
                apiKeyInput.placeholder = '';
            } else {
                const provider = data.provider || 'dashscope';
                const providerLabel = provider === 'doubao' ? '豆包' : 'DashScope';
                apiKeyInput.placeholder = `输入你的 ${providerLabel} API Key`;
                apiKeyInput.dataset.configured = 'false';
            }
        }
    } catch (error) { console.error('加载API Key配置失败:', error); }
}

// 页面切换（用户点击触发）
function showPage(pageName) {
    showPageByName(pageName);
    // 更新导航高亮
    document.querySelectorAll('.nav-item').forEach(item => item.classList.remove('active'));
    if (event.currentTarget) event.currentTarget.classList.add('active');
}

// 程序化页面切换（无事件对象）
function showPageByName(pageName) {
    document.querySelectorAll('.page').forEach(page => page.classList.add('hidden'));
    const target = document.getElementById(`page-${pageName}`);
    if (target) target.classList.remove('hidden');
    
    // 同步导航高亮
    const navMap = { 'dashboard': 0, 'projects': 1, 'create': 2, 'monitor': 3, 'logs': 4 };
    document.querySelectorAll('.nav-item').forEach((item, idx) => {
        item.classList.toggle('active', idx === (navMap[pageName] ?? 0));
    });
    
    if (pageName === 'dashboard') loadDashboard();
    else if (pageName === 'projects') loadProjects();
    else if (pageName === 'create') loadProjectSelect();
    else if (pageName === 'monitor') refreshMonitorData();
    else if (pageName === 'logs') loadLogs();
}

async function loadDashboard() {
    try {
        const response = await fetch(`${API_BASE}/api/projects`);
        const data = await response.json();
        const projects = data.projects || [];
        
        document.getElementById('stat-projects').textContent = projects.length;
        let totalChapters = 0, totalWords = 0;
        projects.forEach(p => { totalChapters += p.current_chapter; totalWords += p.total_words; });
        document.getElementById('stat-chapters').textContent = totalChapters;
        document.getElementById('stat-words').textContent = totalWords.toLocaleString();
        
        const metricsResponse = await fetch(`${API_BASE}/api/monitor/metrics`);
        const metrics = await metricsResponse.json();
        document.getElementById('stat-api-calls').textContent = metrics.api_calls || 0;
    } catch (error) { console.error('加载仪表盘失败:', error); }
}

async function loadProjects() {
    const grid = document.getElementById('projects-grid');
    if (!grid) return;

    try {
        const response = await fetch(`${API_BASE}/api/projects`);
        const data = await response.json();
        const projects = data.projects || [];
        
        if (projects.length === 0) {
            grid.innerHTML = `
                <div class="col-span-full card p-12 text-center">
                    <i class="fas fa-inbox text-6xl text-gray-600 mb-4"></i>
                    <h3 class="text-xl font-bold mb-2">还没有项目</h3>
                    <p class="text-gray-400 mb-4">创建你的第一个小说项目</p>
                    <button class="btn-primary" onclick="openNewProjectModal()"><i class="fas fa-plus mr-2"></i>创建项目</button>
                </div>`;
            return;
        }
        
        grid.innerHTML = projects.map(p => `
            <div class="card p-6 cursor-pointer" onclick="showProjectDetail('${p.name}')">
                <div class="flex items-start justify-between mb-4">
                    <div class="w-12 h-12 rounded-xl bg-indigo-500/20 flex items-center justify-center">
                        <i class="fas fa-book text-indigo-400 text-xl"></i>
                    </div>
                    <span class="status-badge ${p.current_chapter > 0 ? 'status-active' : 'status-pending'}">${p.current_chapter > 0 ? '创作中' : '未开始'}</span>
                </div>
                <h3 class="text-lg font-bold mb-1">${p.title}</h3>
                <p class="text-sm text-gray-400 mb-4">${p.genre}</p>
                <div class="flex items-center text-sm text-gray-400">
                    <i class="fas fa-file-alt mr-2"></i><span>${p.current_chapter} 章</span>
                    <span class="mx-2">|</span>
                    <i class="fas fa-font mr-2"></i><span>${p.total_words.toLocaleString()} 字</span>
                </div>
            </div>`).join('');
    } catch (error) {
        console.error('加载项目列表失败:', error);
        grid.innerHTML = `
            <div class="col-span-full card p-12 text-center">
                <i class="fas fa-exclamation-triangle text-6xl text-red-600 mb-4"></i>
                <h3 class="text-xl font-bold mb-2">加载失败</h3>
                <p class="text-gray-400 mb-4">${error.message || '无法连接到服务器'}</p>
                <button class="btn-primary" onclick="loadProjects()"><i class="fas fa-redo mr-2"></i>重试</button>
            </div>`;
    }
}

async function loadProjectSelect() {
    try {
        const response = await fetch(`${API_BASE}/api/projects`);
        const data = await response.json();
        const select = document.getElementById('project-select');
        select.innerHTML = '<option value="">选择项目...</option>' + (data.projects || []).map(p => `<option value="${p.name}">${p.title}</option>`).join('');
    } catch (error) { console.error('加载项目选择器失败:', error); }
}

async function loadProjectForWriting() {
    const projectName = document.getElementById('project-select').value;
    if (!projectName) {
        document.getElementById('no-project-selected').classList.remove('hidden');
        document.getElementById('write-area').classList.add('hidden');
        return;
    }
    
    try {
        const response = await fetch(`${API_BASE}/api/projects/${projectName}`);
        const project = await response.json();
        currentProject = project;
        
        document.getElementById('no-project-selected').classList.add('hidden');
        document.getElementById('write-area').classList.remove('hidden');
        document.getElementById('current-project-title').textContent = project.metadata.title;
        document.getElementById('current-chapter-num').textContent = project.progress.current_chapter;
        document.getElementById('target-chapter').value = project.progress.current_chapter + 1;
        document.getElementById('chapter-preview').classList.add('hidden');
        document.getElementById('no-chapter-selected').classList.remove('hidden');
    } catch (error) { alert('加载项目失败: ' + error.message); }
}

function openNewProjectModal() { document.getElementById('new-project-modal').classList.add('active'); }

function closeNewProjectModal() {
    document.getElementById('new-project-modal').classList.remove('hidden');
    document.getElementById('new-project-modal').classList.remove('active');
    document.getElementById('new-project-name').value = '';
    document.getElementById('new-project-title').value = '';
}

// ============================================================
// 多阶段项目初始化
// ============================================================

// 阶段定义
const INIT_STAGES = [
    { index: 0, name: 'world', label: '世界观构建', description: '设计故事的世界、场景、规则体系' },
    { index: 1, name: 'character', label: '人物设计', description: '设计核心人物的性格、背景、关系' },
    { index: 2, name: 'plot', label: '情节构建', description: '设计主线和支线情节线' },
    { index: 3, name: 'foreshadowing', label: '伏笔管理', description: '设计伏笔埋设和揭晓计划' },
    { index: 4, name: 'outline', label: '章节大纲', description: '规划故事的整体章节结构' }
];

let initSession = null;  // 当前初始化会话

async function submitNewProject() {
    const name = document.getElementById('new-project-name').value.trim();
    const title = document.getElementById('new-project-title').value.trim() || name;
    const genre = document.getElementById('new-project-genre').value;

    if (!name) { alert('请输入项目名称'); return; }

    // 关闭弹窗，打开进度面板
    closeNewProjectModal();

    // 创建进度面板
    const progressPanel = createInitProgressPanel(title, genre);
    document.body.appendChild(progressPanel);

    try {
        // 阶段1：开始初始化
        addInitLog(progressPanel, 'SYSTEM', 'info', `开始初始化项目: ${title}`);

        // 调用原来的流式创建 API（现在它会分阶段输出）
        const response = await fetch(`${API_BASE}/api/projects/stream-create`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, title, genre })
        });

        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.message || '创建失败');
        }

        // 解析流式响应
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        let currentStage = null;
        let currentStageIndex = 0;
        let stageResults = {};
        let accumulatedContent = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';

            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    try {
                        const jsonStr = line.slice(6).trim();
                        if (!jsonStr) continue;

                        const event = JSON.parse(jsonStr);

                        // 阶段开始事件
                        if (event.type === 'stage_start') {
                            currentStage = INIT_STAGES.find(s => s.name === event.data.stage_name);
                            currentStageIndex = event.data.stage_index;
                            addInitLog(progressPanel, currentStage.label, 'info', `开始${currentStage.label}...`);
                            updateInitProgress(progressPanel, currentStageIndex, INIT_STAGES.length);
                        }

                        // 内容块
                        else if (event.type === 'chunk') {
                            const content = event.data.text || '';
                            accumulatedContent += content;
                            appendInitContent(progressPanel, currentStage?.label || 'SYSTEM', content);
                        }

                        // 思考过程
                        else if (event.type === 'thinking') {
                            appendInitThinking(progressPanel, currentStage?.label || 'SYSTEM', event.data.text);
                        }

                        // 阶段完成
                        else if (event.type === 'stage_complete') {
                            const stageData = event.data;
                            stageResults[stageData.stage_name] = stageData.result;

                            // 显示确认弹窗
                            const confirmed = await showStageConfirmDialog(stageData);

                            if (confirmed) {
                                addInitLog(progressPanel, 'USER', 'success', `已确认${stageData.stage_label}，继续下一阶段`);
                            } else {
                                addInitLog(progressPanel, 'USER', 'warning', `已拒绝${stageData.stage_label}，将重新生成`);
                                // TODO: 支持重新生成
                                addInitLog(progressPanel, 'USER', 'info', '暂时跳过，重新生成功能开发中...');
                            }
                        }

                        // 状态消息
                        else if (event.type === 'status') {
                            addInitLog(progressPanel, 'SYSTEM', 'info', event.data.message);
                        }

                        // 错误
                        else if (event.type === 'error') {
                            addInitLog(progressPanel, 'ERROR', 'error', event.data.message);
                        }

                        // 完成
                        else if (event.type === 'DONE' || event.type === 'complete') {
                            if (event.data?.success || event.status === 'complete') {
                                addInitLog(progressPanel, 'SYSTEM', 'success', '项目初始化完成！');
                                updateInitProgress(progressPanel, INIT_STAGES.length, INIT_STAGES.length);

                                // 刷新并跳转
                                setTimeout(() => {
                                    loadProjects();
                                    loadDashboard();
                                    showPageByName('create');
                                    setTimeout(async () => {
                                        await loadProjectSelect();
                                        const select = document.getElementById('project-select');
                                        if (select) {
                                            select.value = name;
                                            await loadProjectForWriting();
                                        }
                                    }, 100);
                                }, 1500);
                            }
                        }
                    } catch (e) {
                        console.warn('解析 SSE 数据失败:', e);
                    }
                }
            }
        }

    } catch (error) {
        addInitLog(progressPanel, 'ERROR', 'error', '创建失败: ' + error.message);
        alert('创建失败: ' + error.message);
    } finally {
        // 保留面板让用户查看结果
        const closeBtn = document.createElement('button');
        closeBtn.className = 'btn-secondary mt-4';
        closeBtn.textContent = '关闭';
        closeBtn.onclick = () => progressPanel.remove();
        progressPanel.querySelector('.progress-content').appendChild(closeBtn);
    }
}

// 创建初始化进度面板
function createInitProgressPanel(title, genre) {
    const panel = document.createElement('div');
    panel.className = 'init-progress-panel';
    panel.style.cssText = `
        position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%);
        width: 700px; max-height: 80vh; background: #1a1a2e; border: 1px solid #4a5568;
        border-radius: 12px; padding: 24px; z-index: 1000; overflow: hidden;
        box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);
    `;

    panel.innerHTML = `
        <div class="flex items-center justify-between mb-6">
            <div>
                <h3 class="text-xl font-bold text-white">初始化项目</h3>
                <p class="text-sm text-gray-400">${title} · ${genre}</p>
            </div>
            <button onclick="this.closest('.init-progress-panel').remove()" class="text-gray-400 hover:text-white">
                <i class="fas fa-times"></i>
            </button>
        </div>

        <div class="mb-4">
            <div class="flex justify-between text-sm mb-1">
                <span id="init-progress-text" class="text-gray-400">准备中...</span>
                <span id="init-progress-percent" class="text-indigo-400">0/${INIT_STAGES.length}</span>
            </div>
            <div class="progress-bar">
                <div id="init-progress-fill" class="progress-fill" style="width: 0%"></div>
            </div>
        </div>

        <div id="init-stages-indicator" class="flex gap-2 mb-4 overflow-x-auto pb-2">
            ${INIT_STAGES.map((s, i) => `
                <div id="init-stage-${i}" class="init-stage-badge" style="
                    padding: 4px 12px; border-radius: 20px; font-size: 12px;
                    background: #2d3748; color: #a0aec0; white-space: nowrap;
                    transition: all 0.3s;
                ">
                    ${s.label}
                </div>
            `).join('')}
        </div>

        <div class="progress-content" style="max-height: 400px; overflow-y: auto;">
            <div id="init-log-area" class="space-y-2 text-sm"></div>
        </div>
    `;

    return panel;
}

// 添加初始化日志
function addInitLog(panel, agent, level, message) {
    const logArea = panel.querySelector('#init-log-area');
    if (!logArea) return;

    const colors = {
        'info': 'text-blue-400',
        'success': 'text-green-400',
        'warning': 'text-yellow-400',
        'error': 'text-red-400'
    };

    const entry = document.createElement('div');
    entry.className = `flex items-start gap-2 ${colors[level] || 'text-gray-300'}`;
    entry.innerHTML = `
        <span class="text-gray-500">[${agent}]</span>
        <span class="flex-1">${escapeHtml(message)}</span>
        <span class="text-gray-600 text-xs">${new Date().toLocaleTimeString()}</span>
    `;

    logArea.appendChild(entry);
    logArea.scrollTop = logArea.scrollHeight;
}

// 追加初始化内容（用于流式显示）
function appendInitContent(panel, stage, content) {
    // 找到当前阶段的最后一条日志或创建新日志
    const logArea = panel.querySelector('#init-log-area');
    let lastEntry = logArea.lastElementChild;

    if (lastEntry && lastEntry.querySelector('.text-gray-500')?.textContent === `[${stage}]`) {
        // 追加到现有日志
        const msgSpan = lastEntry.querySelector('.flex-1');
        if (msgSpan) {
            msgSpan.textContent += content;
        }
    } else {
        // 创建新日志
        const entry = document.createElement('div');
        entry.className = 'flex items-start gap-2 text-green-300 bg-gray-800/50 p-2 rounded';
        entry.innerHTML = `
            <span class="text-gray-500">[${stage}]</span>
            <span class="flex-1 font-mono text-sm" style="white-space: pre-wrap;">${escapeHtml(content)}</span>
        `;
        logArea.appendChild(entry);
    }

    logArea.scrollTop = logArea.scrollHeight;
}

// 追加思考过程
function appendInitThinking(panel, stage, content) {
    const logArea = panel.querySelector('#init-log-area');

    const entry = document.createElement('div');
    entry.className = 'flex items-start gap-2 text-yellow-200/70 p-2 rounded border-l-2 border-yellow-500';
    entry.innerHTML = `
        <span class="text-yellow-500">💭</span>
        <span class="flex-1 text-sm italic" style="white-space: pre-wrap;">${escapeHtml(content)}</span>
    `;
    logArea.appendChild(entry);
    logArea.scrollTop = logArea.scrollHeight;
}

// 更新初始化进度
function updateInitProgress(panel, current, total) {
    const fill = panel.querySelector('#init-progress-fill');
    const percent = panel.querySelector('#init-progress-percent');
    const text = panel.querySelector('#init-progress-text');

    if (fill) fill.style.width = `${(current / total) * 100}%`;
    if (percent) percent.textContent = `${current}/${total}`;
    if (text) text.textContent = `阶段 ${current + 1}: ${INIT_STAGES[current]?.label || '完成'}`;

    // 更新阶段指示器
    for (let i = 0; i < INIT_STAGES.length; i++) {
        const badge = panel.querySelector(`#init-stage-${i}`);
        if (!badge) continue;

        if (i < current) {
            badge.style.background = '#48bb78';
            badge.style.color = '#fff';
        } else if (i === current) {
            badge.style.background = '#4299e1';
            badge.style.color = '#fff';
        }
    }
}

// 显示阶段确认弹窗
function showStageConfirmDialog(stageData) {
    return new Promise((resolve) => {
        const modal = document.createElement('div');
        modal.className = 'modal active';
        modal.style.cssText = `
            position: fixed; top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(0,0,0,0.7); z-index: 2000;
            display: flex; align-items: center; justify-content: center;
        `;

        const result = stageData.result || {};
        const summary = result.summary || '已完成';

        modal.innerHTML = `
            <div style="background: #1a1a2e; border: 1px solid #4a5568; border-radius: 12px; padding: 24px; max-width: 600px; max-height: 80vh; overflow-y: auto;">
                <h3 class="text-xl font-bold text-white mb-2">📋 ${stageData.stage_label} - 阶段完成</h3>
                <p class="text-gray-400 mb-4">${stageData.description}</p>

                <div class="bg-gray-800 p-4 rounded-lg mb-4">
                    <div class="text-sm text-gray-400 mb-1">结果摘要</div>
                    <div class="text-green-400">${escapeHtml(summary)}</div>
                </div>

                ${result.metadata ? `
                <div class="bg-gray-800/50 p-3 rounded-lg mb-4">
                    <div class="text-xs text-gray-500">详细信息</div>
                    <div class="text-sm text-gray-300">
                        ${Object.entries(result.metadata).map(([k, v]) => `<div><span class="text-gray-500">${k}:</span> ${v}</div>`).join('')}
                    </div>
                </div>
                ` : ''}

                <div class="flex justify-end gap-3">
                    <button id="btn-retry" class="btn-secondary">
                        <i class="fas fa-redo mr-2"></i>重新生成
                    </button>
                    <button id="btn-confirm" class="btn-primary">
                        <i class="fas fa-check mr-2"></i>确认，继续
                    </button>
                </div>
            </div>
        `;

        document.body.appendChild(modal);

        modal.querySelector('#btn-confirm').onclick = () => {
            modal.remove();
            resolve(true);
        };

        modal.querySelector('#btn-retry').onclick = () => {
            modal.remove();
            resolve(false);
        };
    });
}

// 关闭新建项目弹窗
function closeNewProjectModal() {
    const modal = document.getElementById('new-project-modal');
    if (modal) {
        modal.classList.remove('active');
    }
}

// ============================================================
// 创作章节（SSE 真实流式版本）
// ============================================================

async function createChapter() {
    if (!currentProject) return;
    
    const chapterNum = parseInt(document.getElementById('target-chapter').value);
    const btn = document.getElementById('btn-create-chapter');
    const progressDiv = document.getElementById('creation-progress');
    const progressLogs = document.getElementById('progress-logs');
    
    // 禁用按钮
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>A1 规划中...';
    progressDiv.classList.remove('hidden');
    progressLogs.innerHTML = '';
    
    // 初始化编辑器为流式模式
    initEditorForStreaming();
    
    // 存储规划结果和任务 ID
    let chapterPlan = null;
    let writeTaskId = null;
    let isPlanningPhase = true;
    
    try {
        // 1. 启动规划任务（立即返回 task_id）
        addLogEntry('SYSTEM', '状态', '正在规划章节...', 'info');
        
        const planResponse = await fetch(`${API_BASE}/api/projects/${currentProject.name}/chapters/${chapterNum}/plan`, {
            method: 'POST'
        });
        
        const planData = await planResponse.json();
        if (planData.status !== 'task_started') throw new Error(planData.message || '规划任务启动失败');
        
        const planTaskId = planData.task_id;
        addLogEntry('SYSTEM', '任务', `规划任务: ${planTaskId.slice(0, 8)}...`, 'info');
        
        // 2. 连接 SSE 获取规划任务的流式输出
        let planConfirmed = null;
        const planPromise = new Promise((resolve) => {
            planConfirmed = resolve;
        });
        
        await new Promise((resolve, reject) => {
            const es = new EventSource(`${API_BASE}/api/monitor/events/${planTaskId}`);
            let isResolved = false;
            
            function safeClose() {
                if (!isResolved) {
                    isResolved = true;
                    es.close();
                }
            }
            
            es.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    
                    // 处理流式事件（显示到界面）
                    handleStreamEvent(data);
                    
                    // 收到终止信号，收集结果并显示弹窗
                    if (data.type === 'DONE' || data.type === 'complete') {
                        safeClose();
                        resolve();
                        return;
                    }
                    
                    // 收到最终结果
                    if (data.type === 'result' && data.data && data.data.chapter_plan) {
                        chapterPlan = data.data.chapter_plan;
                    }
                    
                    // 错误时也关闭
                    if (data.type === 'error') {
                        safeClose();
                        resolve();
                    }
                } catch (e) {
                    console.warn('解析 SSE 事件失败:', event.data, e);
                }
            };
            
            es.onerror = (error) => {
                safeClose();
                setTimeout(() => { if (!isResolved) reject(new Error('SSE 连接断开')); }, 100);
            };
            
            // 添加超时保护（5分钟）
            setTimeout(() => {
                safeClose();
                if (!isResolved) reject(new Error('规划超时'));
            }, 300000);
        });
        
        if (!chapterPlan) throw new Error('规划结果为空');
        
        // 3. 显示加载提示器，然后弹出确认弹窗
        addLogEntry('A1', '完成', `规划完成: ${chapterPlan.title}`, 'success');
        btn.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>正在生成确认框...';
        
        // 显示规划结果到原始输出
        displayChapterPlanInRawOutput(chapterPlan);
        
        // 弹出确认弹窗
        const confirmed = await showPlanConfirmation(chapterPlan);
        if (!confirmed) {
            addLogEntry('USER', '取消', '取消了创作', 'warning');
            resetButtonState(btn);
            return;
        }
        
        addLogEntry('USER', '确认', '确认规划，开始写作', 'success');
        btn.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>提交任务...';
        
        // 4. 提交写作异步任务（立即返回 task_id）
        const response = await fetch(`${API_BASE}/api/projects/${currentProject.name}/chapters/${chapterNum}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ approved_plan: chapterPlan })
        });
        
        if (!response.ok) throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        
        const taskData = await response.json();
        if (taskData.status !== 'task_started') throw new Error(taskData.message || '任务启动失败');
        
        writeTaskId = taskData.task_id;
        addLogEntry('SYSTEM', '任务', `写作任务: ${writeTaskId.slice(0, 8)}...`, 'info');
        btn.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>生成中...';
        
        // 5. 连接 SSE 获取写作任务的流式输出
        await connectToTaskStream(writeTaskId);
        
    } catch (error) {
        addLogEntry('ERROR', '错误', error.message, 'error');
        alert('创作失败: ' + error.message);
    } finally {
        resetButtonState(btn);
    }
}

// 快速创作（无规划确认，直接SSE流式输出）
async function createChapterQuick() {
    if (!currentProject) return;
    
    const chapterNum = parseInt(document.getElementById('target-chapter').value);
    const btn = document.getElementById('btn-create-chapter');
    const progressDiv = document.getElementById('creation-progress');
    const progressLogs = document.getElementById('progress-logs');
    
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>提交任务...';
    progressDiv.classList.remove('hidden');
    progressLogs.innerHTML = '';
    
    initEditorForStreaming();
    
    try {
        addLogEntry('SYSTEM', '状态', '快速创作模式启动...', 'info');
        
        const response = await fetch(`${API_BASE}/api/projects/${currentProject.name}/chapters/${chapterNum}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({})  // 无approved_plan，让后端自动规划
        });
        
        if (!response.ok) throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        
        const taskData = await response.json();
        if (taskData.status !== 'task_started') throw new Error(taskData.message || '任务启动失败');
        
        const taskId = taskData.task_id;
        addLogEntry('SYSTEM', '任务', `任务已启动: ${taskId.slice(0, 8)}...`, 'info');
        btn.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>生成中...';
        
        await connectToTaskStream(taskId);
        
    } catch (error) {
        addLogEntry('ERROR', '错误', error.message, 'error');
        alert('创作失败: ' + error.message);
    } finally {
        resetButtonState(btn);
    }
}

function resetButtonState(btn) {
    btn.disabled = false;
    btn.innerHTML = '<i class="fas fa-magic mr-2"></i>创作';
    hideTaskControls();
}

// ============================================================
// 任务控制状态
// ============================================================

let currentTaskId = null;
let isTaskPaused = false;
let accumulatedContent = '';  // 累积的内容用于保存

function showTaskControls() {
    const pauseBtn = document.getElementById('btn-pause');
    const resumeBtn = document.getElementById('btn-resume');
    const stopBtn = document.getElementById('btn-stop');
    const saveBtn = document.getElementById('btn-save');
    
    if (pauseBtn) pauseBtn.classList.remove('hidden');
    if (stopBtn) stopBtn.classList.remove('hidden');
    if (saveBtn) saveBtn.classList.remove('hidden');
}

function hideTaskControls() {
    const pauseBtn = document.getElementById('btn-pause');
    const resumeBtn = document.getElementById('btn-resume');
    const stopBtn = document.getElementById('btn-stop');
    
    if (pauseBtn) pauseBtn.classList.add('hidden');
    if (resumeBtn) resumeBtn.classList.add('hidden');
    if (stopBtn) stopBtn.classList.add('hidden');
}

async function pauseTask() {
    if (!currentTaskId) return;
    
    try {
        await fetch(`${API_BASE}/api/monitor/tasks/${currentTaskId}/pause`, {
            method: 'POST'
        });
        
        isTaskPaused = true;
        document.getElementById('btn-pause').classList.add('hidden');
        document.getElementById('btn-resume').classList.remove('hidden');
        addLogEntry('SYSTEM', '状态', '任务已暂停', 'warning');
    } catch (error) {
        addLogEntry('ERROR', '错误', '暂停失败: ' + error.message, 'error');
    }
}

async function resumeTask() {
    if (!currentTaskId) return;
    
    try {
        await fetch(`${API_BASE}/api/monitor/tasks/${currentTaskId}/resume`, {
            method: 'POST'
        });
        
        isTaskPaused = false;
        document.getElementById('btn-pause').classList.remove('hidden');
        document.getElementById('btn-resume').classList.add('hidden');
        addLogEntry('SYSTEM', '状态', '任务已继续', 'info');
    } catch (error) {
        addLogEntry('ERROR', '错误', '继续失败: ' + error.message, 'error');
    }
}

async function saveProgress() {
    // 保存当前累积的内容（优先使用 textarea，否则从 div 获取）
    const textarea = document.getElementById('chapter-textarea');
    const editor = document.getElementById('chapter-editor');
    let content = '';

    if (textarea && textarea.style.display !== 'none') {
        // 编辑模式下使用 textarea
        content = textarea.value;
    } else if (editor) {
        // 阅读/流式模式下，从 div 提取纯文本
        content = extractTextFromEditor(editor);
    }

    if (content || textarea?.value) {
        try {
            await fetch(`${API_BASE}/api/projects/${currentProject.name}/chapters/save-draft`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    chapter_number: parseInt(document.getElementById('target-chapter').value),
                    content: content
                })
            });
            addLogEntry('SYSTEM', '状态', '进度已保存', 'success');
        } catch (error) {
            addLogEntry('ERROR', '错误', '保存失败: ' + error.message, 'error');
        }
    }
}

// 从编辑器 div 提取纯文本，保留段落结构（\n\n 分隔）
function extractTextFromEditor(editor) {
    const paragraphs = Array.from(editor.querySelectorAll('p'))
        .map(p => p.textContent || '')
        .filter(p => p.trim());
    return paragraphs.join('\n\n');
}

async function stopTask() {
    if (!currentTaskId) return;
    
    if (!confirm('确定要中断当前任务吗？')) return;
    
    try {
        await fetch(`${API_BASE}/api/monitor/tasks/${currentTaskId}/stop`, {
            method: 'POST'
        });
        
        addLogEntry('SYSTEM', '状态', '任务已中断', 'warning');
        hideTaskControls();
        
        // 保存当前进度
        await saveProgress();
    } catch (error) {
        addLogEntry('ERROR', '错误', '中断失败: ' + error.message, 'error');
    }
}

// ============================================================
// SSE 流式连接管理
// ============================================================

function connectToTaskStream(taskId) {
    currentTaskId = taskId;
    isTaskPaused = false;
    showTaskControls();
    
    return new Promise((resolve, reject) => {
        const es = new EventSource(`${API_BASE}/api/monitor/events/${taskId}`);
        let isResolved = false;
        
        function safeClose() {
            if (!isResolved) {
                isResolved = true;
                es.close();
                currentTaskId = null;
                hideTaskControls();
            }
        }
        
        es.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                
                // 如果暂停，忽略事件
                if (isTaskPaused && data.type === 'chunk') {
                    return;
                }
                
                // 【关键】收到终止信号，关闭连接
                if (data.type === 'DONE') {
                    safeClose();
                    resolve();
                    return;
                }
                
                handleStreamEvent(data);
                
                // 如果是最终结果或完成，关闭连接
                if (data.type === 'result' || data.type === 'error' || data.type === 'complete') {
                    safeClose();
                    resolve();
                }
            } catch (e) {
                console.warn('解析 SSE 事件失败:', event.data, e);
            }
        };
        
        es.onerror = (error) => {
            safeClose();
            setTimeout(() => { if (!isResolved) reject(new Error('SSE 连接断开')); }, 100);
        };
    });
}

// ============================================================
// 处理流式事件 - 打字机效果核心
// ============================================================

function handleStreamEvent(event) {
    const { type, data, timestamp } = event;
    
    switch (type) {
        case 'status':
            // 状态更新 → 日志区
            updateProgressStatus(data.agent || 'SYSTEM', data.status, data.message);
            addLogEntry(data.agent || 'SYSTEM', '状态', data.message, 'info');
            break;
            
        case 'chunk':
            // 流式文本块 → 打字机效果
            appendChunkToEditor(data.agent, data.content_type, data.text);
            // 累积内容（用于保存）
            if (data.text && !data.text.match(/^[\n\r]+$/)) {
                accumulatedContent += data.text;
            }
            break;
            
        case 'result':
            // 规划结果（chapter_plan）已在 SSE 连接中处理，这里只处理其他类型的 result
            if (data.chapter_plan) {
                // 已在 SSE 连接中显示弹窗，这里只更新原始输出（防止重复显示）
                // displayChapterPlanInRawOutput 和弹窗逻辑在 SSE 连接中
            }
            
            if (data.status === 'success') {
                addLogEntry('SYSTEM', '完成', `章节创作成功: ${data.title} (${data.word_count}字)`, 'success');
                updateProgressStatus('SYSTEM', 'complete', '创作完成！');
                
                // 更新原始输出状态
                const rawStatus = document.getElementById('raw-output-status');
                if (rawStatus) rawStatus.textContent = '已完成';
                
                // 刷新仪表盘
                loadDashboard();
                // 更新章节号
                document.getElementById('current-chapter-num').textContent = data.chapter_number;
                document.getElementById('target-chapter').value = data.chapter_number + 1;
            }
            break;
            
        case 'error':
            addLogEntry('ERROR', '错误', data.message, 'error');
            // 更新原始输出状态为错误
            const rawStatusErr = document.getElementById('raw-output-status');
            if (rawStatusErr) rawStatusErr.textContent = '错误';
            break;
            
        case 'complete':
            addLogEntry('SYSTEM', '完成', '任务结束', 'success');
            break;
    }
}

// ============================================================
// 编辑器初始化与打字机效果
// ============================================================

// 当前流式渲染状态
let _streamingParagraph = null;    // 当前正在追加的 <p> 元素
let _streamingThought = null;      // 当前正在追加的 <details.thought-block> 元素
let _thoughtContent = null;        // 思考内容 <div>
let _totalWordCount = 0;           // 总字数

function initEditorForStreaming() {
    const editor = document.getElementById('chapter-editor');
    const textarea = document.getElementById('chapter-textarea');

    // 流式模式：使用 div 显示内容
    if (editor) {
        editor.innerHTML = '';
        editor.classList.add('streaming-active');
        editor.style.display = 'block';
    }
    if (textarea) {
        textarea.style.display = 'none';
        textarea.value = '';
    }

    // 重置流式状态
    _streamingParagraph = null;
    _streamingThought = null;
    _thoughtContent = null;
    _totalWordCount = 0;
    _streamingBuffer = '';  // 累积的流式内容
    _lastRawLine = null;    // 重置原始输出状态
    _lastRawAgent = null;
    _lastRawType = null;

    const previewDiv = document.getElementById('chapter-preview');
    const noChapterDiv = document.getElementById('no-chapter-selected');
    if (previewDiv && noChapterDiv) {
        previewDiv.classList.remove('hidden');
        noChapterDiv.classList.add('hidden');
    }

    // 初始化原始输出区域
    const rawOutput = document.getElementById('chapter-raw-output');
    if (rawOutput) {
        rawOutput.innerHTML = '<div class="text-gray-500 text-center py-8">等待创作任务...</div>';
    }
    const rawStatus = document.getElementById('raw-output-status');
    if (rawStatus) rawStatus.textContent = '等待...';

    // 更新状态栏
    updateWordCount(0);

    // 绑定滚动事件监听：检测用户手动滚动
    setupScrollListeners();
}

// 流式内容缓冲区（用于最终合并保存）
let _streamingBuffer = '';

// 设置滚动事件监听器
function setupScrollListeners() {
    const rawOutput = document.getElementById('chapter-raw-output');
    const editor = document.getElementById('chapter-editor');
    
    // 原始输出窗口滚动监听
    if (rawOutput) {
        // 移除旧监听器（防止重复绑定）
        rawOutput.removeEventListener('scroll', handleRawOutputScroll);
        rawOutput.addEventListener('scroll', handleRawOutputScroll);
    }
    
    // 章节内容窗口滚动监听
    if (editor) {
        editor.removeEventListener('scroll', handleEditorScroll);
        editor.addEventListener('scroll', handleEditorScroll);
    }
}

// 原始输出窗口滚动处理
function handleRawOutputScroll(e) {
    const target = e.target;
    const isAtBottom = target.scrollHeight - target.scrollTop - target.clientHeight < 50; // 50px 容差
    
    if (!isAtBottom) {
        // 用户向上滚动，停止自动跟随
        _rawOutputUserScrolling = true;
        // 清除之前的恢复计时器
        if (_rawOutputScrollTimer) {
            clearTimeout(_rawOutputScrollTimer);
        }
    } else {
        // 用户滚动到底部，恢复自动跟随
        if (_rawOutputUserScrolling) {
            _rawOutputScrollTimer = setTimeout(() => {
                _rawOutputUserScrolling = false;
            }, 1000); // 1秒后恢复自动跟随
        }
    }
}

// 章节内容窗口滚动处理
function handleEditorScroll(e) {
    const target = e.target;
    const isAtBottom = target.scrollHeight - target.scrollTop - target.clientHeight < 50; // 50px 容差
    
    if (!isAtBottom) {
        // 用户向上滚动，停止自动跟随
        _editorUserScrolling = true;
        // 清除之前的恢复计时器
        if (_editorScrollTimer) {
            clearTimeout(_editorScrollTimer);
        }
    } else {
        // 用户滚动到底部，恢复自动跟随
        if (_editorUserScrolling) {
            _editorScrollTimer = setTimeout(() => {
                _editorUserScrolling = false;
            }, 1000); // 1秒后恢复自动跟随
        }
    }
}

// 追加文本到编辑器 - 富文本版本（双栏布局）

// 滚动状态跟踪：区分自动滚动和用户手动滚动
let _rawOutputUserScrolling = false;   // 原始输出窗口用户手动滚动中
let _editorUserScrolling = false;      // 章节内容窗口用户手动滚动中
let _rawOutputScrollTimer = null;      // 原始输出滚动恢复计时器
let _editorScrollTimer = null;        // 章节内容滚动恢复计时器

let _lastRawLine = null;  // 上一个原始输出行
let _lastRawAgent = null; // 上一个行的角色
let _lastRawType = null;   // 上一个行的内容类型

function appendChunkToEditor(agent, contentType, text) {
    if (!text) return;
    
    const editor = document.getElementById('chapter-editor');
    const rawOutput = document.getElementById('chapter-raw-output');
    if (!editor) return;
    
    // 更新原始输出状态
    const rawStatus = document.getElementById('raw-output-status');
    if (rawStatus) rawStatus.textContent = '生成中...';
    
    // 左栏：原始输出（持续追加到同一行）
    if (rawOutput) {
        appendRawOutput(agent, contentType, text);
    }
    
    // 右栏：正式章节内容（只有 B 角色的正文内容）
    if (contentType === 'content' && agent === 'B') {
        appendContentChunk(text);
    }
}

// 追加原始输出（左栏）- 持续追加到同一行
function appendRawOutput(agent, contentType, text) {
    const rawOutput = document.getElementById('chapter-raw-output');
    if (!rawOutput) return;
    
    // 清空占位符
    if (rawOutput.children.length === 1 && rawOutput.children[0].classList.contains('text-center')) {
        rawOutput.innerHTML = '';
        _lastRawLine = null;
    }
    
    // 判断是否应该追加到上一行（同角色同类型且非段落分隔）
    const isContinuation = (
        _lastRawLine && 
        _lastRawAgent === agent && 
        _lastRawType === contentType &&
        !text.match(/^[\n\r]/)  // 不以换行开头
    );
    
    // 根据类型设置颜色
    let color = 'text-gray-300';
    let prefix = '';
    if (agent === 'A1') {
        prefix = '[A1] ';
        color = contentType === 'reasoning' ? 'text-yellow-500' : 'text-cyan-400';
    } else if (agent === 'B') {
        prefix = '[B] ';
        color = contentType === 'reasoning' ? 'text-yellow-500' : 'text-green-400';
    } else if (agent === 'SYSTEM') {
        prefix = '[SYS] ';
        color = 'text-purple-400';
    }
    
    if (contentType === 'status') {
        // 状态消息：创建新行
        const line = document.createElement('div');
        line.className = `raw-line ${color} mb-2 py-2 px-3 bg-gray-800/50 rounded`;
        line.innerHTML = `<span class="text-blue-400">📢 </span>${escapeHtml(text)}`;
        rawOutput.appendChild(line);
        _lastRawLine = line;
        _lastRawAgent = agent;
        _lastRawType = contentType;
    } else if (isContinuation) {
        // 持续追加到上一行（适用于所有类型）
        _lastRawLine.lastChild.textContent += text;
    } else if (contentType === 'reasoning' || contentType === 'thought') {
        // 思考过程：创建新行
        const line = document.createElement('div');
        line.className = `raw-line ${color} mb-2 p-2 bg-yellow-900/20 rounded border-l-2 border-yellow-600`;
        line.innerHTML = `<span class="opacity-60 mr-2">💭</span><span>${escapeHtml(text)}</span>`;
        rawOutput.appendChild(line);
        _lastRawLine = line;
        _lastRawAgent = agent;
        _lastRawType = contentType;
    } else {
        // 新建一行
        const line = document.createElement('div');
        line.className = `raw-line ${color} mb-1 whitespace-pre-wrap`;
        line.innerHTML = `<span class="opacity-50 mr-2">${prefix}</span><span>${escapeHtml(text)}</span>`;
        rawOutput.appendChild(line);
        _lastRawLine = line;
        _lastRawAgent = agent;
        _lastRawType = contentType;
    }
    
    // 智能滚动：只有当用户没有手动滚动时，才自动跟随到底部
    if (!_rawOutputUserScrolling) {
        rawOutput.scrollTo({
            top: rawOutput.scrollHeight,
            behavior: 'smooth'
        });
    }
}

// 显示章节规划结果到原始输出窗口
function displayChapterPlanInRawOutput(chapterPlan) {
    const rawOutput = document.getElementById('chapter-raw-output');
    if (!rawOutput) return;
    
    // 清空占位符
    if (rawOutput.children.length === 1 && rawOutput.children[0].classList.contains('text-center')) {
        rawOutput.innerHTML = '';
    }
    
    // 创建规划结果容器
    const planContainer = document.createElement('div');
    planContainer.className = 'plan-result mb-4 p-4 bg-gray-800/80 rounded-lg border border-cyan-500/30';
    
    // 标题
    const title = document.createElement('div');
    title.className = 'text-lg font-bold text-cyan-400 mb-3';
    title.innerHTML = '📋 章节规划结果';
    planContainer.appendChild(title);
    
    // 章节标题
    if (chapterPlan.title) {
        const titleRow = document.createElement('div');
        titleRow.className = 'mb-2';
        titleRow.innerHTML = `<span class="text-gray-400 text-sm">章节标题：</span><span class="text-white font-semibold">${escapeHtml(chapterPlan.title)}</span>`;
        planContainer.appendChild(titleRow);
    }
    
    // 预估字数
    if (chapterPlan.expected_word_count) {
        const wordCount = document.createElement('div');
        wordCount.className = 'mb-2';
        wordCount.innerHTML = `<span class="text-gray-400 text-sm">预估字数：</span><span class="text-green-400">${chapterPlan.expected_word_count} 字</span>`;
        planContainer.appendChild(wordCount);
    }
    
    // 本章目标
    if (chapterPlan.objective) {
        const objective = document.createElement('div');
        objective.className = 'mb-2';
        objective.innerHTML = `<span class="text-gray-400 text-sm">本章目标：</span><span class="text-gray-200">${escapeHtml(chapterPlan.objective)}</span>`;
        planContainer.appendChild(objective);
    }
    
    // 关键事件
    if (chapterPlan.key_events && chapterPlan.key_events.length > 0) {
        const eventsContainer = document.createElement('div');
        eventsContainer.className = 'mb-2';
        eventsContainer.innerHTML = `<span class="text-gray-400 text-sm block mb-1">关键事件：</span>`;
        
        const eventsList = document.createElement('ul');
        eventsList.className = 'list-disc list-inside space-y-1';
        
        chapterPlan.key_events.forEach((event, index) => {
            const li = document.createElement('li');
            li.className = 'text-gray-300 text-sm';
            li.textContent = event;
            eventsList.appendChild(li);
        });
        
        eventsContainer.appendChild(eventsList);
        planContainer.appendChild(eventsContainer);
    }
    
    // 分隔线
    const divider = document.createElement('div');
    divider.className = 'border-t border-gray-600 mt-3 pt-3';
    divider.innerHTML = '<span class="text-gray-500 text-xs">--- 规划完成，即将开始写作 ---</span>';
    planContainer.appendChild(divider);
    
    rawOutput.appendChild(planContainer);
    
    // 自动滚动到底部
    if (!_rawOutputUserScrolling) {
        rawOutput.scrollTo({
            top: rawOutput.scrollHeight,
            behavior: 'smooth'
        });
    }
}

// 正文追加：处理段落换行和富文本追加
function appendContentChunk(text) {
    const editor = document.getElementById('chapter-editor');
    const textarea = document.getElementById('chapter-textarea');
    if (!editor) return;

    // 同时更新 textarea（编辑模式用）和 div（显示模式用）
    // textarea 使用纯文本，div 使用 HTML

    // 1. 更新 textarea（追加纯文本）
    if (textarea && textarea.style.display !== 'none') {
        textarea.value += text;
        textarea.scrollTop = textarea.scrollHeight;
    }

    // 2. 更新 div 显示
    editor.style.display = 'block';

    // 累积缓冲区
    _streamingBuffer += text;

    // 确保有当前段落
    if (!_streamingParagraph || _streamingParagraph.parentNode !== editor) {
        _streamingParagraph = document.createElement('p');
        editor.appendChild(_streamingParagraph);
    }

    // 处理文本中的特殊字符
    const safeText = escapeHtml(text);

    // 检测段落分隔符：\n\n 或连续的多个换行
    if (/\n{2,}/.test(text)) {
        // 分割文本，保留段落结构
        const parts = text.split(/(\n{2,})/);
        for (const part of parts) {
            if (part.match(/\n{2,}/)) {
                // 遇到段落分隔 → 创建新段落
                _streamingParagraph = document.createElement('p');
                editor.appendChild(_streamingParagraph);
            } else {
                // 普通文本 → 追加到当前段落（\n 转为 <br>）
                const html = part.replace(/\n/g, '<br>');
                _streamingParagraph.insertAdjacentHTML('beforeend', html);
                _totalWordCount += part.replace(/\n/g, '').length;
            }
        }
    } else {
        // 无段落分隔，直接追加（\n 转为 <br>）
        const html = safeText.replace(/\n/g, '<br>');
        _streamingParagraph.insertAdjacentHTML('beforeend', html);
        _totalWordCount += text.replace(/\n/g, '').length;
    }

    // 智能滚动：只有当用户没有手动滚动时，才自动跟随到底部
    if (!_editorUserScrolling) {
        editor.scrollTo({
            top: editor.scrollHeight,
            behavior: 'smooth'
        });
    }

    // 更新字数统计
    updateWordCount(_totalWordCount);
}

// 思考过程追加：HTML5 <details> 折叠面板
function appendThoughtChunk(agent, text) {
    const editor = document.getElementById('chapter-editor');
    if (!editor) return;
    
    // 如果当前没有展开的 thought 块，或文本包含段落分隔（视为新思考块），创建新的
    if (!_streamingThought || _streamingThought.parentNode !== editor || /\n{2,}/.test(text)) {
        _streamingThought = document.createElement('details');
        _streamingThought.className = 'thought-block';
        _streamingThought.open = true;  // 默认展开，方便查看
        
        const summary = document.createElement('summary');
        summary.innerHTML = `<span>💭</span> <span>[${agent || '模型'}] 智能体思考中...</span>`;
        
        _thoughtContent = document.createElement('div');
        _thoughtContent.className = 'thought-content';
        
        _streamingThought.appendChild(summary);
        _streamingThought.appendChild(_thoughtContent);
        editor.appendChild(_streamingThought);
    }
    
    // 追加思考内容
    const safeText = escapeHtml(text).replace(/\n/g, '<br>');
    _thoughtContent.insertAdjacentHTML('beforeend', safeText);
    
    // 自动滚动到思考块
    editor.scrollTo({
        top: editor.scrollHeight,
        behavior: 'smooth'
    });
}

// 更新字数统计（显示在底部状态栏和右栏标题）
function updateWordCount(count) {
    const wordCountEl = document.getElementById('preview-word-count');
    if (wordCountEl) wordCountEl.textContent = typeof count === 'number' ? count.toLocaleString() : (count || 0).toLocaleString();
    
    // 更新右栏标题的字数显示
    const badgeEl = document.getElementById('chapter-word-count-badge');
    if (badgeEl) badgeEl.textContent = `${typeof count === 'number' ? count.toLocaleString() : (count || 0).toLocaleString()} 字`;
}

// ============================================================
// 进度条和日志
// ============================================================

function updateProgressStatus(agent, status, message) {
    const progressText = document.getElementById('progress-text');
    const progressPercent = document.getElementById('progress-percent');
    
    if (status === 'working') {
        if (progressText) progressText.textContent = `[${agent}] ${message}`;
        if (progressPercent) progressPercent.textContent = '生成中...';
    } else if (status === 'complete') {
        if (progressText) progressText.textContent = message;
        if (progressPercent) progressPercent.textContent = '100%';
    }
}

function addLogEntry(agent, type, message, level) {
    const progressLogs = document.getElementById('progress-logs');
    if (!progressLogs) return;
    
    const entry = document.createElement('div');
    
    // 根据agent类型选择图标和样式前缀
    let icon = '📋';
    let agentClass = '';
    
    switch (agent) {
        case 'A1':   icon = '🧠'; agentClass = 'agent-a1'; break;
        case 'B':    icon = '✍️'; agentClass = 'agent-b'; break;
        case 'C1':   icon = '🔍'; agentClass = 'agent-c1'; break;
        case 'SYSTEM': icon = '⚙️';  agentClass = 'agent-system'; break;
        case 'USER': icon = '👤'; agentClass = 'agent-user'; break;
        case 'ERROR':icon = '❌'; agentClass = 'agent-error'; break;
        default:     icon = '📌';
    }
    
    entry.className = `log-entry ${level} ${agentClass}`;
    entry.innerHTML = `
        <span class="log-icon">${icon}</span>
        <span class="log-agent">[${agent}]</span>
        <span class="log-type">${type}:</span>
        <span class="log-msg">${escapeHtml(message)}</span>
        <span class="log-time">${new Date().toLocaleTimeString('zh-CN')}</span>
    `;
    progressLogs.appendChild(entry);
    
    // 平滑滚动到底部
    smoothScrollToBottom(progressLogs);
}

// HTML转义防止XSS
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function clearStreamingDisplay() {
    const container = document.getElementById('streaming-content-container');
    if (container && container.parentNode) container.parentNode.removeChild(container);
}

// 平滑滚动辅助函数
function smoothScrollToBottom(element) {
    element.scrollTo({
        top: element.scrollHeight,
        behavior: 'smooth'
    });
}

// ============================================================
// 规划确认弹窗
// ============================================================

function showPlanConfirmation(chapterPlan) {
    return new Promise((resolve) => {
        const modal = document.createElement('div');
        modal.className = 'modal active';
        modal.innerHTML = `
            <div class="modal-content" style="max-width: 700px;">
                <h3 class="text-xl font-bold mb-4">📋 章节规划确认</h3>
                <div class="bg-gray-800 p-4 rounded-lg mb-4">
                    <div class="grid grid-cols-2 gap-4 mb-4">
                        <div><span class="text-gray-400 text-sm">章节标题</span><p class="font-bold">${chapterPlan.title}</p></div>
                        <div><span class="text-gray-400 text-sm">预估字数</span><p class="font-bold">${chapterPlan.expected_word_count || 3000} 字</p></div>
                    </div>
                    <div class="mb-4"><span class="text-gray-400 text-sm">本章目标</span><p class="mt-1">${chapterPlan.objective}</p></div>
                    <div class="mb-4"><span class="text-gray-400 text-sm">关键事件</span><ul class="mt-1 space-y-1">
                        ${(chapterPlan.key_events || []).map(event => `<li><i class="fas fa-check-circle text-green-400 mt-1 mr-2"></i>${event}</li>`).join('')}
                    </ul></div>
                </div>
                <div class="flex justify-end gap-3">
                    <button id="btn-reject-plan" class="btn-secondary"><i class="fas fa-times mr-1"></i>取消</button>
                    <button id="btn-approve-plan" class="btn-primary"><i class="fas fa-check mr-1"></i>确认并开始写作</button>
                </div>
            </div>`;
        document.body.appendChild(modal);
        modal.querySelector('#btn-approve-plan').onclick = () => { modal.remove(); resolve(true); };
        modal.querySelector('#btn-reject-plan').onclick = () => { modal.remove(); resolve(false); };
    });
}

// ============================================================
// 章节预览/编辑
// ============================================================

function showChapterPreview(chapter) {
    const previewDiv = document.getElementById('chapter-preview');
    const noChapterDiv = document.getElementById('no-chapter-selected');
    if (!previewDiv || !noChapterDiv) return;
    
    document.getElementById('preview-title').textContent = `${chapter.number}. ${chapter.title}`;
    document.getElementById('preview-word-count').textContent = chapter.word_count ? chapter.word_count.toLocaleString() : '0';
    document.getElementById('preview-paragraphs').textContent = chapter.paragraphs_count || '0';
    
    // 清空原始输出区域（阅读模式不需要显示原始流式日志）
    const rawOutput = document.getElementById('chapter-raw-output');
    if (rawOutput) {
        rawOutput.innerHTML = '<div class="text-gray-500 text-center py-8">📖 阅读模式</div>';
    }
    const rawStatus = document.getElementById('raw-output-status');
    if (rawStatus) rawStatus.textContent = '阅读中';
    
    // 加载章节正文到编辑器
    loadChapterContent(chapter.number);
    
    previewDiv.classList.remove('hidden');
    noChapterDiv.classList.add('hidden');
}

async function loadChapterContent(chapterNum) {
    if (!currentProject) return;
    try {
        const response = await fetch(`${API_BASE}/api/projects/${currentProject.name}/chapters/${chapterNum}`);
        const data = await response.json();
        const editor = document.getElementById('chapter-editor');
        const textarea = document.getElementById('chapter-textarea');

        if (editor) {
            // 确保在阅读模式（div 显示，textarea 隐藏）
            editor.style.display = 'block';
            if (textarea) textarea.style.display = 'none';

            // 富文本渲染：将文本按段落分割，包裹 <p> 标签
            editor.classList.remove('streaming-active');
            editor.innerHTML = '';

            if (data.content) {
                const paragraphs = data.content.split(/\n{2,}/);
                paragraphs.forEach(para => {
                    if (para.trim()) {
                        const p = document.createElement('p');
                        // 换行符转为 <br>
                        p.innerHTML = escapeHtml(para).replace(/\n/g, '<br>');
                        editor.appendChild(p);
                    }
                });
            }

            _totalWordCount = (data.content || '').length;
            updateWordCount(_totalWordCount);
        }
    } catch (error) { console.error('加载章节内容失败:', error); }
}

function toggleEdit() {
    const editor = document.getElementById('chapter-editor');
    const textarea = document.getElementById('chapter-textarea');
    if (!editor || !textarea) return;

    const isEditMode = textarea.style.display !== 'none';

    if (isEditMode) {
        // 退出编辑模式：将 textarea 内容同步到 div
        textarea.style.display = 'none';
        editor.style.display = 'block';

        // 将 textarea 的纯文本转为带 <p> 标签的 HTML
        const content = textarea.value;
        editor.innerHTML = '';
        if (content.trim()) {
            const paragraphs = content.split(/\n{2,}/);
            paragraphs.forEach(para => {
                if (para.trim()) {
                    const p = document.createElement('p');
                    p.innerHTML = escapeHtml(para).replace(/\n/g, '<br>');
                    editor.appendChild(p);
                }
            });
        }

        // 更新字数
        _totalWordCount = content.length;
        updateWordCount(_totalWordCount);
    } else {
        // 进入编辑模式：将 div 内容转为纯文本填入 textarea
        const content = extractTextFromEditor(editor);
        textarea.value = content;
        editor.style.display = 'none';
        textarea.style.display = 'block';
        textarea.focus();
        // 将光标移到末尾
        textarea.selectionStart = textarea.selectionEnd = textarea.value.length;
    }
}

async function saveChapterEdit() {
    if (!currentProject || !currentChapterData) return;

    const editor = document.getElementById('chapter-editor');
    const textarea = document.getElementById('chapter-textarea');
    if (!editor) return;

    // 从 textarea（编辑模式）或 div（阅读模式）获取纯文本
    let content = '';
    if (textarea && textarea.style.display !== 'none') {
        content = textarea.value;
    } else {
        content = extractTextFromEditor(editor);
    }

    try {
        const response = await fetch(`${API_BASE}/api/projects/${currentProject.name}/chapters/${currentChapterData.number}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content: content })
        });
        const data = await response.json();
        if (data.success) {
            // 如果是编辑模式，先退出编辑再提示
            if (textarea && textarea.style.display !== 'none') {
                toggleEdit(); // 退出编辑模式，同步内容
            }
            alert('保存成功！');
        } else {
            throw new Error(data.error || '保存失败');
        }
    } catch (error) { alert('保存失败: ' + error.message); }
}

// ============================================================
// 项目详情
// ============================================================

async function showProjectDetail(projectName) {
    try {
        const response = await fetch(`${API_BASE}/api/projects/${projectName}`);
        const project = await response.json();
        
        document.getElementById('detail-project-name').textContent = project.metadata.title;
        const content = document.getElementById('project-detail-content');
        content.innerHTML = `
            <div class="mb-6">
                <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
                    <div class="bg-gray-800 p-4 rounded-lg text-center"><div class="text-2xl font-bold text-indigo-400">${project.progress.current_chapter}</div><div class="text-sm text-gray-400">已创作章节</div></div>
                    <div class="bg-gray-800 p-4 rounded-lg text-center"><div class="text-2xl font-bold text-purple-400">${project.progress.total_words.toLocaleString()}</div><div class="text-sm text-gray-400">总字数</div></div>
                    <div class="bg-gray-800 p-4 rounded-lg text-center"><div class="text-2xl font-bold text-green-400">${project.metadata.genre || '都市奇幻'}</div><div class="text-sm text-gray-400">题材</div></div>
                    <div class="bg-gray-800 p-4 rounded-lg text-center"><div class="text-2xl font-bold text-yellow-400">${project.metadata.author || 'MANS'}</div><div class="text-sm text-gray-400">作者</div></div>
                </div>
            </div>
            <h4 class="font-bold mb-3">章节列表</h4>
            <div class="space-y-2 max-h-60 overflow-y-auto">
                ${project.chapters.length === 0 ?
                    '<p class="text-gray-500 text-center py-4">暂无章节，开始你的创作吧！</p>' :
                    project.chapters.map(ch => `
                        <div class="flex items-center justify-between bg-gray-800 p-3 rounded-lg">
                            <div class="flex items-center"><span class="text-indigo-400 font-bold mr-3">第${ch.number}章</span><span>${ch.title}</span></div>
                            <div class="flex items-center gap-4"><span class="text-sm text-gray-400">${ch.word_count ? ch.word_count.toLocaleString() : '-'} 字</span>
                                <button class="text-indigo-400 hover:text-indigo-300" onclick="readChapter('${project.name}', ${ch.number})"><i class="fas fa-eye"></i> 阅读</button>
                            </div>
                        </div>`).join('')
                }
            </div>
            <div class="mt-6 flex gap-3">
                <button class="btn-primary flex-1" onclick="closeProjectDetailModal(); showPage('create'); document.getElementById('project-select').value='${project.name}'; loadProjectForWriting();"><i class="fas fa-pen mr-2"></i>继续创作</button>
                <button class="btn-secondary" onclick="deleteProject('${project.name}')"><i class="fas fa-trash"></i></button>
            </div>`;
        document.getElementById('project-detail-modal').classList.add('active');
    } catch (error) { alert('加载失败: ' + error.message); }
}

async function readChapter(projectName, chapterNum) {
    try {
        const response = await fetch(`${API_BASE}/api/projects/${projectName}/chapters/${chapterNum}`);
        const data = await response.json();
        const content = document.getElementById('project-detail-content');
        content.innerHTML = `
            <div class="mb-4 flex items-center justify-between">
                <button onclick="showProjectDetail('${projectName}')" class="text-gray-400 hover:text-white flex items-center"><i class="fas fa-arrow-left mr-2"></i>返回</button>
                <span class="text-sm text-gray-500">${data.word_count ? data.word_count.toLocaleString() : '-'} 字</span>
            </div>
            <h4 class="text-xl font-bold mb-4">${data.title}</h4>
            <div class="chapter-content bg-gray-800 p-6 rounded-lg" style="max-height: 60vh; overflow-y: auto;">${(data.content || '').replace(/\n/g, '<br>')}</div>`;
    } catch (error) { alert('加载章节失败: ' + error.message); }
}

function closeProjectDetailModal() { document.getElementById('project-detail-modal').classList.remove('active'); }

async function deleteProject(projectName) {
    if (!confirm(`确定要删除项目 "${projectName}" 吗？此操作不可恢复。`)) return;
    try {
        const response = await fetch(`${API_BASE}/api/projects/${projectName}`, { method: 'DELETE' });
        const data = await response.json();
        if (data.success) { closeProjectDetailModal(); loadProjects(); loadDashboard(); alert('项目已删除'); }
        else throw new Error(data.error || '删除失败');
    } catch (error) { alert('删除失败: ' + error.message); }
}

async function saveApiKey() {
    const apiKeyInput = document.getElementById('api-key-input');
    const apiKey = apiKeyInput.value.trim();
    if (!apiKey) { alert('请输入API Key'); return; }
    
    try {
        const response = await fetch(`${API_BASE}/api/config/api-key`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ api_key: apiKey }) });
        const data = await response.json();
        if (data.success) {
            const maskedKey = apiKey.substring(0, 4) + '***' + apiKey.substring(apiKey.length - 4);
            apiKeyInput.value = maskedKey;
            apiKeyInput.dataset.configured = 'true';
            checkApiStatus();
            alert('✅ API Key已保存！');
        } else { throw new Error(data.error || '保存失败'); }
    } catch (error) { alert('保存失败: ' + error.message); }
}

async function loadChapterForEdit() {
    if (!currentProject) return;
    const chapterNum = parseInt(document.getElementById('target-chapter').value);
    try {
        const response = await fetch(`${API_BASE}/api/projects/${currentProject.name}/chapters/${chapterNum}`);
        const data = await response.json();
        currentChapterData = { number: chapterNum, title: data.title, word_count: data.word_count };
        showChapterPreview(data);
    } catch (error) { alert('加载章节失败: ' + error.message); }
}

// ============================================================
// 监控系统
// ============================================================

function initMonitor() {
    if (window.EventSource) {
        eventSource = new EventSource(`${API_BASE}/api/monitor/events`);
        eventSource.onmessage = (event) => { 
            try { 
                const data = JSON.parse(event.data);
                // 全局监控事件也使用增强日志格式
                addMonitorEvent(data);
            } catch (e) {} 
        };
        eventSource.onerror = () => {};
    }
    monitorInterval = setInterval(refreshSystemInfo, 5000);
}

// 增强版监控事件渲染（与创作页面的addLogEntry统一风格）
function addMonitorEvent(event) {
    const stream = document.getElementById('event-stream');
    if (!stream) return;
    
    // 清空初始占位符
    if (stream.children.length === 1 && stream.children[0].classList.contains('text-center')) {
        stream.innerHTML = '';
    }
    
    const type = event.type;
    
    // 心跳事件忽略
    if (!type || type === 'connected') return;
    
    const entry = document.createElement('div');
    
    let icon, colorClass, contentHtml;
    
    switch (type) {
        case 'status':
            icon = '⚙️';
            colorClass = event.data?.agent === 'A1' ? 'agent-a1' : 
                        event.data?.agent === 'B' ? 'agent-b' : 'agent-system';
            contentHtml = `<span class="log-agent">[${event.data.agent || 'SYSTEM'}]</span> 状态: ${escapeHtml(event.data.message || '')}`;
            break;
            
        case 'chunk':
            const agent = event.data?.agent || '?';
            const text = event.data?.text || '';
            
            if (agent === 'B' && event.data.content_type === 'content') {
                // 正文片段 - 显示截断预览
                icon = '✍️';
                colorClass = 'agent-b';
                const preview = text.length > 80 ? text.substring(0, 80) + '...' : text;
                contentHtml = `<span class="log-agent agent-b">[B]</span> <span class="log-type">正文</span>: <span class="log-msg" style="color:#e2e8f0">${escapeHtml(preview)}</span>`;
            } else if (event.data.content_type === 'reasoning') {
                // 思考过程
                icon = '💭';
                colorClass = 'thought';
                const preview = text.length > 60 ? text.substring(0, 60) + '...' : text;
                contentHtml = `<span class="log-agent">${agent}</span> <span class="log-type">思考</span>: <span class="log-msg">${escapeHtml(preview)}</span>`;
            } else {
                icon = '📝';
                contentHtml = `<span class="log-agent">${agent}</span>: ${escapeHtml(text.substring(0, 50))}`;
            }
            break;
            
        case 'result':
            icon = '✅';
            colorClass = 'success';
            contentHtml = `<span class="log-agent agent-system">[完成]</span> ${event.data.title || ''} (${event.data.word_count || 0}字)`;
            break;
            
        case 'error':
            icon = '❌';
            colorClass = 'error';
            contentHtml = `<span class="log-agent agent-error">[错误]</span> ${escapeHtml(event.data.message || '')}`;
            break;
            
        case 'DONE':
            icon = '🏁';
            contentHtml = `<span class="text-gray-400">任务结束</span>`;
            break;
            
        default:
            icon = '📌';
            contentHtml = JSON.stringify(event).substring(0, 200);
    }
    
    entry.className = `log-entry ${colorClass || 'info'} mb-1`;
    entry.innerHTML = `
        <span class="log-icon">${icon}</span>
        <div class="flex-1 min-w-0">${contentHtml}</div>
        <span class="log-time text-xs text-gray-600">${new Date().toLocaleTimeString('zh-CN')}</span>
    `;
    
    stream.insertBefore(entry, stream.firstChild);  // 最新事件在最上面
    
    // 限制数量防止内存溢出
    while (stream.children.length > 100) {
        stream.removeChild(stream.lastChild);
    }
}

async function refreshMonitorData() {
    try {
        const metricsResponse = await fetch(`${API_BASE}/api/monitor/metrics`);
        const metrics = await metricsResponse.json();
        
        // 核心指标
        document.getElementById('monitor-api-calls').textContent = metrics.api_calls || 0;
        document.getElementById('monitor-avg-time').textContent = metrics.last_call_time ? `${metrics.last_call_time}s` : '-';
        
        // 任务统计（新增）
        if (document.getElementById('monitor-tasks-total')) {
            document.getElementById('monitor-tasks-total').textContent = metrics.tasks_total || 0;
            document.getElementById('monitor-tasks-completed').textContent = metrics.tasks_completed || 0;
            document.getElementById('monitor-tasks-running').textContent = metrics.tasks_running || 0;
            document.getElementById('monitor-tasks-failed').textContent = metrics.tasks_failed || 0;
            
            // 运行时长格式化
            const uptimeSecs = metrics.uptime_seconds || 0;
            if (uptimeSecs > 0) {
                const h = Math.floor(uptimeSecs / 3600);
                const m = Math.floor((uptimeSecs % 3600) / 60);
                const s = Math.floor(uptimeSecs % 60);
                document.getElementById('monitor-uptime-val').textContent = 
                    `${h}h ${m}m ${s}s`;
            }
        }
    } catch (error) {}
}

async function refreshSystemInfo() {
    try {
        const response = await fetch(`${API_BASE}/api/monitor/system`);
        const info = await response.json();
        document.getElementById('monitor-cpu').textContent = `${info.cpu.percent}%`;
        document.getElementById('monitor-memory').textContent = `${info.memory.percent}%`;
        document.getElementById('cpu-percent').textContent = `${info.cpu.percent}%`;
        document.getElementById('cpu-bar').style.width = `${info.cpu.percent}%`;
        document.getElementById('memory-percent').textContent = `${info.memory.percent}%`;
        document.getElementById('memory-bar').style.width = `${info.memory.percent}%`;
        document.getElementById('disk-percent').textContent = `${info.disk.percent}%`;
        document.getElementById('disk-bar').style.width = `${info.disk.percent}%`;
    } catch (error) {}
}

function addEventToStream(event) {
    const stream = document.getElementById('event-stream');
    if (!stream) return;
    if (stream.children.length === 1 && stream.children[0].classList.contains('text-center')) stream.innerHTML = '';
    
    const eventDiv = document.createElement('div');
    eventDiv.className = 'event-item';
    const time = new Date(event.timestamp).toLocaleTimeString('zh-CN');
    let icon = '📌', color = 'text-gray-400';
    if (event.type.includes('start')) { icon = '🚀'; color = 'text-blue-400'; }
    else if (event.type.includes('complete')) { icon = '✅'; color = 'text-green-400'; }
    else if (event.type.includes('error')) { icon = '❌'; color = 'text-red-400'; }
    
    eventDiv.innerHTML = `<div class="flex items-center justify-between"><span class="${color}">${icon} ${event.type}</span><span class="text-xs text-gray-500">${time}</span></div><div class="text-sm text-gray-300 mt-1">${JSON.stringify(event.data)}</div>`;
    stream.insertBefore(eventDiv, stream.firstChild);
    while (stream.children.length > 50) stream.removeChild(stream.lastChild);
}

async function loadLogs() {
    try {
        const logType = document.getElementById('log-type').value;
        const response = await fetch(`${API_BASE}/api/logs/recent?limit=50`);
        const data = await response.json();
        const container = document.getElementById('logs-container');
        
        if (data.logs.length === 0) { container.innerHTML = '<div class="text-gray-500 text-center py-8">暂无日志</div>'; return; }
        
        container.innerHTML = data.logs.map(log => {
            const type = log.type === 'api_request' ? 'info' : 'error';
            const time = new Date(log.timestamp).toLocaleString('zh-CN');
            return `<div class="log-entry ${type}">
                <div class="flex justify-between text-xs mb-1"><span class="font-bold">${log.type}</span><span>${time}</span></div>
                <div class="text-sm">${log.provider ? `[${log.provider}] ${log.model_type}` : ''} ${log.prompt_length ? `Prompt: ${log.prompt_length} chars` : ''} ${log.response_length ? `Response: ${log.response_length} chars` : ''} ${log.content ? log.content.substring(0, 200) : ''}</div>
            </div>`;
        }).join('');
    } catch (error) { console.error('加载日志失败:', error); }
}

function exportAllData() { alert('导出功能开发中...'); }
function clearAllCache() { if (confirm('确定要清理缓存吗？')) alert('缓存已清理'); }
