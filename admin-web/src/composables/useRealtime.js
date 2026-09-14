import { computed, onBeforeUnmount, ref, watch } from 'vue'

const PING_INTERVAL_MS = 30000

/**
 * 连接 /ws/realtime，支持 subscribe/unsubscribe/ping 协议。
 * 断线自动退避重连，重连后重发全部已知订阅。
 */
export function useRealtime(onEvent, { enabled = true } = {}) {
  const connected = ref(false)
  const latencyMs = ref(null)
  const enabledState = computed(() => {
    if (typeof enabled === 'function') return Boolean(enabled())
    if (enabled && typeof enabled === 'object' && 'value' in enabled) {
      return Boolean(enabled.value)
    }
    return Boolean(enabled)
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
    ws = new WebSocket(`${proto}//${window.location.host}/ws/realtime`)

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

  watch(enabledState, (next) => {
    if (next) startConnection()
    else stopConnection()
  }, { immediate: true })

  onBeforeUnmount(() => {
    stopConnection()
  })

  return { connected, latencyMs, subscribe, unsubscribe }
}
