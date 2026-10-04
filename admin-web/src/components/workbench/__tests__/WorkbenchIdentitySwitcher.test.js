// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'

// 票 04：顶栏身份切换器 —— 票面那组用例就是这张表。
// 四类会话组合（两套都在 / 只有管理端 / 只有员工 / 都没有）× 记忆值，只断言**渲染与
// 切换结果**：哪一档亮着、哪一档点得动、点下去变成谁、有没有那句降级提示。
// 会话靠真实的两个探针（`/api/auth/status`、`/api/hygiene/staff/me`）读出来，与守卫
// 同一套口径；身份判定本身在 `utils/__tests__/workbenchIdentity.test.js` 里逐行压过，
// 这里压的是它**渲染出来**的样子。

const IDENTITY_KEY = 'luyun.login.workbench.identity'

/** 内存版 localStorage（照 `loginPrefs.test.js` 的写法），用例之间互不串味。 */
function memoryStorage() {
  const map = new Map()
  return {
    getItem: (key) => (map.has(key) ? map.get(key) : null),
    setItem: (key, value) => map.set(key, String(value)),
    removeItem: (key) => map.delete(key),
    clear: () => map.clear(),
    key: (i) => [...map.keys()][i] ?? null,
    get length() { return map.size },
    dump: () => Object.fromEntries(map),
  }
}

function jsonResponse(data, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => data,
  }
}

const SESSIONS = {
  /** 店里那台共用电脑：两套会话都在。 */
  both: { admin: true, staff: true },
  /** 只有管理端会话。 */
  admin: { admin: true, staff: false },
  /** 员工自己的手机：只有员工会话。 */
  staff: { admin: false, staff: true },
  /** 两套都没有（会话就在这一页上过期了）。 */
  none: { admin: false, staff: false },
}

/** 会话探针的假后端：与 `forbidden.test.js` 同一套 URL 口径。 */
function fetchFor(session, { name = '张三' } = {}) {
  return vi.fn(async (url) => {
    const path = String(url)
    if (path.includes('/api/auth/status')) {
      return jsonResponse({ logged_in: session.admin, initialized: true })
    }
    if (path.includes('/api/hygiene/staff/me')) {
      return session.staff
        ? jsonResponse({ employee: { name } })
        : jsonResponse({ detail: '需要员工登录' }, 401)
    }
    return jsonResponse({}, 404)
  })
}

const ROUTES = [
  '/workbench',
  '/workbench/me/today',
  '/workbench/daily',
]

/**
 * 挂一次切换器。每个用例都换一份崭新的模块图：`authStatus` 有 10 秒状态缓存，
 * 不清模块的话上一个用例的会话结论会漏到下一个。
 *
 * `saved` 就是「这台设备记住了哪一档」—— 探针之前先写进 localStorage，与真实的
 * 「上次选过、这次重新打开」同一个顺序。
 */
async function mountSwitcher({ session, saved = null, staffName = '张三' } = {}) {
  vi.resetModules()
  vi.stubGlobal('fetch', fetchFor(session, { name: staffName }))
  if (saved) localStorage.setItem(IDENTITY_KEY, saved)
  const [{ default: Switcher }, { useWorkbenchIdentityStore }] = await Promise.all([
    import('../WorkbenchIdentitySwitcher.vue'),
    import('../../../stores/workbenchIdentity'),
  ])

  // 每个用例一份崭新的 pinia：store 是单例，跨用例复用会把上一轮的会话结论带进来。
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useWorkbenchIdentityStore()

  const router = createRouter({
    history: createMemoryHistory(),
    routes: ROUTES.map((path) => ({ path, component: { template: '<div />' } })),
  })
  await router.push('/workbench/me/today')
  await router.isReady()

  // 探针由组件自己开场（`onMounted` 里那次 refresh），测试不替它调 —— 挂上去之后
  // 显示成什么样，就是真实用法下的样子。
  const wrapper = mount(Switcher, { global: { plugins: [router, pinia] } })
  await flushPromises()
  await flushPromises()
  return { wrapper, store, router }
}

function option(wrapper, text) {
  const found = wrapper.findAll('.wb-id-opt').find((node) => node.text().includes(text))
  if (!found) throw new Error(`切换器上没有「${text}」这一档`)
  return found
}

function litOption(wrapper) {
  const lit = wrapper.findAll('.wb-id-opt').filter((node) => node.classes().includes('is-on'))
  expect(lit).toHaveLength(1)
  return lit[0]
}

