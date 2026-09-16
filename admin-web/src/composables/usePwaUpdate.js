import { computed, ref } from 'vue'

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
  swUrl = '/sw.js',
  scope = '/',
  reload = () => globalThis.location?.reload(),
  controllerChangeTimeoutMs = 3000,
  setTimer = setTimeout,
  clearTimer = clearTimeout,
} = {}) {
  const needRefresh = ref(false)
  const applying = ref(false)
  const error = ref('')
  const dismissed = ref(false)
  const supported = isSecurePwaContext(locationRef, serviceWorker)
  let initialized = false
  let registration = null

  const visible = computed(
    () => supported && needRefresh.value && !dismissed.value,
  )

  function markRefreshReady() {
    needRefresh.value = true
  }

  function observeRegistration(nextRegistration) {
    if (!nextRegistration) return
    registration = nextRegistration

    if (registration.waiting && serviceWorker?.controller) {
      markRefreshReady()
    }

    registration.addEventListener?.('updatefound', () => {
      const installing = registration.installing
      if (!installing) return
      installing.addEventListener('statechange', () => {
        if (installing.state === 'installed' && serviceWorker?.controller) {
          markRefreshReady()
        }
      })
    })
  }

  async function initialize() {
    if (initialized) return supported
    initialized = true
    if (!supported) return false

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
    } catch (registerError) {
      console.warn('[PWA] Service Worker 注册失败:', registerError)
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

  async function apply() {
    if (!visible.value || applying.value) return false
    applying.value = true
    error.value = ''
    try {
      await waitForControllerChange(registration?.waiting)
      reload()
      applying.value = false
      return true
    } catch (updateError) {
      applying.value = false
      error.value = updateError?.message || '更新失败，请检查网络后重试'
      return false
    }
  }

  function dismiss() {
    dismissed.value = true
  }

  return {
    visible,
    needRefresh,
    applying,
    error,
    supported,
    initialize,
    apply,
    dismiss,
  }
}

export function usePwaUpdate() {
  const controller = createPwaUpdateController()
  void controller.initialize()
  return controller
}
