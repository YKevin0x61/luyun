/**
 * 实时连接状态管理 Store（替代 polling.js 的角色）。
 *
 * 设计要点：
 * - 后端是 nudge 模型（只发 {type:'nudge', topic, scope}，不带数据/seq/delta），
 *   本 store 不做任何 seq 跟踪或增量合并，只负责：连接生命周期、订阅登记、
 *   把收到的 nudge 按 topic 分发给各页面自行注册的处理函数。
 * - 各页面通过 `on(topic, handler)` 注册自己的刷新逻辑（例如厨房页收到
 *   'orders' nudge 后重拉当天订单），页面卸载时只需调用返回的取消函数移除
 *   自己的处理函数，不影响 WS 连接本身（连接是全局单例，随 App 生命周期存在）。
 * - 60s 低频对账兜底复用同一套 'orders' 处理函数：定时器直接触发已注册的
 *   'orders' 处理函数（携带 reconcile 标记），不依赖连接是否正常——即使掉线，
 *   兜底定时器仍会尽力发起 HTTP 拉取。
 */

import { defineStore } from 'pinia'
import { RealtimeConnection } from '../utils/realtime.js'

// 展示态同步间隔：uni.connectSocket 的状态变化没有响应式事件，用轻量定时器
// 把 connection.status 同步到 Pinia state；这不是数据轮询，只是同步连接展示态，
// 开销可忽略。
const STATUS_SYNC_INTERVAL_MS = 1000

// 低频对账兜底间隔：即使 WS 连接正常，也定期兜底拉取一次，防止漏 nudge/漏单。
const RECONCILE_INTERVAL_MS = 60000

let connection = null
let statusSyncTimer = null
let reconcileTimer = null
// topic -> Set<handler>，模块级单例（与 connection 生命周期一致），页面各自注册/移除
// 自己的处理函数，不写入 Pinia state（避免 Set 被响应式代理包裹的多余开销）。
const handlersByTopic = new Map()

function dispatch(topic, scope) {
  const handlers = handlersByTopic.get(topic)
  if (!handlers || handlers.size === 0) return
  for (const handler of handlers) {
    try {
      handler(scope)
    } catch (error) {
      console.error(`[realtime] topic=${topic} 处理函数执行失败:`, error)
    }
  }
}

export const useRealtimeStore = defineStore('realtime', {
  state: () => ({
    connectionStatus: 'disconnected', // connected | disconnected | reconnecting
    lastUpdateTime: null, // 最近一次收到任意 WS 消息的时间
    lastNudgeAt: null // 最近一次收到 nudge 的时间
  }),

  getters: {
    connectionStatusText: (state) => {
      const statusMap = { connected: '已连接', disconnected: '已断开', reconnecting: '重连中' }
      return statusMap[state.connectionStatus] || '未知'
    },
    isOnline: (state) => state.connectionStatus === 'connected'
  },

  actions: {
    /**
     * 初始化连接 + 对账兜底定时器（幂等：多个页面调用只生效一次）
     */
    init() {
      if (connection) return
      connection = new RealtimeConnection()
      connection.onMessage((message) => this._handleMessage(message))
      connection.connect()
      this._startStatusSync()
      this._startReconcileLoop()
    },

    /**
     * 订阅一个 topic 集合。多个页面用不同 id 订阅相同 topic/filters 是安全的
     * （后端按连接去重推送，不会重复收到同一条 nudge）。
     */
    subscribe(id, topics, filters = {}) {
      connection?.subscribe(id, topics, filters)
    },

    unsubscribe(id) {
      connection?.unsubscribe(id)
    },

    /**
     * 注册某个 topic 的处理函数，返回取消注册函数。
     * @param {String} topic
     * @param {Function} handler (scope) => void
     * @returns {Function} 取消注册
     */
    on(topic, handler) {
      if (!handlersByTopic.has(topic)) {
        handlersByTopic.set(topic, new Set())
      }
      handlersByTopic.get(topic).add(handler)
      return () => handlersByTopic.get(topic)?.delete(handler)
    },

    _handleMessage(message) {
      this.lastUpdateTime = new Date()
      if (!message || message.type !== 'nudge') return
      this.lastNudgeAt = new Date()
      dispatch(message.topic, message.scope || {})
    },

    _startStatusSync() {
      if (statusSyncTimer) return
      statusSyncTimer = setInterval(() => {
        if (connection) this.connectionStatus = connection.status
      }, STATUS_SYNC_INTERVAL_MS)
    },

    _startReconcileLoop() {
      if (reconcileTimer) return
      reconcileTimer = setInterval(() => {
        dispatch('orders', { reconcile: true })
      }, RECONCILE_INTERVAL_MS)
    },

    /**
     * 整体关闭（当前无页面在应用生命周期内主动调用，随进程退出即释放；
     * 保留此方法用于测试/未来需要彻底释放连接的场景）。
     */
    cleanup() {
      connection?.close()
      connection = null
      if (statusSyncTimer) {
        clearInterval(statusSyncTimer)
        statusSyncTimer = null
      }
      if (reconcileTimer) {
        clearInterval(reconcileTimer)
        reconcileTimer = null
      }
      handlersByTopic.clear()
      this.connectionStatus = 'disconnected'
    }
  }
})

export default useRealtimeStore
