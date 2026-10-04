import { computed, ref, watch } from 'vue'

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
  // 当前注册的是哪个 App 的 worker（票 09 起一个页面可能先后注册两份：从管理面
  // 客户端路由走进工作台时补注册工作台那份）。同一个不吃第二遍。
  let registeredUrl = null
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

  /** 注册并开始盯住**当前 App** 那份 worker。
   *
   *  `app` 就是 App 归属判据给出的那条清单条目（`utils/pwaManifest.js` 的
   *  `selectPwaManifest(...)`）：`serviceWorker` 是脚本地址、`serviceWorkerScope` 是
   *  scope。省略则回落到构造时的默认值（全站那份 `/sw.js` + scope `/`）。
   *
   *  换 App（管理面 ↔ 工作台）时注册新的那份、并把提示条改盯新的 registration ——
   *  否则「应用更新」会把 SKIP_WAITING 发到另一个 App 的 worker 上。
   */
  async function initialize(app = {}) {
    if (!supported) return false
    const nextUrl = app.serviceWorker || swUrl
    const nextScope = app.serviceWorkerScope || scope
    if (registeredUrl === nextUrl) return true

    try {
      const nextRegistration = await serviceWorker.register(nextUrl, {
        scope: nextScope,
        updateViaCache: 'none',
      })
      registeredUrl = nextUrl
      // 换了 App：上一份的等待状态与「已忽略」不跟着走。
      needRefresh.value = false
      dismissed.value = false
      error.value = ''
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

export function usePwaUpdate(appSource = null, options = {}) {
  const controller = createPwaUpdateController(options)
  // 盯的是 App 归属判据那条清单条目（引用稳定：同一档里路由怎么变都是同一个对象）。
  // 首次立刻注册；从管理面客户端路由走进工作台时补注册工作台那份。
  const resolveApp = () =>
    typeof appSource === 'function' ? appSource() : appSource?.value ?? appSource
  watch(
    resolveApp,
    (app) => {
      void controller.initialize(app || {})
    },
    { immediate: true },
  )
  return controller
}
