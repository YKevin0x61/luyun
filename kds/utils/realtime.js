/**
 * WebSocket 实时连接管理（替代 polling.js）。
 * 用 uni.connectSocket，URL 带 ?token=（复用现有 ApiAuthManager token）。
 * 自动重连（3s 固定间隔，与后端设计文档一致）、30s 心跳 ping。
 *
 * 后端是 nudge 模型：只推 `{type:'nudge', topic, scope}`，不带数据、不带
 * seq/delta——收到 nudge 后由调用方自行复用现有 HTTP API 拉取最新数据。
 */

import { ApiAuthManager, ApiSettingsManager } from './storage.js'

const RECONNECT_DELAY_MS = 3000
const PING_INTERVAL_MS = 30000

export class RealtimeConnection {
  constructor() {
    this.socket = null
    this.status = 'disconnected' // connected | disconnected | reconnecting
    this.lastMessageAt = null
    this.closedByCaller = false
    this.pingTimer = null
    this.reconnectTimer = null
    this.pendingSubscriptions = new Map() // id -> { topics, filters }
    this.listeners = new Set()
  }

  onMessage(handler) {
    this.listeners.add(handler)
    return () => this.listeners.delete(handler)
  }

  connect() {
    this.closedByCaller = false
    const baseUrl = ApiSettingsManager.getBaseUrl()
    const wsUrl = baseUrl.replace(/^http/, 'ws') + '/ws/realtime?token=' + encodeURIComponent(ApiAuthManager.getToken())

    this.socket = uni.connectSocket({ url: wsUrl, complete: () => {} })

    this.socket.onOpen(() => {
      this.status = 'connected'
      this._startPing()
      // 重连成功后重发所有已知订阅
      for (const [id, { topics, filters }] of this.pendingSubscriptions) {
        this._send({ action: 'subscribe', id, topics, filters })
      }
    })

    this.socket.onMessage((res) => {
      this.lastMessageAt = new Date()
      let data
      try {
        data = JSON.parse(res.data)
      } catch (error) {
        console.error('[realtime] 消息解析失败:', error)
        return
      }
      for (const handler of this.listeners) handler(data)
    })

    this.socket.onClose(() => {
      this.status = 'disconnected'
      this._stopPing()
      if (!this.closedByCaller) this._scheduleReconnect()
    })

    this.socket.onError((error) => {
      console.error('[realtime] 连接错误:', error)
    })
  }

  subscribe(id, topics, filters = {}) {
    this.pendingSubscriptions.set(id, { topics, filters })
    if (this.status === 'connected') {
      this._send({ action: 'subscribe', id, topics, filters })
    }
  }

  unsubscribe(id) {
    this.pendingSubscriptions.delete(id)
    if (this.status === 'connected') {
      this._send({ action: 'unsubscribe', id })
    }
  }

  close() {
    this.closedByCaller = true
    this._stopPing()
    clearTimeout(this.reconnectTimer)
    if (this.socket) this.socket.close()
  }

  _send(payload) {
    if (this.socket) this.socket.send({ data: JSON.stringify(payload) })
  }

  _startPing() {
    this._stopPing()
    this.pingTimer = setInterval(() => this._send({ action: 'ping' }), PING_INTERVAL_MS)
  }

  _stopPing() {
    if (this.pingTimer) {
      clearInterval(this.pingTimer)
      this.pingTimer = null
    }
  }

  _scheduleReconnect() {
    this.status = 'reconnecting'
    clearTimeout(this.reconnectTimer)
    this.reconnectTimer = setTimeout(() => this.connect(), RECONNECT_DELAY_MS)
  }
}
