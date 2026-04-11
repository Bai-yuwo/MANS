/**
 * MANS 前端应用脚本
 * 
 * 功能：
 * 1. 连接后端 SSE 流式接口
 * 2. 处理并展示各类系统事件
 * 3. 触发写作命令
 */

// ============================================================
// 配置常量
// ============================================================

const API_BASE_URL = 'http://127.0.0.1:8000';
const SSE_ENDPOINT = `${API_BASE_URL}/api/v1/stream`;
const COMMAND_ENDPOINT = `${API_BASE_URL}/api/v1/command`;

// 事件类型对应的显示颜色
const EVENT_COLORS = {
    'system_info': { text: 'text-cyan-400', label: 'ℹ️', bg: 'bg-cyan-900/30' },
    'agent_start': { text: 'text-blue-400', label: '🚀', bg: 'bg-blue-900/30' },
    'prompt_built': { text: 'text-yellow-400', label: '📝', bg: 'bg-yellow-900/30' },
    'llm_stream_token': { text: 'text-purple-400', label: '✨', bg: 'bg-purple-900/30' },
    'llm_end': { text: 'text-green-400', label: '✅', bg: 'bg-green-900/30' },
    'agent_end': { text: 'text-green-400', label: '🏁', bg: 'bg-green-900/30' },
    'error': { text: 'text-red-400', label: '❌', bg: 'bg-red-900/30' }
};

// ============================================================
// DOM 元素引用
// ============================================================

const plotInput = document.getElementById('plotInput');
const sendBtn = document.getElementById('sendBtn');
const novelOutput = document.getElementById('novelOutput');
const systemLog = document.getElementById('systemLog');
const connectionStatus = document.getElementById('connectionStatus');
const lastEventType = document.getElementById('lastEventType');
const tokenCount = document.getElementById('tokenCount');
const eventCount = document.getElementById('eventCount');

// ============================================================
// 状态变量
// ============================================================

let eventSource = null;
let tokenCounter = 0;
let eventCounter = 0;
let isReceiving = false;

// ============================================================
// 工具函数
// ============================================================

/**
 * 获取当前时间戳字符串
 */
function getTimestamp() {
    const now = new Date();
    return now.toLocaleTimeString('zh-CN', { 
        hour12: false, 
        hour: '2-digit', 
        minute: '2-digit', 
        second: '2-digit',
        fractionalSecondDigits: 3
    }) + 'ms';
}

/**
 * 转义 HTML 特殊字符，防止 XSS
 */
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/**
 * 更新连接状态显示
 */
function updateConnectionStatus(connected, message) {
    const dot = connectionStatus.querySelector('span:first-child');
    const text = connectionStatus.querySelector('span:last-child');
    
    if (connected) {
        dot.className = 'w-2 h-2 rounded-full bg-emerald-500 animate-pulse';
        text.textContent = 'SSE 已连接';
        text.className = 'text-emerald-400';
    } else {
        dot.className = 'w-2 h-2 rounded-full bg-red-500';
        text.textContent = message || 'SSE 未连接';
        text.className = 'text-red-400';
    }
}

/**
 * 添加系统日志条目
 */
function addLogEntry(eventType, payload) {
    const colors = EVENT_COLORS[eventType] || { text: 'text-gray-400', label: '📌', bg: 'bg-gray-900/30' };
    
    const entry = document.createElement('div');
    entry.className = `mb-2 p-2 rounded ${colors.bg} border border-gray-800`;
    
    const header = document.createElement('div');
    header.className = 'flex items-center gap-2 mb-1';
    
    const timestamp = document.createElement('span');
    timestamp.className = 'text-gray-500';
    timestamp.textContent = `[${getTimestamp()}]`;
    
    const label = document.createElement('span');
    label.className = colors.text;
    label.textContent = colors.label;
    
    const typeSpan = document.createElement('span');
    typeSpan.className = `${colors.text} font-bold`;
    typeSpan.textContent = eventType.toUpperCase();
    
    header.appendChild(timestamp);
    header.appendChild(label);
    header.appendChild(typeSpan);
    entry.appendChild(header);
    
    // 根据事件类型格式化 payload 显示
    if (eventType === 'llm_stream_token') {
        const preview = document.createElement('span');
        preview.className = 'text-gray-300 text-xs';
        preview.textContent = `"${escapeHtml(payload.token)}"`;
        entry.appendChild(preview);
    } else if (eventType === 'prompt_built' && payload.messages) {
        const msgDiv = document.createElement('div');
        msgDiv.className = 'text-xs text-gray-400 mt-1';
        payload.messages.forEach((msg) => {
            const role = msg.role === 'system' ? '【系统】' : '【用户】';
            const content = msg.content.length > 50 ? msg.content.substring(0, 50) + '...' : msg.content;
            msgDiv.textContent += `${role} ${content}\n`;
        });
        entry.appendChild(msgDiv);
    } else if (eventType === 'error') {
        const errorDiv = document.createElement('div');
        errorDiv.className = 'text-red-400 text-xs mt-1 font-bold';
        errorDiv.textContent = payload.error;
        entry.appendChild(errorDiv);
    } else {
        const payloadDiv = document.createElement('pre');
        payloadDiv.className = 'text-xs text-gray-400 mt-1 overflow-x-auto';
        payloadDiv.textContent = JSON.stringify(payload, null, 2);
        entry.appendChild(payloadDiv);
    }
    
    systemLog.appendChild(entry);
    systemLog.scrollTop = systemLog.scrollHeight;
    
    eventCounter++;
    eventCount.textContent = eventCounter;
    lastEventType.textContent = eventType;
}

