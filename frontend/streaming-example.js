/**
 * streaming-example.js
 *
 * SSE 流式输出集成示例与参考实现。
 *
 * 职责边界：
 *     - 演示两种流式集成方式：简单轮询模拟 vs 真正的 SSE 流式读取。
 *     - 提供进度面板 UI 的完整构建代码，可作为自定义面板的基础。
 *     - 包含事件处理和日志展示的示例实现。
 *
 * 重要说明：
 *     此文件为示例/参考代码，不直接参与应用运行。
 *     生产环境中的流式输出由 streaming-client.js 和 app.js 共同处理。
 *     保留此文件是为了方便开发者理解 SSE 集成原理，或进行自定义扩展。
 */

// ============================================
// 方式1: 最简单的集成 - 使用轮询模拟流式
// ============================================

/**
 * 模拟流式生成 Bible（非真实 SSE，仅展示进度提示）。
 *
 * 适用场景：
 *     后端暂不支持 SSE 时，通过普通 POST 请求 + 前端状态提示
 *     提供基本的用户反馈。
 *
 * 流程：
 *     1. 禁用按钮，显示"生成中..."状态。
 *     2. 调用普通 API 请求（非流式）。
 *     3. 请求完成后恢复按钮状态，展示结果。
 */
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
// 方式2: 使用真正的 SSE 流式（推荐）
// ============================================

/**
 * 通过真实 SSE 流式生成 Bible。
 *
 * 适用场景：
 *     后端支持 SSE 端点时，提供最佳的实时反馈体验。
 *
 * 流程：
 *     1. 创建或复用进度面板。
 *     2. 发起 fetch 请求到 /stream/{type} 端点。
 *     3. 使用 ReadableStream 逐块读取响应体。
 *     4. 解析 SSE 格式的事件行（event: / data:）。
 *     5. 将事件内容实时展示在进度面板中。
 *     6. 流结束后标记完成状态。
 */
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

            // 处理SSE事件：按行分割，保留不完整的行到 buffer
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

/**
 * 创建进度面板 DOM 元素。
 *
 * 面板特性：
 *     - 固定定位在屏幕右下角。
 *     - 包含标题栏（渐变色背景 + 关闭按钮）和日志容器。
 *     - 最大高度 400px，日志区域可滚动。
 *
 * Returns:
 *     构建好的进度面板 DOM 元素。
 */
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

/**
 * 向进度面板追加一条日志。
 *
 * 每条日志左侧带彩色竖条，根据 type 区分颜色：
 *     - error：红色
 *     - success：绿色
 *     - info（默认）：蓝紫色
 *
 * Args:
 *     container: 日志容器 DOM 元素。
 *     message: 日志内容。
 *     type: 日志类型，决定左侧边框颜色。
 */
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

/**
 * 处理流式事件数据，将内容展示到进度面板。
 *
 * 事件类型处理：
 *     - message：作为 info 类型日志展示。
 *     - content（token）：截取前 100 字符展示，避免过长内容撑爆面板。
 *     - error：作为 error 类型日志展示。
 *     - data（最终结果）：JSON 序列化后截取前 200 字符展示。
 *
 * Args:
 *     data: 解析后的 SSE 事件数据对象。
 *     logContainer: 日志容器 DOM 元素。
 */
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

// 在你的 app.js 中找到 generateBible 函数，替换为：
/*
async function generateBible() {
    // 选择一种方式：

    // 方式1: 简单轮询
    // await generateBibleWithProgress();

    // 方式2: 真正流式（推荐）
    await generateBibleStreaming();
}
*/
