/**
 * MANS SSE 客户端
 * 
 * 功能：
 * 1. 接收后端SSE流式事件
 * 2. 实时更新流式输出面板
 * 3. 处理生成进度、token输出、完成和错误事件
 * 
 * 使用方法：
 * 在需要流式生成的地方调用 startStreamingGeneration()
 */

// ============================================
// 全局状态
// ============================================

const StreamingState = {
    eventSource: null,
    isStreaming: false,
    fullContent: '',
    logs: [],
    startTime: null,
    currentTokenLog: null  // 当前token日志元素
};

// ============================================
// 核心函数
// ============================================

/**
 * 开始流式生成（以Bible为例）
 */
async function startStreamingGeneration(type = 'bible') {
    if (!AppState.currentProject) {
        showMessage('请先选择项目', 'error');
        return;
    }
    
    if (StreamingState.isStreaming) {
        showMessage('正在生成中，请稍候', 'warning');
        return;
    }
    
    // 显示流式面板
    showStreamingPanel(`生成${getTypeName(type)}...`);
    
    // 重置状态
    StreamingState.isStreaming = true;
    StreamingState.fullContent = '';
    StreamingState.logs = [];
    StreamingState.startTime = Date.now();
    
    // 添加开始日志
    addStreamingLog('progress', `开始${getTypeName(type)}生成...`);
    
    try {
        // 创建SSE连接
        const url = `/api/projects/${AppState.currentProject}/stream/${type}`;
        
        // 使用fetch + ReadableStream处理POST SSE
        const response = await fetch(url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            }
        });
        
        if (!response.ok) {
            // 读取错误响应体
            let errorMessage = `HTTP ${response.status}: ${response.statusText}`;
            try {
                const errorBody = await response.text();
                const errorJson = JSON.parse(errorBody);
                errorMessage = errorJson.detail || errorJson.message || errorMessage;
            } catch (e) {
                // 如果无法解析错误体，使用默认消息
            }
            throw new Error(errorMessage);
        }
        
        // 读取流式数据
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let lastEventType = 'message';
        
        while (true) {
            const { done, value } = await reader.read();
            
            if (done) {
                break;
            }
            
            // 解码数据
            buffer += decoder.decode(value, { stream: true });
            
            // 处理SSE事件
            const lines = buffer.split('\n');
            buffer = lines.pop() || ''; // 保留不完整的行
            
            for (const line of lines) {
                if (line.startsWith('event:')) {
                    lastEventType = line.slice(6).trim();
                    continue;
                }
                
                if (line.startsWith('data:')) {
                    const dataStr = line.slice(5).trim();
                    try {
                        const data = JSON.parse(dataStr);
                        handleSSEEvent(lastEventType, data);
                        // 重置事件类型
                        lastEventType = 'message';
                    } catch (e) {
                        console.warn('解析SSE数据失败:', e, dataStr);
                    }
                }
            }
        }
        
        // 只有在没有错误的情况下才显示完成消息
        if (StreamingState.status !== 'error') {
            addStreamingLog('complete', '生成完成！');
            updateStreamingStatus('complete', '已完成');
        }
        
    } catch (error) {
        console.error('流式生成失败:', error);
        addStreamingLog('error', `生成失败: ${error.message}`);
        updateStreamingStatus('error', '失败');
        showMessage('生成失败: ' + error.message, 'error');
    } finally {
        StreamingState.isStreaming = false;
        document.getElementById('btn-copy-output').disabled = false;
    }
}

/**
 * 处理SSE事件
 */
