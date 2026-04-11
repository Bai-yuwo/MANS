/**
 * MANS 前端核心逻辑
 */

// 直接使用相对路径，因为页面由同一个 FastAPI 托管
const SSE_ENDPOINT = '/api/v1/stream';
const COMMAND_ENDPOINT = '/api/v1/command';

const EVENT_COLORS = {
    'system_info': { text: 'text-cyan-400', label: '[SYS]' },
    'agent_start': { text: 'text-blue-400', label: '[START]' },
    'prompt_built': { text: 'text-yellow-400', label: '[PROMPT]' },
    'llm_stream_token': { text: 'text-purple-400', label: '[TOKEN]' },
    'llm_end': { text: 'text-green-400', label: '[END]' },
    'error': { text: 'text-red-500', label: '[ERROR]' }
};

// DOM 元素
const ui = {
    projectName: document.getElementById('projectName'),
    chapterTitle: document.getElementById('chapterTitle'),
    plotInput: document.getElementById('plotInput'),
    sendBtn: document.getElementById('sendBtn'),
    novelOutput: document.getElementById('novelOutput'),
    systemLog: document.getElementById('systemLog'),
    tokenCount: document.getElementById('tokenCount'),
    eventCount: document.getElementById('eventCount'),
    connectionStatus: document.getElementById('connectionStatus')
};

let eventSource = null;
let counters = { tokens: 0, events: 0 };
let fullText = ""; // 缓存完整正文，处理打字机效果

function updateStatus(connected) {
    const dot = ui.connectionStatus.querySelector('span:first-child');
    const text = ui.connectionStatus.querySelector('span:last-child');
    if (connected) {
        dot.className = 'w-2 h-2 rounded-full bg-emerald-500 animate-pulse';
        text.textContent = 'SSE 已连接';
        text.className = 'text-emerald-400';
    } else {
        dot.className = 'w-2 h-2 rounded-full bg-red-500';
        text.textContent = '连接断开';
        text.className = 'text-red-500';
    }
}

function appendLog(type, payload) {
    counters.events++;
    ui.eventCount.textContent = counters.events;

    // 对于 token 事件，不再打印大段日志刷屏，仅更新正文
    if (type === 'llm_stream_token') {
        fullText += payload.token;
        ui.novelOutput.textContent = fullText;
        ui.novelOutput.scrollTop = ui.novelOutput.scrollHeight;

        counters.tokens++;
        ui.tokenCount.textContent = counters.tokens;
        return;
    }

    const colorCfg = EVENT_COLORS[type] || { text: 'text-gray-400', label: '[LOG]' };
    const logEl = document.createElement('div');
    logEl.className = 'mb-2 pb-2 border-b border-gray-800/50';

    const time = new Date().toLocaleTimeString('zh-CN', { hour12: false });
    let detailContent = typeof payload === 'object' ? JSON.stringify(payload, null, 2) : payload;

    logEl.innerHTML = `
        <div class="flex gap-2 mb-1">
            <span class="text-gray-600">[${time}]</span>
            <span class="${colorCfg.text} font-bold">${colorCfg.label}</span>
            <span class="${colorCfg.text}">${type.toUpperCase()}</span>
        </div>
        <pre class="text-gray-400 text-[11px] whitespace-pre-wrap overflow-x-hidden">${detailContent}</pre>
    `;

    ui.systemLog.appendChild(logEl);
    ui.systemLog.scrollTop = ui.systemLog.scrollHeight;
}

function connectSSE() {
    if (eventSource) eventSource.close();
    eventSource = new EventSource(SSE_ENDPOINT);

    eventSource.onopen = () => updateStatus(true);
    eventSource.onerror = () => {
        updateStatus(false);
        setTimeout(connectSSE, 3000); // 断线重连
    };

    eventSource.onmessage = (e) => {
        try {
            const data = JSON.parse(e.data);
            appendLog(data.event_type, data.payload);

            if (data.event_type === 'llm_end' || data.event_type === 'error') {
                ui.sendBtn.disabled = false;
                ui.sendBtn.textContent = '触发写作智能体 (WriterAgent)';
            }
        } catch (err) {
            console.error("Parse error:", err);
        }
    };
}

async function sendCommand() {
    if (!ui.projectName.value || !ui.plotInput.value) return alert("项目名称和剧情指示不能为空");

    ui.sendBtn.disabled = true;
    ui.sendBtn.textContent = '处理中...';

    // 初始化状态
    fullText = "";
    ui.novelOutput.textContent = "";
    counters.tokens = 0;
    ui.tokenCount.textContent = "0";

    const payload = {
        project_name: ui.projectName.value.trim(),
        chapter_title: ui.chapterTitle.value.trim() || '未命名章节',
        plot: ui.plotInput.value.trim()
    };

    try {
        await fetch(COMMAND_ENDPOINT, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ command: 'DRAFT_CHAPTER', payload })
        });
    } catch (err) {
        appendLog('error', err.message);
        ui.sendBtn.disabled = false;
        ui.sendBtn.textContent = '触发写作智能体 (WriterAgent)';
    }
}

// 启动
document.addEventListener('DOMContentLoaded', () => {
    ui.sendBtn.addEventListener('click', sendCommand);
    connectSSE();
});