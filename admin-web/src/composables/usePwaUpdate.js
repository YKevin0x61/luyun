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
  // 「立即更新」会 reload 整个页面：正在填的表单、没提交的弹窗都会丢。先问一句，
  // 让用户可以点「稍后」把手上的活交掉再更新。默认走浏览器原生 confirm；测试可注入。
  confirmApply = (message) =>
    typeof globalThis.confirm === 'function' ? globalThis.confirm(message) : true,
  controllerChangeTimeoutMs = 3000,
  // 点「立即更新」时本次发布的新版本可能还在安装（预缓存几十个文件要几秒）。
  // 这时 registration.waiting 还是上一版（用户上次点「稍后」留下的），先激活
  // 它只会刷回同一个旧页面，新版本装完提示条又出现 —— 用户看到的就是「点了
  // 两次才更新」。等安装完成再发 SKIP_WAITING，一次点到位。
  installTimeoutMs = 12000,
  // `registration.update()` 在「已有安装进行中」时会等这次安装结束才 resolve
  // （Chrome 的行为），慢网下能挂几十秒。点击后不能一直卡在「更新中…」：
  // 最多等这么久，查不到就用手上现成的 worker 继续。
  updateTimeoutMs = 3000,
  setTimer = setTimeout,
  clearTimer = clearTimeout,
  // 开发环境（Vite dev server，:5173）不注册：那份 worker 只在构建时生成
  // （`vite.config.js` 两份都 `devOptions.enabled: false`），dev 下 `/sw.js` 与
  // `/workbench/sw.js` 都落到 SPA history fallback，回一份 index.html
  // （实测 200 + `Content-Type: text/html`）—— 浏览器按 MIME 直接拒注册，
  // 控制台每次启动刷一条 `SecurityError: unsupported MIME type ('text/html')`，
  // 而注册本来就不可能成功。要验 PWA 行为请用构建产物走后端 :8000。
  enabled = import.meta.env?.DEV !== true,
} = {}) {
  const needRefresh = ref(false)
  const applying = ref(false)
  const error = ref('')
  // 更新过程中给用户看的阶段文案：等在安装/切换时，按钮上的「更新中…」太笼统。
  const status = ref('')
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
    if (!supported || !enabled) return false
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
        await withTimeout(nextRegistration.update(), updateTimeoutMs)
      }
      return true
    } catch (registerError) {
      console.warn('[PWA] Service Worker 注册失败:', registerError)
      return false
    }
  }

  /** 给任意 Promise 加超时：到点 resolve(null)，不抛错、不阻塞调用方。 */
  function withTimeout(promise, timeoutMs) {
    return new Promise((resolve) => {
      let settled = false
      let timer = null
      function finish(value) {
        if (settled) return
        settled = true
        if (timer) clearTimer(timer)
        resolve(value)
      }
      timer = setTimer(() => finish(null), timeoutMs)
      Promise.resolve(promise).then(
        (value) => finish(value),
        () => finish(null),
      )
    })
  }

  function waitForWorkerInstalled(worker, timeoutMs) {
    if (!worker?.addEventListener) return Promise.resolve(worker)
    if (worker.state && worker.state !== 'installing') return Promise.resolve(worker)
    return new Promise((resolve) => {
      let settled = false
      let timer = null

      function finish(value) {
        if (settled) return
        settled = true
        if (timer) clearTimer(timer)
        worker.removeEventListener?.('statechange', onChange)
        resolve(value)
      }

      function onChange() {
        if (worker.state === 'installed' || worker.state === 'activated') finish(worker)
        else if (worker.state === 'redundant') finish(null)
      }

      timer = setTimer(() => finish(null), timeoutMs)
      worker.addEventListener('statechange', onChange)
    })
  }

  /** 等 controller 真正换过来。返回 true 才允许 reload —— 超时说明新 worker
   *  还没接管，这时 reload 只会刷回旧页面（旧 SW 的 navigateFallback 还端着
   *  旧 index.html），用户就得再点一次。 */
  function waitForControllerChange(waitingWorker) {
    return new Promise((resolve) => {
      let settled = false
      let timer = null
      let changed = false

      function finish() {
        if (settled) return
        settled = true
        if (timer) clearTimer(timer)
        serviceWorker?.removeEventListener?.('controllerchange', onChange)
        resolve(changed)
      }

      function onChange() {
        changed = true
        finish()
      }

      timer = setTimer(finish, controllerChangeTimeoutMs)
      serviceWorker?.addEventListener?.('controllerchange', onChange, { once: true })
      if (waitingWorker?.postMessage) {
        try {
          waitingWorker.postMessage({ type: 'SKIP_WAITING' })
        } catch (error) {
          if (timer) clearTimer(timer)
          serviceWorker?.removeEventListener?.('controllerchange', onChange)
          throw error
        }
      } else {
        finish()
      }
    })
  }

  async function apply() {
    if (!visible.value || applying.value) return false
    if (confirmApply) {
      const accepted = confirmApply(
        '更新会重新加载当前页面：已经填好但还没提交的内容会丢失。确定现在更新吗？',
      )
      if (accepted === false) return false
    }
    applying.value = true
    error.value = ''
    status.value = '正在检查新版本…'
    try {
      // 先主动查一次最新版本：提示条可能来自上一版 waiting，而本次发布的新
      // worker 还没被浏览器发现。查完再看 installing —— 它一定比 waiting 新。
      // update() 本身可能因为「安装进行中」而长时间不 resolve，最多等
      // updateTimeoutMs，超时就用手上现成的 worker 继续，别卡住按钮。
      await withTimeout(registration?.update?.(), updateTimeoutMs)
      let worker = registration?.installing || registration?.waiting || null
      if (!worker) {
        applying.value = false
        error.value = '新版本还没下载好，请稍后再试'
        return false
      }
      if (worker.state === 'installing') {
        status.value = '正在下载新版本…'
        const installed = await waitForWorkerInstalled(worker, installTimeoutMs)
        if (!installed) {
          applying.value = false
          error.value = '新版本正在下载，完成后会再提示你更新'
          return false
        }
        worker = installed
      }
      status.value = '正在切换页面…'
      const applied = await waitForControllerChange(worker)
      if (!applied) {
        applying.value = false
        error.value = '新版本已下载，但页面还没切换，请再点一次'
        return false
      }
      reload()
      applying.value = false
      return true
    } catch (updateError) {
      applying.value = false
      error.value = updateError?.message || '更新失败，请检查网络后重试'
      return false
    } finally {
      status.value = ''
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
    status,
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
