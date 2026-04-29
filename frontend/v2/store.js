/**
 * store.js — MANS v2 轻量级全局状态管理
 *
 * 职责:
 *   - 集中缓存全局状态（当前项目、运行状态、KB 概览）
 *   - 提供 subscribe / set / get API，减少重复 API 调用
 *   - app.js 写入状态，组件可选择订阅或直接从 store 读取
 *
 * 用法:
 *   store.setProject(pid, project);           // 切换项目
 *   await store.refresh();                     // 刷新 status + overview
 *   store.subscribe('status', (s) => { ... }); // 订阅变化
 */

class MANSStore {
    constructor() {
        this._projectId = null;
        this._status = null;
        this._overview = null;
        this._listeners = new Map();
        this._client = typeof MANSApiClient !== "undefined" ? new MANSApiClient() : null;
    }

    /** 当前项目 ID */
    get projectId() {
        return this._projectId;
    }

    /** 当前项目状态 */
    get status() {
        return this._status;
    }

    /** 当前 KB 概览 */
    get overview() {
        return this._overview;
    }

    /** 设置当前项目（切项目时调用） */
    setProject(projectId, project) {
        const prevId = this._projectId;
        this._projectId = projectId;
        this._status = project ? { ...project } : null;
        this._overview = null;
        if (prevId !== projectId) {
            this._emit("projectId", projectId, prevId);
        }
    }

    /** 更新状态（合并） */
    setStatus(status) {
        const prev = this._status;
        this._status = { ...(prev || {}), ...status };
        this._emit("status", this._status, prev);
    }

    /** 更新概览 */
    setOverview(overview) {
        this._overview = overview;
        this._emit("overview", overview);
    }

    /**
     * 刷新项目状态 — 同时拉取 status + overview，避免重复调用
     * @param {string} [projectId] 项目 ID，默认使用当前项目
     * @returns {Promise<{status, overview}|null>}
     */
    async refresh(projectId = this._projectId) {
        if (!projectId || !this._client) return null;
        try {
            const [status, overview] = await Promise.all([
                this._client.getStatus(projectId),
                this._client.getOverview(projectId),
            ]);
            this._status = status;
            this._overview = overview;
            const payload = { status, overview };
            this._emit("refresh", payload);
            return payload;
        } catch (err) {
            console.error("[store] refresh failed:", err);
            return null;
        }
    }

    /**
     * 订阅状态变化
     * @param {string} key — 'projectId' | 'status' | 'overview' | 'refresh'
     * @param {Function} fn — (newVal, prevVal, key) => void
     * @returns {Function} unsubscribe
     */
    subscribe(key, fn) {
        if (!this._listeners.has(key)) this._listeners.set(key, new Set());
        this._listeners.get(key).add(fn);
        return () => this._listeners.get(key)?.delete(fn);
    }

    /** 订阅多个键，任一变化时触发回调 */
    subscribeMany(keys, fn) {
        const unsubs = keys.map((k) => this.subscribe(k, (v, p, key) => fn(v, p, key)));
        return () => unsubs.forEach((u) => u());
    }

    _emit(key, value, prev) {
        const cbs = this._listeners.get(key);
        if (!cbs) return;
        cbs.forEach((fn) => {
            try {
                fn(value, prev, key);
            } catch (e) {
                console.error("[store] listener error:", e);
            }
        });
    }
}

window.mansStore = window.mansStore || new MANSStore();
