/**
 * MANS 流式输出 - 快速集成示例
 * 
 * 这个文件演示如何将流式输出集成到现有的app.js中
 * 你可以参考这些代码修改你的app.js
 */

// ============================================
// 方式1: 最简单的集成 - 使用轮询模拟流式
// ============================================

async function generateBibleWithProgress() {
    if (!AppState.currentProject) return;
    
    const btn = document.getElementById('generate-bible-btn');
    btn.disabled = true;
    btn.textContent = '生成中...';
    
    // 显示简单的进度提示
    showMessage('开始生成 Bible...', 'info');
    
    try {
        // 调用后端API（非流式，但我们可以添加中间状态提示）
        const result = await apiRequest(
            `/api/projects/${AppState.currentProject}/generate/bible`,
            { method: 'POST' }
        );
        
        showMessage('Bible 生成成功！', 'success');
        displayBible(result.data);
        await checkInitializationStatus(AppState.currentProject);
        
    } catch (error) {
        showMessage('生成 Bible 失败: ' + error.message, 'error');
        btn.disabled = false;
    }
    
    btn.textContent = '生成 Bible';
}

// ============================================
// 方式2: 使用真正的SSE流式（推荐）
// ============================================

async function generateBibleStreaming() {
    if (!AppState.currentProject) return;
    
    const btn = document.getElementById('generate-bible-btn');
    btn.disabled = true;
    btn.textContent = '生成中...';
    
    // 创建/显示进度面板
    let progressPanel = document.getElementById('progress-panel');
    if (!progressPanel) {
        progressPanel = createProgressPanel();
        document.body.appendChild(progressPanel);
    }
    progressPanel.style.display = 'block';
    
    // 清空之前内容
    const logContainer = progressPanel.querySelector('.log-container');
    logContainer.innerHTML = '';
    
    try {
        // 添加初始日志
        addProgressLog(logContainer, '开始生成 Bible...');
        
        // 调用流式API
        const response = await fetch(
            `/api/projects/${AppState.currentProject}/stream/bible`,
            { method: 'POST' }
        );
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        
        // 读取SSE流
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let fullContent = '';
        
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            
            buffer += decoder.decode(value, { stream: true });
            
            // 处理SSE事件
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';
            
            for (const line of lines) {
                if (line.startsWith('data:')) {
                    try {
                        const data = JSON.parse(line.slice(5));
                        handleStreamEvent(data, logContainer);
                    } catch (e) {
                        console.warn('解析失败:', e);
                    }
                }
            }
        }
        
        addProgressLog(logContainer, '✅ 生成完成！', 'success');
        
    } catch (error) {
        addProgressLog(logContainer, `❌ 生成失败: ${error.message}`, 'error');
    }
    
    btn.disabled = false;
    btn.textContent = '生成 Bible';
}

// ============================================
// 辅助函数
// ============================================

function createProgressPanel() {
    const panel = document.createElement('div');
    panel.id = 'progress-panel';
    panel.innerHTML = `
        <div style="
            position: fixed;
            right: 20px;
            bottom: 20px;
            width: 500px;
            max-height: 400px;
            background: white;
            border-radius: 8px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.15);
            z-index: 1000;
            display: flex;
            flex-direction: column;
        ">
            <div style="
                padding: 12px 16px;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                border-radius: 8px 8px 0 0;
                display: flex;
                justify-content: space-between;
                align-items: center;
            ">
                <span style="font-weight: 600;">生成进度</span>
                <button onclick="this.parentElement.parentElement.parentElement.style.display='none'" 
                    style="background:none;border:none;color:white;cursor:pointer;font-size:20px;">×</button>
            </div>
            <div class="log-container" style="
                flex: 1;
                overflow-y: auto;
                padding: 16px;
                font-family: monospace;
                font-size: 13px;
                background: #f8f9fa;
            "></div>
        </div>
    `;
    return panel;
}

function addProgressLog(container, message, type = 'info') {
    const log = document.createElement('div');
    log.style.cssText = `
        padding: 8px;
        margin-bottom: 8px;
        border-radius: 4px;
        background: white;
        border-left: 3px solid ${type === 'error' ? '#ef4444' : type === 'success' ? '#10b981' : '#667eea'};
    `;
    
    const time = new Date().toLocaleTimeString('zh-CN');
    log.innerHTML = `
        <div style="font-size: 11px; color: #9ca3af; margin-bottom: 4px;">${time}</div>
        <div style="color: #374151;">${message}</div>
    `;
    
    container.appendChild(log);
    container.scrollTop = container.scrollHeight;
}

function handleStreamEvent(data, logContainer) {
    if (data.message) {
        addProgressLog(logContainer, data.message, 'info');
    }
    
    if (data.content) {
        // 显示LLM输出（可以累积后显示）
        addProgressLog(logContainer, data.content.substring(0, 100) + '...', 'token');
    }
    
    if (data.error) {
        addProgressLog(logContainer, `错误: ${data.error}`, 'error');
    }
    
    if (data.data) {
        // 显示完整结果
        addProgressLog(logContainer, '结果: ' + JSON.stringify(data.data).substring(0, 200) + '...', 'success');
    }
}

// ============================================
// 使用示例
// ============================================

// 在你的app.js中找到generateBible函数，替换为：
/*
async function generateBible() {
    // 选择一种方式：
    
    // 方式1: 简单轮询
    // await generateBibleWithProgress();
    
    // 方式2: 真正流式（推荐）
    await generateBibleStreaming();
}
*/
