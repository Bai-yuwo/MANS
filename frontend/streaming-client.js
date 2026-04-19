/**
 * streaming-client.js
 *
 * SSE（Server-Sent Events）流式输出客户端。
 *
 * 职责边界：
 *     - 管理前端与后端 SSE 端点的连接生命周期（建立、接收、关闭）。
 *     - 解析 SSE 事件流，将 progress / token / complete / error 等事件分发给 UI 层。
 *     - 维护流式输出的全局状态（连接状态、累积内容、日志历史）。
 *     - 提供流式面板的显示/隐藏/内容管理接口。
 *
 * 与 app.js 的关系：
 *     streaming-client.js 是 app.js 的下层依赖，专注于 SSE 连接和事件解析。
 *     app.js 负责业务逻辑（如调用 startStreamingGeneration、处理生成完成后的页面刷新）。
 *     两者通过全局函数和 AppState 对象进行协作。
 *
 * SSE 事件协议：
 *     后端发送的 SSE 消息格式遵循标准规范：
 *         event: {type}\n
 *         data: {json}\n\n
 *     其中 {type} 可取：start / progress / token / complete / error / done
 *     {json} 为事件携带的数据，通常是包含 message / content / data / error 字段的对象。
 *
 * 用法：
 *     // 启动 Bible 流式生成
 *     await startStreamingGeneration('bible', { temperature: 0.7 });
 *
 *     // 复制本次生成的完整内容到剪贴板
 *     copyStreamingOutput();
 */

// ============================================
// 全局状态
// ============================================

/**
 * 流式输出全局状态对象。
 *
 * 属性说明：
 *     eventSource: EventSource 实例（当前未使用，保留用于未来扩展）。
 *     isStreaming: 是否正在接收流式数据，用于防止重复发起生成请求。
 *     fullContent: 本次生成累积的完整文本内容，用于复制功能。
 *     logs: 流式日志条目数组，每项包含类型、内容、时间戳和 DOM 元素引用。
 *     startTime: 本次生成的起始时间戳，用于计算耗时（毫秒）。
 *     currentTokenLog: 当前正在追加 token 的日志 DOM 元素，用于合并连续 token。
 *     status: 当前流式状态机，取值 idle / streaming / complete / error。
 *     lastError: 最近一次错误消息，用于在连接关闭时判断是否需要抛出异常。
 */
const StreamingState = {
    eventSource: null,
    isStreaming: false,
    fullContent: '',
    logs: [],
    startTime: null,
    currentTokenLog: null,
    status: 'idle',
    lastError: ''
};

// ============================================
// 核心函数
// ============================================

/**
 * 启动流式生成会话。
 *
 * 执行流程：
 *     1. 检查前置条件（已选项目、非重复生成）。
 *     2. 显示流式面板并重置全局状态。
 *     3. 读取用户设置中的温度参数。
 *     4. 构建 SSE 请求 URL（支持自定义 API 基础地址）。
 *     5. 使用 fetch + ReadableStream 读取 SSE 流（而非 EventSource），
 *        因为部分端点需要 POST 方法和请求体。
 *     6. 手动解析 SSE 事件流（event: 和 data: 行）。
 *     7. 根据事件类型分发到 handleSSEEvent() 处理。
 *     8. 流结束后判断最终状态，更新 UI。
 *     9. 无论成功或失败，都在 finally 中释放生成锁。
 *
 * 容错处理：
 *     - HTTP 错误（非 2xx）：尝试解析错误响应体中的 detail 字段，提供更友好的错误消息。
 *     - 流中发生错误：状态标记为 error，错误日志追加到面板，最终向上抛出异常。
 *
 * Args:
 *     type: 生成类型标识，如 'bible'、'characters'、'outline'、'arc'。
 *     extraParams: 额外查询参数字典，会附加到 URL 中。
 *
 * Returns:
 *     Promise，生成完成时 resolve，失败时 reject。
 */
async function startStreamingGeneration(type = 'bible', extraParams = {}) {
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
    StreamingState.lastError = '';

    // 添加开始日志
    addStreamingLog('progress', `开始${getTypeName(type)}生成...`);

    try {
        // 读取温度设置
        const temperature = typeof getSetting === 'function' ? getSetting('temperature', 0.7) : 0.7;

        // 构建查询参数
        const queryParams = new URLSearchParams();
        queryParams.append('temperature', temperature);
        for (const [key, value] of Object.entries(extraParams)) {
            queryParams.append(key, value);
        }

        // 构建SSE连接URL（支持自定义apiBase）
        const base = (typeof getApiBase === 'function' ? getApiBase() : '').replace(/\/$/, '');
        const url = base
            ? `${base}/api/projects/${AppState.currentProject}/stream/${type}?${queryParams.toString()}`
            : `/api/projects/${AppState.currentProject}/stream/${type}?${queryParams.toString()}`;

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

            // 处理SSE事件：按行分割，保留最后一个不完整的行到 buffer
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';

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
        if (StreamingState.status === 'error') {
            throw new Error(StreamingState.lastError || '生成过程中出现错误');
        }
        addStreamingLog('complete', '生成完成！');
        updateStreamingStatus('complete', '已完成');

    } catch (error) {
        console.error('流式生成失败:', error);
        addStreamingLog('error', `生成失败: ${error.message}`);
        updateStreamingStatus('error', '失败');
        showMessage('生成失败: ' + error.message, 'error');
        throw error; // 继续向上抛出，让调用方知道失败
    } finally {
        StreamingState.isStreaming = false;
        document.getElementById('btn-copy-output').disabled = false;
    }
}

