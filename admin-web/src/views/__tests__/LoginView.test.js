// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'
import LoginView from '../LoginView.vue'

// /login 面板的组件级测试：真实挂载页面，只断言「渲染出什么」与「它实际发出什么请求」。
// 票 03 起一个面板两个 Tab（管理员 / 员工）：
//  - 状态接口的返回决定当前栏落在哪一态（未初始化 → init 只属于管理员栏，已初始化未登录
//    → login，已登录 + ?switch=1 → loggedIn 确认面板）；
//  - 默认栏 = 记住值（`luyun.login.panel.tab`），`?next=` 落在员工端前缀内时强制员工栏；
//  - 管理员栏打 `/api/auth/*`（原生 fetch），员工栏打 `/api/hygiene/staff/*`（staffRequest）。
// 不看组件内部状态与私有函数名。

function jsonResponse(data, { ok = true, status = 200 } = {}) {
  return {
    ok,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => data,
  }
}

const STAFF_401 = () => jsonResponse({ detail: '需要员工登录' }, { ok: false, status: 401 })
const ADMIN_LOGGED_OUT = () => jsonResponse({ logged_in: false, initialized: true })
const ADMIN_UNINITIALIZED = () => jsonResponse({ logged_in: false, initialized: false })

const ROUTES = [
  { path: '/', component: { template: '<div />' } },
  { path: '/login', component: { template: '<div />' } },
  { path: '/admin', component: { template: '<div />' } },
  { path: '/workbench/me/today', component: { template: '<div />' } },
  { path: '/workbench/me/month', component: { template: '<div />' } },
  { path: '/workbench/me/clean', component: { template: '<div />' } },
  { path: '/workbench/hr/roster', component: { template: '<div />' } },
  // 票 07：配方阅读面（扫码的目标页）。
  { path: '/workbench/kitchen/recipe/detail', component: { template: '<div />' } },
  { path: '/workbench/kitchen/recipe', component: { template: '<div />' } },
  { path: '/register', component: { template: '<div />' } },
]

async function mountLogin(path = '/login', { from = null } = {}) {
  const router = createRouter({ history: createMemoryHistory(), routes: ROUTES })
  // `from`：先往历史里压一页「上一页」，才能验证登录落点的 history 语义
  // （memory history 的第一次导航会占掉初始那一格，只 push 登录页就没得退了）。
  if (from) await router.push(from)
  await router.push(path)
  await router.isReady()
  const pinia = createPinia()
  setActivePinia(pinia)
  const wrapper = mount(LoginView, { global: { plugins: [router, pinia] } })
  await flushPromises()
  return { wrapper, router }
}

function findCall(fetchMock, url) {
  return fetchMock.mock.calls.find(([calledUrl]) => calledUrl === url)
}

function tabTexts(wrapper) {
  return wrapper.findAll('[role="tab"]').map((tab) => tab.text())
}

function activeTab(wrapper) {
  return wrapper.get('[role="tab"][aria-selected="true"]').text()
}

/** 面板记住上次选的那一栏（与 utils/loginPrefs.js 同一族键名）。 */
function rememberTab(tab) {
  window.localStorage.setItem('luyun.login.panel.tab', tab)
}

async function clickTab(wrapper, label) {
  const tab = wrapper.findAll('[role="tab"]').find((item) => item.text() === label)
  await tab.trigger('click')
  await flushPromises()
}

async function submitWith(wrapper, values) {
  const form = wrapper.get('form')
  for (const [selector, value] of Object.entries(values)) {
    await form.get(selector).setValue(value)
  }
  await form.trigger('submit')
  await flushPromises()
}

let fetchMock
let manifestLink
let themeMeta

