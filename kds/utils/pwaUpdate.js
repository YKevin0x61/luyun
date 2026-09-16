const CONTROLLER_CHANGE_TIMEOUT_MS = 3000

export function isSecurePwaContext(
  locationRef = globalThis.location,
  serviceWorker = globalThis.navigator?.serviceWorker,
) {
  if (!locationRef || !serviceWorker) return false
  if (locationRef.protocol === 'https:') return true
  return ['localhost', '127.0.0.1', '[::1]', '::1'].includes(locationRef.hostname)
}

export function createPwaUpdateController({
  serviceWorker = globalThis.navigator?.serviceWorker,
  locationRef = globalThis.location,
  swUrl = '/kds/sw.js',
  scope = '/kds/',
  confirmApply = async () => true,
  reload = () => globalThis.location?.reload(),
  controllerChangeTimeoutMs = CONTROLLER_CHANGE_TIMEOUT_MS,
  setTimer = setTimeout,
  clearTimer = clearTimeout,
} = {}) {
  const listeners = new Set()
  const state = {
    visible: false,
    applying: false,
    error: '',
  }
  let initialized = false
  let registration = null
  let refreshAvailable = false
  let dismissed = false

  function snapshot() {
    return { ...state }
  }

  function publish() {
    state.visible = refreshAvailable && !dismissed
    const current = snapshot()
    for (const listener of listeners) {
      try {
        listener(current)
      } catch (error) {
        console.error('[PWA] 更新状态订阅失败:', error)
      }
    }
  }

  function markRefreshAvailable() {
    refreshAvailable = true
    publish()
  }

  function observeRegistration(nextRegistration) {
    if (!nextRegistration) return
    registration = nextRegistration

    if (registration.waiting && serviceWorker?.controller) {
      markRefreshAvailable()
    }

    registration.addEventListener?.('updatefound', () => {
      const installing = registration.installing
      if (!installing) return
      installing.addEventListener('statechange', () => {
        if (installing.state === 'installed' && serviceWorker?.controller) {
          markRefreshAvailable()
        }
      })
    })
  }

  async function initialize() {
    if (initialized) return isSecurePwaContext(locationRef, serviceWorker)
    initialized = true
    if (!isSecurePwaContext(locationRef, serviceWorker)) return false

    try {
      const nextRegistration = await serviceWorker.register(swUrl, {
        scope,
        updateViaCache: 'none',
      })
      observeRegistration(nextRegistration)
      if (nextRegistration.update) {
        await nextRegistration.update().catch(() => {})
      }
      return true
    } catch (error) {
      state.error = '检查更新失败'
      publish()
      console.warn('[PWA] Service Worker 注册失败:', error)
      return false
    }
  }

  function waitForControllerChange(waitingWorker) {
    return new Promise((resolve) => {
      let settled = false
      let timer = null

      function finish() {
        if (settled) return
        settled = true
        if (timer) clearTimer(timer)
        resolve()
      }

      timer = setTimer(finish, controllerChangeTimeoutMs)
      serviceWorker?.addEventListener?.('controllerchange', finish, { once: true })
      if (waitingWorker?.postMessage) {
        try {
          waitingWorker.postMessage({ type: 'SKIP_WAITING' })
        } catch (error) {
          if (timer) clearTimer(timer)
          throw error
        }
      } else {
        finish()
      }
    })
  }

  async function applyUpdate() {
    if (!state.visible || state.applying) return false
    if (!(await confirmApply())) return false

    state.applying = true
    state.error = ''
    publish()

    try {
      await waitForControllerChange(registration?.waiting)
      reload()
      state.applying = false
      publish()
      return true
    } catch (error) {
      state.applying = false
      state.error = error?.message || '更新失败，请稍后重试'
      publish()
      return false
    }
  }

  function dismiss() {
    dismissed = true
    publish()
  }

  function subscribe(listener) {
    listeners.add(listener)
    listener(snapshot())
    return () => listeners.delete(listener)
  }

  return {
    initialize,
    applyUpdate,
    dismiss,
    subscribe,
    snapshot,
  }
}