/**
 * 追加小说内容
 */
function appendNovelContent(token) {
    if (!isReceiving) {
        novelOutput.innerHTML = '';
        isReceiving = true;
    }
    
    const span = document.createElement('span');
    span.className = 'text-gray-100';
    span.textContent = token;
    novelOutput.appendChild(span);
    novelOutput.scrollTop = novelOutput.scrollHeight;
    
    tokenCounter++;
    tokenCount.textContent = tokenCounter;
}

// ============================================================
// SSE 连接管理
// ============================================================

function connectSSE() {
    if (eventSource) {
        eventSource.close();
    }
    
    updateConnectionStatus(false, '正在连接...');
    
    try {
        eventSource = new EventSource(SSE_ENDPOINT);
        
        eventSource.onopen = () => {
            updateConnectionStatus(true);
            addLogEntry('system_info', { message: 'SSE 连接已建立' });
        };
        
        eventSource.onmessage = (e) => {
            try {
                const data = JSON.parse(e.data);
                const { event_type, payload } = data;
                
                if (event_type === 'llm_stream_token') {
                    appendNovelContent(payload.token);
                } else if (event_type === 'llm_end' || event_type === 'agent_end') {
                    isReceiving = false;
                }
                
                addLogEntry(event_type, payload);
                
            } catch (err) {
                console.error('解析 SSE 数据失败:', err);
            }
        };
        
        eventSource.onerror = (err) => {
            console.error('SSE 连接错误:', err);
            updateConnectionStatus(false, '连接断开');
            addLogEntry('error', { message: 'SSE 连接断开，将在 3 秒后重连...' });
            setTimeout(connectSSE, 3000);
        };
        
    } catch (err) {
        console.error('创建 SSE 连接失败:', err);
        updateConnectionStatus(false, '连接失败');
        addLogEntry('error', { message: `SSE 连接失败: ${err.message}` });
    }
}

// ============================================================
// 命令发送
// ============================================================

async function sendCommand() {
    const plot = plotInput.value.trim();
    
    if (!plot) {
        alert('请输入剧情提示！');
        return;
    }
    
    sendBtn.disabled = true;
    sendBtn.innerHTML = `
        <svg class="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
        </svg>
        处理中...
    `;
    
    tokenCounter = 0;
    eventCounter = 0;
    isReceiving = false;
    tokenCount.textContent = '0';
    eventCount.textContent = '0';
    novelOutput.innerHTML = '<p class="text-gray-500 italic">正在生成...</p>';
    
    try {
        const response = await fetch(COMMAND_ENDPOINT, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                command: 'DRAFT_CHAPTER',
                payload: { plot }
            })
        });
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        
        const result = await response.json();
        addLogEntry('system_info', { message: `命令已发送: ${result.message}` });
        
    } catch (err) {
        console.error('发送命令失败:', err);
        addLogEntry('error', { message: `命令发送失败: ${err.message}` });
        sendBtn.disabled = false;
        sendBtn.innerHTML = `
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/>
            </svg>
            发送命令 (DRAFT_CHAPTER)
        `;
    }
}

// ============================================================
// 初始化
// ============================================================

function init() {
    sendBtn.addEventListener('click', sendCommand);
    
    plotInput.addEventListener('keydown', (e) => {
        if (e.ctrlKey && e.key === 'Enter') {
            e.preventDefault();
            sendCommand();
        }
    });
    
    connectSSE();
    
    window.addEventListener('beforeunload', () => {
        if (eventSource) {
            eventSource.close();
        }
    });
    
    addLogEntry('system_info', { message: 'MANS 前端已初始化' });
}

document.addEventListener('DOMContentLoaded', init);