beforeEach(() => {
  window.localStorage.clear()
  // index.html 里那三处默认值在 jsdom 里不存在，按生产页面的样子补上：
  // 测试只通过 DOM 上这个 link / meta 观察「装出来是哪份清单」。
  manifestLink = document.createElement('link')
  manifestLink.id = 'app-manifest'
  manifestLink.rel = 'manifest'
  manifestLink.setAttribute('href', '/pwa/manifests/admin.webmanifest')
  document.head.appendChild(manifestLink)
  themeMeta = document.createElement('meta')
  themeMeta.setAttribute('name', 'theme-color')
  themeMeta.setAttribute('content', '#0a0d16')
  document.head.appendChild(themeMeta)

  fetchMock = vi.fn()
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  manifestLink.remove()
  themeMeta.remove()
  vi.unstubAllGlobals()
})

/** 面板此刻会被人「添加到主屏幕」装成哪份清单。 */
function manifestHref() {
  return document.getElementById('app-manifest').getAttribute('href')
}

function themeColor() {
  return document.querySelector('meta[name="theme-color"]').getAttribute('content')
}

describe('/login 面板：两个 Tab 与默认栏', () => {
  it('两个显式 Tab 都在；没记住过时默认员工栏（员工手机一打开就是员工栏）', async () => {
    fetchMock.mockResolvedValueOnce(STAFF_401())

    const { wrapper } = await mountLogin('/login')

    expect(tabTexts(wrapper)).toEqual(['管理员', '员工'])
    expect(activeTab(wrapper)).toBe('员工')
    const form = wrapper.get('form')
    expect(form.get('input[type="tel"]').exists()).toBe(true)
    expect(form.get('input[type="password"]').exists()).toBe(true)
    // 默认栏是员工栏：不该顺手去查管理端状态。
    expect(findCall(fetchMock, '/api/auth/status')).toBeFalsy()
  })

  it('记住上次选的栏：记住管理员栏时打开就是管理员栏', async () => {
    rememberTab('admin')
    fetchMock.mockResolvedValueOnce(ADMIN_LOGGED_OUT())

    const { wrapper } = await mountLogin('/login')

    expect(activeTab(wrapper)).toBe('管理员')
    const statusCall = findCall(fetchMock, '/api/auth/status')
    expect(statusCall).toBeTruthy()
    expect(statusCall[1].credentials).toBe('include')
    expect(findCall(fetchMock, '/api/hygiene/staff/me')).toBeFalsy()
  })

  it('?next 落在员工端前缀内时强制员工栏，优先于记住值', async () => {
    rememberTab('admin')
    fetchMock.mockResolvedValueOnce(STAFF_401())

    const { wrapper } = await mountLogin('/login?next=%2Fworkbench%2Fme%2Fmonth')

    expect(activeTab(wrapper)).toBe('员工')
    expect(findCall(fetchMock, '/api/hygiene/staff/me')).toBeTruthy()
    expect(findCall(fetchMock, '/api/auth/status')).toBeFalsy()
  })

  it('切栏会记住这次选择（下次打开落在这一栏）', async () => {
    fetchMock
      .mockResolvedValueOnce(STAFF_401())
      .mockResolvedValueOnce(ADMIN_LOGGED_OUT())

    const { wrapper } = await mountLogin('/login')
    await clickTab(wrapper, '管理员')

    expect(activeTab(wrapper)).toBe('管理员')
    expect(window.localStorage.getItem('luyun.login.panel.tab')).toBe('admin')
  })
})

describe('/login 面板：清单归属只看路径（票 09 收敛，不再随栏位变）', () => {
  it('切到员工栏时清单与主题色都不变：登录页整个归管理端那份', async () => {
    // 票 07 的做法是「面板身份就是归属」——一条路径两份清单，判据吃不下「当下停在哪一栏」，
    // 票 09 按票面口径把 `/login` 整个划给管理端。员工要装工作台那份，从 `/register`
    // 或已经登录后的工作台页面装（工作台前缀归工作台）。
    rememberTab('admin')
    fetchMock.mockResolvedValueOnce(ADMIN_LOGGED_OUT()).mockResolvedValueOnce(STAFF_401())

    const { wrapper } = await mountLogin('/login')
    expect(activeTab(wrapper)).toBe('管理员')
    expect(manifestHref()).toBe('/pwa/manifests/admin.webmanifest')

    await clickTab(wrapper, '员工')

    expect(activeTab(wrapper)).toBe('员工')
    expect(manifestHref()).toBe('/pwa/manifests/admin.webmanifest')
    expect(themeColor()).toBe('#0a0d16')
  })
})