beforeEach(() => {
  vi.stubGlobal('localStorage', memoryStorage())
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('工作台身份切换器', () => {
  it('两套会话都在 + 没选过：自动定超级管理员，两档都点得动', async () => {
    const { wrapper } = await mountSwitcher({ session: SESSIONS.both })

    expect(litOption(wrapper).text()).toContain('超级管理员')
    expect(option(wrapper, '超级管理员').attributes('disabled')).toBeUndefined()
    expect(option(wrapper, '员工').attributes('disabled')).toBeUndefined()
    // 两档各一个 aria-pressed：亮着的那个是 true，另一个 false。
    expect(option(wrapper, '超级管理员').attributes('aria-pressed')).toBe('true')
    expect(option(wrapper, '员工').attributes('aria-pressed')).toBe('false')
  })

  it('只有管理端会话：员工那一档点不动（哪怕记着员工）', async () => {
    const { wrapper } = await mountSwitcher({ session: SESSIONS.admin, saved: 'super' })

    expect(litOption(wrapper).text()).toContain('超级管理员')
    expect(option(wrapper, '员工').attributes('disabled')).toBeDefined()
  })

  it('只有员工会话：显示「员工（我的姓名）」，超级管理员那一档点不动', async () => {
    const { wrapper } = await mountSwitcher({ session: SESSIONS.staff, staffName: '李四' })

    expect(litOption(wrapper).text()).toContain('员工（李四）')
    expect(option(wrapper, '超级管理员').attributes('disabled')).toBeDefined()
  })

  it('两套都没有：不冒充身份，两档都灰着、也没有「当前档」', async () => {
    const { wrapper } = await mountSwitcher({ session: SESSIONS.none, saved: 'super' })

    expect(wrapper.findAll('.wb-id-opt').filter((n) => n.classes().includes('is-on'))).toHaveLength(0)
    expect(option(wrapper, '超级管理员').attributes('disabled')).toBeDefined()
    expect(option(wrapper, '员工').attributes('disabled')).toBeDefined()
  })

  it('只降不升：记着超级管理员而它失效、员工会话还在 → 降为员工并提示一次', async () => {
    const { wrapper } = await mountSwitcher({ session: SESSIONS.staff, saved: 'super', staffName: '李四' })

    expect(litOption(wrapper).text()).toContain('员工（李四）')
    // 看得见、一句话、不阻塞（role=status，不是 modal / alert 拦截）。
    const notice = wrapper.get('[role="status"]')
    expect(notice.text()).toContain('管理端的登录已过期')
    expect(notice.text()).toContain('员工')
    // 降级落到本机记忆里：店长会话哪天自己回来也不会把这一档顶回去。
    expect(localStorage.getItem(IDENTITY_KEY)).toBe('staff')
  })

  it('降级提示不常驻：关掉就没了，重新打开也不再提', async () => {
    const { wrapper } = await mountSwitcher({ session: SESSIONS.staff, saved: 'super', staffName: '李四' })

    await wrapper.get('.wb-id-dismiss').trigger('click')
    await flushPromises()
    expect(wrapper.find('[role="status"]').exists()).toBe(false)

    // 本机记忆已经变成员工，所以再打开一次（新的模块图、同一个 localStorage）时
    // 不构成第二次降级 —— 提示不会再冒出来。
    const reopened = await mountSwitcher({ session: SESSIONS.staff, staffName: '李四' })
    expect(litOption(reopened.wrapper).text()).toContain('员工（李四）')
    expect(reopened.wrapper.find('[role="status"]').exists()).toBe(false)
  })

  it('只降不升：记着员工而店长会话也在 → 仍显示员工，绝不自动升（且两档都还点得动）', async () => {
    const { wrapper, store } = await mountSwitcher({ session: SESSIONS.both, saved: 'staff', staffName: '李四' })

    expect(litOption(wrapper).text()).toContain('员工（李四）')
    expect(store.identity).toBe('staff')
    // 员工可以自己点回超级管理员（手动不算自动升）；记忆值也没被系统改写。
    expect(option(wrapper, '超级管理员').attributes('disabled')).toBeUndefined()
    expect(localStorage.getItem(IDENTITY_KEY)).toBe('staff')
  })

  it('点另一档：当场换过去、记在本机；反向也点得动', async () => {
    const { wrapper, store } = await mountSwitcher({ session: SESSIONS.both })

    await option(wrapper, '员工').trigger('click')
    await flushPromises()
    expect(store.identity).toBe('staff')
    expect(localStorage.getItem(IDENTITY_KEY)).toBe('staff')

    await option(wrapper, '超级管理员').trigger('click')
    await flushPromises()
    expect(store.identity).toBe('super')
    expect(localStorage.getItem(IDENTITY_KEY)).toBe('super')
  })

  it('记在本机：换一次之后重新打开（同一台设备、同一个 localStorage）还是那一档', async () => {
    const first = await mountSwitcher({ session: SESSIONS.both })
    await option(first.wrapper, '员工').trigger('click')
    await flushPromises()

    // 「重新打开」：换一份崭新的模块图，但本机记忆还在（localStorage 没换）。
    const second = await mountSwitcher({ session: SESSIONS.both })
    expect(litOption(second.wrapper).text()).toContain('员工')
  })

  it('拿不到姓名时不显示空括号，只写「员工」', async () => {
    const { wrapper } = await mountSwitcher({ session: SESSIONS.staff, staffName: '' })

    const staffOption = option(wrapper, '员工')
    expect(staffOption.text()).toBe('员工')
    expect(staffOption.text()).not.toContain('（')
  })
})