function handleSSEEvent(eventType, data) {
    console.log('SSE事件:', eventType, data);
    
    switch (eventType) {
        case 'start':
        case 'message':  // 默认事件类型
            if (data.message) {
                addStreamingLog('progress', data.message);
                updateStreamingStatus('streaming', data.message);
            }
            break;
            
        case 'progress':
            if (data.message) {
                addStreamingLog('progress', data.message);
            }
            break;
            
        case 'token':
            // 实时显示LLM输出
            if (data.content) {
                StreamingState.fullContent += data.content;
                
                // 如果还没有token日志元素，创建一个
                if (!StreamingState.currentTokenLog) {
                    const now = new Date();
                    const timeStr = now.toLocaleTimeString('zh-CN');
                    
                    const body = document.getElementById('streaming-panel-body');
                    if (body) {
                        const logDiv = document.createElement('div');
                        logDiv.className = 'streaming-log token';
                        logDiv.innerHTML = `
                            <div class="streaming-log-time">${timeStr}</div>
                            <div class="streaming-log-content"></div>
                        `;
                        body.appendChild(logDiv);
                        body.scrollTop = body.scrollHeight;
                        
                        StreamingState.currentTokenLog = logDiv;
                        StreamingState.logs.push({ 
                            type: 'token', 
                            content: '', 
                            time: timeStr,
                            element: logDiv 
                        });
                    }
                }
                
                // 更新现有日志元素的内容（而不是创建新元素）
                if (StreamingState.currentTokenLog) {
                    const contentEl = StreamingState.currentTokenLog.querySelector('.streaming-log-content');
                    if (contentEl) {
                        contentEl.textContent = StreamingState.fullContent;
                        
                        // 自动滚动
                        const body = document.getElementById('streaming-panel-body');
                        if (body) {
                            body.scrollTop = body.scrollHeight;
                        }
                    }
                }
            }
            break;
            
        case 'complete':
            if (data.message) {
                addStreamingLog('complete', data.message);
            }
            updateStreamingStatus('complete', '已完成');
            
            // 重置currentTokenLog，下次生成时创建新的
            StreamingState.currentTokenLog = null;
            
            // 显示完整结果
            if (data.data) {
                addStreamingLog('token', JSON.stringify(data.data, null, 2));
                // 自动显示生成的内容
                if (data.data.world_name || data.data.world_description) {
                    displayBible(data.data);
                }
            }
            document.getElementById('btn-copy-output').disabled = false;
            break;
            
        case 'error':
            if (data.error) {
                addStreamingLog('error', data.error);
            }
            updateStreamingStatus('error', '错误');
            break;
            
        case 'done':
            if (data.message) {
                addStreamingLog('complete', data.message);
            }
            break;
    }
}

// ============================================
// UI 操作
// ============================================

/**
 * 显示流式面板
 */
function showStreamingPanel(title) {
    const panel = document.getElementById('streaming-panel');
    const titleEl = document.getElementById('streaming-panel-title');
    
    if (panel && titleEl) {
        panel.classList.add('active');
        titleEl.textContent = title;
        
        // 清空之前的内容
        const body = document.getElementById('streaming-panel-body');
        if (body) body.innerHTML = '';
        
        // 重置状态
        updateStreamingStatus('streaming', '生成中...');
        document.getElementById('btn-copy-output').disabled = true;
    }
}

/**
 * 关闭流式面板
 */
function closeStreamingPanel() {
    const panel = document.getElementById('streaming-panel');
    if (panel) {
        panel.classList.remove('active');
    }
}

/**
 * 更新流式状态
 */
function updateStreamingStatus(status, text) {
    const dot = document.getElementById('streaming-status-dot');
    const textEl = document.getElementById('streaming-status-text');
    
    // 更新状态
    StreamingState.status = status;
    
    if (dot && textEl) {
        dot.className = 'streaming-status-dot';
        if (status === 'error') dot.classList.add('error');
        if (status === 'complete') dot.classList.add('complete');
        
        textEl.textContent = text;
    }
}

/**
 * 添加流式日志
 */
function addStreamingLog(type, content) {
    const body = document.getElementById('streaming-panel-body');
    if (!body) return;
    
    const now = new Date();
    const timeStr = now.toLocaleTimeString('zh-CN');
    
    const logDiv = document.createElement('div');
    logDiv.className = `streaming-log ${type}`;
    
    logDiv.innerHTML = `
        <div class="streaming-log-time">${timeStr}</div>
        <div class="streaming-log-content">${escapeHtml(content)}</div>
    `;
    
    body.appendChild(logDiv);
    
    // 自动滚动到底部
    body.scrollTop = body.scrollHeight;
    
    // 保存日志
    StreamingState.logs.push({ type, content, time: timeStr, element: logDiv });
    
    return logDiv;
}

/**
 * 复制输出内容
 */
function copyStreamingOutput() {
    if (!StreamingState.fullContent) {
        showMessage('没有可复制的内容', 'warning');
        return;
    }
    
    navigator.clipboard.writeText(StreamingState.fullContent).then(() => {
        showMessage('已复制到剪贴板', 'success');
    }).catch(err => {
        console.error('复制失败:', err);
        showMessage('复制失败', 'error');
    });
}

// ============================================
// 工具函数
// ============================================

/**
 * 获取类型中文名
 */
function getTypeName(type) {
    const names = {
        'bible': 'Bible',
        'characters': '人物设定',
        'outline': '大纲'
    };
    return names[type] || type;
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
 * 重置流式状态
 */
function resetStreamingState() {
    if (StreamingState.eventSource) {
        StreamingState.eventSource.close();
        StreamingState.eventSource = null;
    }
    StreamingState.isStreaming = false;
    StreamingState.fullContent = '';
    StreamingState.logs = [];
    StreamingState.currentTokenLog = null;
    StreamingState.startTime = null;
}