describe('/login 面板：管理员栏', () => {
  beforeEach(() => rememberTab('admin'))

  it('状态接口报「已初始化且未登录」时渲染管理员登录表单', async () => {
    fetchMock.mockResolvedValueOnce(ADMIN_LOGGED_OUT())

    const { wrapper } = await mountLogin('/login')

    const form = wrapper.get('form')
    expect(form.get('input[type="text"]').exists()).toBe(true)
    expect(form.get('input[type="password"]').exists()).toBe(true)
    expect(form.get('button[type="submit"]').text()).toContain('登录')
  })

  it('提交登录表单时向管理端登录端点发 POST', async () => {
    fetchMock
      .mockResolvedValueOnce(ADMIN_LOGGED_OUT())
      .mockResolvedValueOnce(jsonResponse({ ok: true }))

    const { wrapper } = await mountLogin('/login')
    await submitWith(wrapper, {
      'input[type="text"]': 'admin',
      'input[type="password"]': 's3cret',
    })

    const loginCall = findCall(fetchMock, '/api/auth/login')
    expect(loginCall).toBeTruthy()
    expect(loginCall[1].method).toBe('POST')
    expect(loginCall[1].credentials).toBe('include')
    expect(JSON.parse(loginCall[1].body)).toMatchObject({ username: 'admin', password: 's3cret' })
  })

  it('状态接口报未初始化时渲染首次初始化表单并 POST 到初始化端点', async () => {
    fetchMock
      .mockResolvedValueOnce(ADMIN_UNINITIALIZED())
      .mockResolvedValueOnce(jsonResponse({ ok: true }))

    const { wrapper } = await mountLogin('/login')
    const form = wrapper.get('form')
    expect(form.get('button[type="submit"]').text()).toContain('创建账号')
    expect(form.findAll('input[type="password"]')).toHaveLength(2)

    await form.get('input[type="text"]').setValue('admin')
    await form.findAll('input[type="password"]')[0].setValue('s3cret-pass')
    await form.findAll('input[type="password"]')[1].setValue('s3cret-pass')
    await form.trigger('submit')
    await flushPromises()

    const initCall = findCall(fetchMock, '/api/auth/init')
    expect(initCall).toBeTruthy()
    expect(initCall[1].method).toBe('POST')
  })

  it('首次初始化只属于管理员栏：员工栏没有这一态', async () => {
    // 这一条要从默认的员工栏进，覆盖掉本 describe 的「记住管理员栏」前置。
    window.localStorage.clear()
    fetchMock
      .mockResolvedValueOnce(STAFF_401())
      .mockResolvedValueOnce(ADMIN_UNINITIALIZED())

    const { wrapper } = await mountLogin('/login')

    expect(activeTab(wrapper)).toBe('员工')
    expect(wrapper.get('form').get('button[type="submit"]').text()).toContain('登录')
    expect(wrapper.text()).not.toContain('创建账号并登录')

    // 同一份未初始化状态在管理员栏才变成首次初始化表单。
    await clickTab(wrapper, '管理员')
    expect(wrapper.get('form').get('button[type="submit"]').text()).toContain('创建账号')
  })

  it('已登录且未要求换账号时直接进入系统', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ logged_in: true, initialized: true, username: 'admin' }),
    )

    const { router } = await mountLogin('/login')

    expect(router.currentRoute.value.path).toBe('/')
  })

  it('已登录且带 ?switch=1 时停在确认面板', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ logged_in: true, initialized: true, username: 'admin' }),
    )

    const { wrapper } = await mountLogin('/login?switch=1')

    expect(wrapper.text()).toContain('admin')
    const buttons = wrapper.findAll('button').map((button) => button.text())
    expect(buttons).toContain('进入系统')
    expect(buttons).toContain('退出登录')
  })

  it('登录成功后回到 ?next 指定的管理页（老前缀的花名册地址也算）', async () => {
    fetchMock
      .mockResolvedValueOnce(ADMIN_LOGGED_OUT())
      .mockResolvedValueOnce(jsonResponse({ ok: true }))

    const { wrapper, router } = await mountLogin('/login?next=%2Fhygiene%2Froster')
    await submitWith(wrapper, {
      'input[type="text"]': 'admin',
      'input[type="password"]': 's3cret',
    })

    // `?next=/hygiene/roster` 是搬家前的老地址：入口换成新分组之后的花名册页（票 05）。
    expect(router.currentRoute.value.path).toBe('/workbench/hr/roster')
  })

  it('登录落点用 replace：后退不会退回登录页（管理栏与员工栏同一套 history 语义）', async () => {
    fetchMock
      .mockResolvedValueOnce(ADMIN_LOGGED_OUT())
      .mockResolvedValueOnce(jsonResponse({ ok: true }))

    const { wrapper, router } = await mountLogin('/login?next=%2Fworkbench%2Fhr%2Froster', {
      from: '/',
    })
    await submitWith(wrapper, {
      'input[type="text"]': 'admin',
      'input[type="password"]': 's3cret',
    })
    expect(router.currentRoute.value.path).toBe('/workbench/hr/roster')

    // 登录页（`router.push` 进来的那一格）被落点**替换**掉，不在后退历史里：
    // 再按一次浏览器后退，回到的是进登录页之前的那一页（这里是初始的 `/`）。
    router.back()
    await flushPromises()

    expect(router.currentRoute.value.path).toBe('/')
  })
})