/**
 * 处理单个 SSE 事件。
 *
 * 根据 eventType 将事件分发到对应的处理逻辑：
 *     - start / message：显示进度消息。
 *     - progress：显示详细的生成进度。
 *     - token：累积 LLM 输出的 token，实时更新面板中的 token 日志元素。
 *     - complete：生成完成，更新状态，触发后续业务逻辑（如刷新弧线列表）。
 *     - error：记录错误信息，标记状态为 error。
 *     - done：流结束确认（通常与 complete 配合使用）。
 *
 * token 合并策略：
 *     为避免每个 token 都创建新的 DOM 元素导致性能问题，
 *     连续的 token 事件会复用同一个 currentTokenLog 元素，
 *     仅更新其 textContent。
 *
 * Args:
 *     eventType: SSE 事件类型字符串。
 *     data: 解析后的 JSON 数据对象。
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
                // 弧线生成完成后刷新弧线列表
                if (typeof checkArcStatus === 'function' && AppState.currentProject) {
                    checkArcStatus(AppState.currentProject);
                }
            }
            document.getElementById('btn-copy-output').disabled = false;
            break;

        case 'error':
            if (data.error) {
                addStreamingLog('error', data.error);
                StreamingState.lastError = data.error;
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
 * 显示流式输出面板。
 *
 * 操作内容：
 *     1. 为 #streaming-panel 添加 .active 类，触发 CSS 滑入动画。
 *     2. 更新面板标题。
 *     3. 清空面板主体中的历史内容。
 *     4. 重置状态为 streaming，禁用复制按钮。
 *
 * Args:
 *     title: 面板标题文本。
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
 * 关闭流式输出面板。
 *
 * 移除 #streaming-panel 的 .active 类，触发 CSS 滑出动画。
 */
function closeStreamingPanel() {
    const panel = document.getElementById('streaming-panel');
    if (panel) {
        panel.classList.remove('active');
    }
}

/**
 * 更新流式输出面板底部的状态指示器。
 *
 * 根据 status 值更新圆点颜色类（streaming 为绿色脉冲，error 为红色静态，complete 为蓝色静态）
 * 和状态文本描述。
 *
 * Args:
 *     status: 状态标识，取值 streaming / complete / error / idle。
 *     text: 状态描述文本，显示在圆点右侧。
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
 * 向流式面板追加一条日志条目。
 *
 * 每行日志包含时间戳（HH:MM:SS）和经过 HTML 转义的内容文本。
 * 追加后自动滚动面板到底部。
 *
 * Args:
 *     type: 日志类型，决定左边框颜色（progress / token / error / complete）。
 *     content: 日志内容文本。
 *
 * Returns:
 *     创建的日志 DOM 元素。
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
 * 将本次生成的完整内容复制到系统剪贴板。
 *
 * 使用 Clipboard API 的 writeText() 方法。
 * 若 API 不可用或用户拒绝权限，显示错误提示。
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
 * 将生成类型标识转换为中文显示名称。
 *
 * Args:
 *     type: 类型标识字符串，如 'bible'、'characters'。
 *
 * Returns:
 *     中文名称，未知类型返回原字符串。
 */
function getTypeName(type) {
    const names = {
        'bible': 'Bible',
        'characters': '人物设定',
        'outline': '大纲',
        'arc': '弧线规划'
    };
    return names[type] || type;
}

/**
 * 对文本进行 HTML 转义，防止 XSS 攻击。
 *
 * 将特殊字符（<、>、&、"、'）转换为对应的 HTML 实体。
 * 实现方式：创建一个临时 div 元素，利用浏览器的原生转义能力。
 *
 * Args:
 *     text: 原始文本字符串。
 *
 * Returns:
 *     转义后的安全 HTML 字符串。
 */
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/**
 * 重置流式输出全局状态。
 *
 * 关闭活跃的 EventSource 连接（若存在），
 * 清空所有累积数据和日志引用。
 * 通常在页面切换或需要中断生成时调用。
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
    StreamingState.lastError = '';
}

// 显式导出到 window，供 HTML 内联事件处理器调用
window.showStreamingPanel = showStreamingPanel;
window.closeStreamingPanel = closeStreamingPanel;
window.updateStreamingStatus = updateStreamingStatus;
window.addStreamingLog = addStreamingLog;
window.copyStreamingOutput = copyStreamingOutput;
window.getTypeName = getTypeName;
window.escapeHtml = escapeHtml;
window.resetStreamingState = resetStreamingState;
