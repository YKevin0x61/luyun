import { computed, onBeforeUnmount, ref, watch } from 'vue'

const PING_INTERVAL_MS = 30000

/**
 * 连接 /ws/realtime，支持 subscribe/unsubscribe/ping 协议。
 * 断线自动退避重连，重连后重发全部已知订阅。
 *
 * `identity`：这条连接**显式声明**的身份（见 `api/security.py` 的 `identify_ws`）。
 * 同一浏览器可以同时持管理端与员工端两套 cookie，服务端默认「管理端优先」，员工端
 * 页面不声明就会被判成管理端身份（主题白名单全开、员工之间的归属隔离失效）。
 * 员工端页面传 `'staff'`，管理端页面不传（默认优先级本来就对）。
 * 声明只是「用哪份凭据验」，验不过服务端直接拒连，不回落到另一份。
 * 声明写在连接 URL 上、只在建连那一刻生效，所以声明变了要重连一次。
 */
export function useRealtime(onEvent, { enabled = true, identity = null } = {}) {
  const connected = ref(false)
  const latencyMs = ref(null)
  const enabledState = computed(() => {
    if (typeof enabled === 'function') return Boolean(enabled())
    if (enabled && typeof enabled === 'object' && 'value' in enabled) {
      return Boolean(enabled.value)
    }
    return Boolean(enabled)
  })
  const declaredIdentity = computed(() => {
    const value = typeof identity === 'function'
      ? identity()
      : (identity && typeof identity === 'object' && 'value' in identity ? identity.value : identity)
    return value || null
  })
  let ws = null
  let reconnectTimer = null
  let pingTimer = null
  let pingSentAt = null
  let backoffMs = 1000
  let stopped = false
  const pendingSubscriptions = new Map()

  function send(payload) {
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(payload))
    }
  }

  function subscribe(id, topics, filters = {}) {
    pendingSubscriptions.set(id, { topics, filters })
    send({ action: 'subscribe', id, topics, filters })
  }

  function unsubscribe(id) {
    pendingSubscriptions.delete(id)
    send({ action: 'unsubscribe', id })
  }

  function resubscribeAll() {
    for (const [id, { topics, filters }] of pendingSubscriptions) {
      send({ action: 'subscribe', id, topics, filters })
    }
  }

  function sendPing() {
    pingSentAt = performance.now()
    send({ action: 'ping' })
  }

  function startPing() {
    clearInterval(pingTimer)
    sendPing()
    pingTimer = setInterval(sendPing, PING_INTERVAL_MS)
  }

  function stopPing() {
    clearInterval(pingTimer)
    pingTimer = null
    pingSentAt = null
  }

  function connect() {
    if (stopped || !enabledState.value) return
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const declared = declaredIdentity.value
    const declaration = declared ? `?identity=${encodeURIComponent(declared)}` : ''
    ws = new WebSocket(`${proto}//${window.location.host}/ws/realtime${declaration}`)

    ws.onopen = () => {
      connected.value = true
      backoffMs = 1000
      resubscribeAll()
      startPing()
    }
    ws.onclose = () => {
      connected.value = false
      latencyMs.value = null
      stopPing()
      if (!stopped) scheduleReconnect()
    }
    ws.onerror = () => {
      try { ws.close() } catch (e) { /* noop */ }
    }
    ws.onmessage = (evt) => {
      try {
        const data = JSON.parse(evt.data)
        if (data.type === 'pong') {
          if (pingSentAt != null) {
            latencyMs.value = Math.round(performance.now() - pingSentAt)
            pingSentAt = null
          }
          return
        }
        if (data.type && data.type !== 'connected') {
          onEvent?.(data)
        }
      } catch (e) { /* ignore malformed frame */ }
    }
  }

  function scheduleReconnect() {
    if (!enabledState.value) return
    clearTimeout(reconnectTimer)
    reconnectTimer = setTimeout(connect, backoffMs)
    backoffMs = Math.min(backoffMs * 2, 30000)
  }

  function stopConnection() {
    stopped = true
    clearTimeout(reconnectTimer)
    stopPing()
    ws?.close()
    ws = null
    connected.value = false
    latencyMs.value = null
  }

  function startConnection() {
    stopped = false
    backoffMs = 1000
    connect()
  }

  /** 声明变了要换一条连接：声明写在 URL 上，旧连接不会自己改身份。 */
  function restartConnection() {
    clearTimeout(reconnectTimer)
    backoffMs = 1000
    const socket = ws
    ws = null
    if (socket) {
      socket.onclose = null // 本次关闭是主动换连，不走退避重连
      try { socket.close() } catch (e) { /* noop */ }
    }
    stopPing()
    connected.value = false
    latencyMs.value = null
    connect()
  }

  watch([enabledState, declaredIdentity], ([next]) => {
    if (!next) {
      stopConnection()
      return
    }
    if (stopped) startConnection()
    else restartConnection()
  }, { immediate: true })

  onBeforeUnmount(() => {
    stopConnection()
  })

  return { connected, latencyMs, subscribe, unsubscribe }
}