describe('/login 面板：员工栏', () => {
  it('提交时向员工端登录端点发 POST，成功后落到 ?next 指定的员工页', async () => {
    fetchMock
      .mockResolvedValueOnce(STAFF_401())
      .mockResolvedValueOnce(jsonResponse({ success: true, employee: { name: '张三' } }))

    const { wrapper, router } = await mountLogin('/login?next=%2Fworkbench%2Fme%2Fmonth')
    await submitWith(wrapper, {
      'input[type="tel"]': '13800138000',
      'input[type="password"]': 's3cret',
    })

    const loginCall = findCall(fetchMock, '/api/hygiene/staff/login')
    expect(loginCall).toBeTruthy()
    expect(loginCall[1].method).toBe('POST')
    expect(JSON.parse(loginCall[1].body)).toMatchObject({
      phone: '13800138000',
      password: 's3cret',
      remember: true,
    })
    expect(router.currentRoute.value.path).toBe('/workbench/me/month')
  })

  it('员工栏落点同样是 replace：后退不退回登录页', async () => {
    fetchMock
      .mockResolvedValueOnce(STAFF_401())
      .mockResolvedValueOnce(jsonResponse({ success: true, employee: { name: '张三' } }))

    const { wrapper, router } = await mountLogin('/login?next=%2Fworkbench%2Fme%2Fmonth', {
      from: '/',
    })
    await submitWith(wrapper, {
      'input[type="tel"]': '13800138000',
      'input[type="password"]': 's3cret',
    })

    router.back()
    await flushPromises()

    expect(router.currentRoute.value.path).toBe('/')
  })

  it('没带 ?next 时落到员工默认落点（今天页）', async () => {
    fetchMock
      .mockResolvedValueOnce(STAFF_401())
      .mockResolvedValueOnce(jsonResponse({ success: true, employee: { name: '张三' } }))

    const { wrapper, router } = await mountLogin('/login')
    await submitWith(wrapper, {
      'input[type="tel"]': '13800138000',
      'input[type="password"]': 's3cret',
    })

    expect(router.currentRoute.value.path).toBe('/workbench/me/today')
  })

  it('员工会话仍有效时自动进入员工落点', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ employee: { name: '张三' } }))

    const { router } = await mountLogin('/login?next=%2Fworkbench%2Fme%2Fclean')

    expect(router.currentRoute.value.path).toBe('/workbench/me/clean')
  })

  it('?switch=1 且员工会话有效时停在确认面板，退出走员工端登出接口', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ employee: { name: '张三' } }))
      .mockResolvedValueOnce(jsonResponse({ success: true }))

    const { wrapper } = await mountLogin('/login?switch=1')

    expect(wrapper.text()).toContain('张三')
    const buttons = wrapper.findAll('button').map((button) => button.text())
    expect(buttons).toContain('进入系统')
    expect(buttons).toContain('退出登录')

    const logoutButton = wrapper.findAll('button').find((button) => button.text() === '退出登录')
    await logoutButton.trigger('click')
    await flushPromises()

    const logoutCall = findCall(fetchMock, '/api/hygiene/staff/logout')
    expect(logoutCall).toBeTruthy()
    expect(logoutCall[1].method).toBe('POST')
    // 退出后回到员工登录表单，而不是停在确认面板。
    expect(wrapper.get('form').get('input[type="tel"]').exists()).toBe(true)
  })

  it('?switch=1 但员工会话无效时不出现确认面板', async () => {
    fetchMock.mockResolvedValueOnce(STAFF_401())

    const { wrapper } = await mountLogin('/login?switch=1')

    const buttons = wrapper.findAll('button').map((button) => button.text())
    expect(buttons).not.toContain('进入系统')
    expect(wrapper.get('form').get('input[type="tel"]').exists()).toBe(true)
  })

  it('失败提示不出现在另一栏', async () => {
    fetchMock
      .mockResolvedValueOnce(STAFF_401())
      .mockResolvedValueOnce(
        jsonResponse({ detail: '手机号或密码错误，或账号未批准、已停用' }, { ok: false, status: 401 }),
      )
      .mockResolvedValueOnce(ADMIN_LOGGED_OUT())

    const { wrapper } = await mountLogin('/login')
    await submitWith(wrapper, {
      'input[type="tel"]': '13800138000',
      'input[type="password"]': 'wrong',
    })

    expect(wrapper.get('[role="alert"]').text()).toContain('手机号或密码错误')

    await clickTab(wrapper, '管理员')

    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    expect(wrapper.get('form').get('input[type="text"]').exists()).toBe(true)
  })
})

describe('/login 面板：非法 ?next 回落', () => {
  it('协议相对地址不强制员工栏，且管理员登录后回落到 /', async () => {
    rememberTab('admin')
    fetchMock
      .mockResolvedValueOnce(ADMIN_LOGGED_OUT())
      .mockResolvedValueOnce(jsonResponse({ ok: true }))

    const { wrapper, router } = await mountLogin('/login?next=%2F%2Fevil.example')

    expect(activeTab(wrapper)).toBe('管理员')
    await submitWith(wrapper, {
      'input[type="text"]': 'admin',
      'input[type="password"]': 's3cret',
    })
    expect(router.currentRoute.value.path).toBe('/')
  })

  it('站外地址不强制员工栏，员工登录后回落到今天页', async () => {
    fetchMock
      .mockResolvedValueOnce(STAFF_401())
      .mockResolvedValueOnce(jsonResponse({ success: true, employee: { name: '张三' } }))

    const { wrapper, router } = await mountLogin('/login?next=https%3A%2F%2Fevil.example%2Fphish')

    expect(activeTab(wrapper)).toBe('员工')
    await submitWith(wrapper, {
      'input[type="tel"]': '13800138000',
      'input[type="password"]': 's3cret',
    })
    expect(router.currentRoute.value.path).toBe('/workbench/me/today')
  })

  it('员工端路径不把管理员身份送进去：管理栏登录后回落到 /', async () => {
    fetchMock
      .mockResolvedValueOnce(STAFF_401())
      .mockResolvedValueOnce(ADMIN_LOGGED_OUT())
      .mockResolvedValueOnce(jsonResponse({ ok: true }))

    const { wrapper, router } = await mountLogin('/login?next=%2Fworkbench%2Fme%2Ftoday')
    // 带员工端 next 时面板开在员工栏；管理员自己切回管理栏。
    expect(activeTab(wrapper)).toBe('员工')
    await clickTab(wrapper, '管理员')

    await submitWith(wrapper, {
      'input[type="text"]': 'admin',
      'input[type="password"]': 's3cret',
    })

    expect(findCall(fetchMock, '/api/auth/login')).toBeTruthy()
    expect(router.currentRoute.value.path).toBe('/')
  })
})

describe('/login 面板：扫码进来看配方（票 07）', () => {
  // 本票风险最高的一条链路：扫码 → 未登录 → 登录页默认开员工栏 → 登录后回到那条配方。
  // 两处最容易漏：面板开错栏（厨师范不着管理栏）、员工栏把配方路径当「不是员工落点」
  // 静默落到 `/workbench/me/today`。
  const SCAN = '/login?next=%2Fworkbench%2Fkitchen%2Frecipe%2Fdetail%3Fslug%3Dchangfen'

  it('扫码进来默认开员工栏，优先于记住的管理员栏', async () => {
    rememberTab('admin')
    fetchMock.mockResolvedValueOnce(STAFF_401())

    const { wrapper } = await mountLogin(SCAN)

    expect(activeTab(wrapper)).toBe('员工')
    // 员工栏不该顺手去查管理端状态（扫码的绝大多数是厨师）。
    expect(findCall(fetchMock, '/api/auth/status')).toBeFalsy()
  })

  it('厨师在员工栏登录后回到**那条配方**（不是静默落到「今天」）', async () => {
    fetchMock
      .mockResolvedValueOnce(STAFF_401())
      .mockResolvedValueOnce(jsonResponse({ success: true, employee: { name: '张三' } }))

    const { wrapper, router } = await mountLogin(SCAN)
    await submitWith(wrapper, {
      'input[type="tel"]': '13800138000',
      'input[type="password"]': 's3cret',
    })

    expect(findCall(fetchMock, '/api/hygiene/staff/login')).toBeTruthy()
    expect(router.currentRoute.value.path).toBe('/workbench/kitchen/recipe/detail')
    expect(router.currentRoute.value.query.slug).toBe('changfen')
  })

  it('岗位码若还指着老地址，入口换成新地址后照样回到那条配方', async () => {
    fetchMock
      .mockResolvedValueOnce(STAFF_401())
      .mockResolvedValueOnce(jsonResponse({ success: true, employee: { name: '张三' } }))

    const { wrapper, router } = await mountLogin('/login?next=%2Frecipe%2Fdetail%3Fslug%3Dchangfen')
    await submitWith(wrapper, {
      'input[type="tel"]': '13800138000',
      'input[type="password"]': 's3cret',
    })

    expect(router.currentRoute.value.path).toBe('/workbench/kitchen/recipe/detail')
    expect(router.currentRoute.value.query.slug).toBe('changfen')
  })

  it('店长在管理栏登录后同样回到那条配方（阅读面两档都进得去）', async () => {
    fetchMock
      .mockResolvedValueOnce(STAFF_401())
      .mockResolvedValueOnce(ADMIN_LOGGED_OUT())
      .mockResolvedValueOnce(jsonResponse({ ok: true }))

    const { wrapper, router } = await mountLogin(SCAN)
    expect(activeTab(wrapper)).toBe('员工')
    await clickTab(wrapper, '管理员')
    await submitWith(wrapper, {
      'input[type="text"]': 'admin',
      'input[type="password"]': 's3cret',
    })

    expect(findCall(fetchMock, '/api/auth/login')).toBeTruthy()
    expect(router.currentRoute.value.path).toBe('/workbench/kitchen/recipe/detail')
  })
})
