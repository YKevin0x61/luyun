// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'

import StaffMealView, {
  AUTO_SAVE_DEBOUNCE_MS,
  cellBadgeText,
  clockText,
  draftWithItemsInfo,
  firstFailedDate,
  parseDateQuery,
  shortDate,
  staffMonthSummary,
  todayText,
} from '../StaffMealView.vue'
import { useWorkbenchIdentityStore } from '../../../stores/workbenchIdentity'

// 员工餐采购页（2026-11-13 走查 S1/S2/S3/S5/S8/S9/S10/F14/F18）：
// 失败单可见、今天标记与员工口径、重推二次确认、术语、草稿自动保存、导入预览提示、
// 模板下载、乐观锁冲突回执。
//
// 断言分两层：口径/日期这类纯逻辑直接 import 组件导出的函数单测；
// 可见性/流程用挂载后的渲染与发出的请求断言（不看组件内部状态）。

const STAFF_MEAL_PATH = '/workbench/kitchen/staff-meal'
const TODAY = todayText()
const MONTH = TODAY.slice(0, 7)

let currentWrapper = null

function localDate(offset = 0) {
  const date = new Date()
  date.setDate(date.getDate() + offset)
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(
    date.getDate()
  ).padStart(2, '0')}`
}

function jsonResponse(data, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => data,
  }
}

function order(overrides = {}) {
  return {
    id: 11,
    order_date: TODAY,
    status: 'draft',
    push_status: 'not_pushed',
    push_error: null,
    submitter_name: '张三',
    submitted_at: '',
    filled_items: 0,
    can_edit: true,
    updated_at: '2026-11-13T09:00:00+08:00',
    remark: '',
    ...overrides,
  }
}

function menuDay(date) {
  return {
    menu_date: date,
    lunch_main: '红烧肉',
    lunch_side1: '炒青菜',
    lunch_side2: '番茄蛋汤',
    dinner_main: '宫保鸡丁',
    dinner_side1: '凉拌黄瓜',
    dinner_side2: '紫菜蛋汤',
    drink: '绿豆汤',
    freeform: false,
  }
}

function dishItems() {
  return [
    {
      id: 1,
      dish_name: '红烧肉',
      meal: 'lunch',
      role: 'main',
      items: [
        { item_name: '五花肉', quantity: 2, unit: '斤' },
        { item_name: '冰糖', quantity: null, unit: '袋' },
      ],
    },
  ]
}

function dayPayload(date, { order: dayOrder = null, dishes = dishItems() } = {}) {
  return {
    menu_day: menuDay(date),
    dishes,
    other_items: [],
    order: dayOrder,
    summary: {},
  }
}

function savedDay(date, body, current) {
  const base = current || dayPayload(date, { order: order({ order_date: date }) })
  return {
    ...base,
    order: {
      ...(base.order || order({ order_date: date })),
      remark: body?.remark ?? '',
      updated_at: '2026-11-13T10:30:00+08:00',
    },
  }
}

/** 按「接口路径 + 方法」造假响应，同时把每次请求记进 mock.calls 供断言。 */
function createFetchMock({
  meta = { units: ['斤', '袋'], max_items: 300, max_remark: 200 },
  month = null,
  days = {},
  save = null,
  submit = null,
} = {}) {
  return vi.fn(async (url, init = {}) => {
    const method = String(init.method || 'GET').toUpperCase()
    const path = String(url).split('?')[0]
    const body = init.body ? JSON.parse(String(init.body)) : null
    if (path === '/api/staff-meal/meta') return jsonResponse(meta)
    if (method === 'GET' && path.startsWith('/api/staff-meal/months/')) {
      return month === null ? jsonResponse({ detail: 'not found' }, 404) : jsonResponse(month)
    }
    if (method === 'GET' && path.startsWith('/api/staff-meal/days/')) {
      const date = path.split('/')[4]
      return days[date]
        ? jsonResponse(days[date])
        : jsonResponse({ detail: '没有这一天的菜单' }, 404)
    }
    if (method === 'PUT' && path.endsWith('/order')) {
      const date = path.split('/')[4]
      if (save) return save({ date, body })
      return jsonResponse(savedDay(date, body, days[date]))
    }
    if (method === 'POST' && path.endsWith('/order/submit')) {
      const date = path.split('/')[4]
      if (submit) return submit({ date, body })
      const current = days[date] || dayPayload(date, { order: order({ order_date: date }) })
      return jsonResponse({
        ...current,
        order: {
          ...(current.order || order({ order_date: date })),
          status: 'submitted',
          push_status: 'pushed',
        },
      })
    }
    return jsonResponse({ detail: 'not found' }, 404)
  })
}

function requestedPaths(fetchMock) {
  return fetchMock.mock.calls.map(([url]) => String(url).split('?')[0])
}

function putCalls(fetchMock) {
  return fetchMock.mock.calls.filter(
    ([, init]) => String(init?.method || 'GET').toUpperCase() === 'PUT'
  )
}

function postCalls(fetchMock) {
  return fetchMock.mock.calls.filter(
    ([, init]) => String(init?.method || 'GET').toUpperCase() === 'POST'
  )
}

async function mountView({
  identity = 'super',
  path = STAFF_MEAL_PATH,
  fetchOptions = {},
} = {}) {
  const fetchMock = createFetchMock(fetchOptions)
  vi.stubGlobal('fetch', fetchMock)

  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useWorkbenchIdentityStore()
  store.identity = identity
  store.sessions = { admin: identity === 'super', staff: identity === 'staff' }
  store.available = identity ? [identity] : []

  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: STAFF_MEAL_PATH, component: { template: '<div />' } }],
  })
  await router.push(path)
  await router.isReady()

  const wrapper = mount(StaffMealView, { global: { plugins: [router, pinia] } })
  currentWrapper = wrapper
  await flushPromises()
  await flushPromises()
  return { wrapper, router, store, fetchMock }
}

function findButton(wrapper, text) {
  return wrapper.findAll('button').find((button) => button.text() === text)
}

afterEach(async () => {
  // 先卸组件（会 flush 挂着的自动保存草稿），再撤掉 fetch / 定时器。
  currentWrapper?.unmount()
  currentWrapper = null
  await flushPromises()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  document.body.innerHTML = ''
})

// ── 纯函数：日期 / 查询参数 ─────────────────────────────────────────
describe('员工餐页 · 日期与 ?date= 参数解析', () => {
  it('todayText 用本机自然日，clockText 是 HH:MM', () => {
    expect(todayText()).toMatch(/^\d{4}-\d{2}-\d{2}$/)
    expect(clockText()).toMatch(/^\d{2}:\d{2}$/)
    expect(todayText(new Date(2026, 0, 5))).toBe('2026-01-05')
    expect(clockText(new Date(2026, 0, 5, 9, 4))).toBe('09:04')
  })

  it('shortDate 只压合法日期', () => {
    expect(shortDate('2026-11-05')).toBe('11-05')
    expect(shortDate('2026-11-05T00:00:00')).toBe('')
    expect(shortDate('')).toBe('')
  })

  it('parseDateQuery 只认真实存在的 YYYY-MM-DD', () => {
    expect(parseDateQuery('2026-11-05')).toBe('2026-11-05')
    expect(parseDateQuery(' 2026-11-05 ')).toBe('2026-11-05')
    expect(parseDateQuery(['2026-11-05', '2026-11-06'])).toBe('2026-11-05')
    expect(parseDateQuery('2028-02-29')).toBe('2028-02-29')
    // 脏值一律当没传：格式错、不可能日期、非闰年 2-29、带时间、空数组。
    expect(parseDateQuery('2026-2-5')).toBe('')
    expect(parseDateQuery('2026-13-01')).toBe('')
    expect(parseDateQuery('2026-02-30')).toBe('')
    expect(parseDateQuery('2026-02-29')).toBe('')
    expect(parseDateQuery('2026-11-05T00:00:00')).toBe('')
    expect(parseDateQuery([])).toBe('')
    expect(parseDateQuery(null)).toBe('')
    expect(parseDateQuery(undefined)).toBe('')
    expect(parseDateQuery(20261105)).toBe('')
  })
})

// ── 纯函数：员工摘要 ────────────────────────────────────────────────
describe('员工餐页 · 员工月历摘要口径', () => {
  const baseDays = [
    { menu_day: { menu_date: '2026-11-01' }, order: { status: 'draft' } },
    { menu_day: { menu_date: '2026-11-12' }, order: { status: 'submitted' } },
    { menu_day: { menu_date: '2026-11-13' }, order: { status: 'draft' } },
    { menu_day: { menu_date: '2026-11-20' }, order: { status: 'submitted' } },
    { menu_day: { menu_date: '2026-11-25' }, order: null },
  ]

  it('当月：今天未填 + 只数今天及以后没提交的天', () => {
    const result = staffMonthSummary({ days: baseDays, month: '2026-11', today: '2026-11-13' })
    expect(result.isCurrentMonth).toBe(true)
    expect(result.todayStatus).toBe('empty')
    expect(result.remaining).toBe(2) // 11-13 草稿 + 11-25 没单；11-01 过去的漏填不算
    expect(result.text).toBe('今天：未填 · 本月还差 2 天')
  })

  it('今天已提交：摘要说已提交', () => {
    const days = baseDays.map((row) =>
      row.menu_day.menu_date === '2026-11-13'
        ? { menu_day: row.menu_day, order: { status: 'submitted' } }
        : row
    )
    const result = staffMonthSummary({ days, month: '2026-11', today: '2026-11-13' })
    expect(result.todayStatus).toBe('submitted')
    expect(result.text).toBe('今天：已提交 · 本月还差 1 天')
  })

  it('未来月 / 过去月：说还差几天 / 已过完', () => {
    const future = staffMonthSummary({
      days: [{ menu_day: { menu_date: '2026-12-01' } }, { menu_day: { menu_date: '2026-12-02' } }],
      month: '2026-12',
      today: '2026-11-13',
    })
    expect(future.isCurrentMonth).toBe(false)
    expect(future.text).toBe('本月还差 2 天')

    const past = staffMonthSummary({
      days: [{ menu_day: { menu_date: '2026-10-01' }, order: { status: 'draft' } }],
      month: '2026-10',
      today: '2026-11-13',
    })
    expect(past.text).toBe('本月已过完')
  })

  it('兼容只有 order.order_date 的老数据，空行不算天数', () => {
    const result = staffMonthSummary({
      days: [
        { order: { order_date: '2026-11-18', status: 'draft' } },
        { menu_day: {}, order: null },
        { menu_day: {}, order: {} },
      ],
      month: '2026-11',
      today: '2026-11-13',
    })
    expect(result.remaining).toBe(1)
  })
})

// ── 纯函数：徽章 / 第一张失败单 / S9 预览 ──────────────────────────
describe('员工餐页 · 月历徽章与失败单 / 导入预览', () => {
  it('cellBadgeText：已提交态说推送结果，草稿填过东西说「已填 · N 项」', () => {
    expect(cellBadgeText(null)).toBe('')
    expect(cellBadgeText({ menu_day: {} })).toBe('')
    expect(cellBadgeText({ order: { status: 'submitted', push_status: 'pushed' } })).toBe('已提交')
    expect(cellBadgeText({ order: { status: 'submitted', push_status: 'failed' } })).toBe('推送失败')
    expect(cellBadgeText({ order: { status: 'draft', filled_items: 3 } })).toBe('已填 · 3 项')
    expect(cellBadgeText({ order: { status: 'draft', filled_items: 0 } })).toBe('草稿')
    expect(cellBadgeText({ order: { status: 'draft' } })).toBe('草稿')
  })

  it('firstFailedDate 取最早一张推失败的单（兼容缺 order_date）', () => {
    const days = [
      { menu_day: { menu_date: '2026-11-01' }, order: { order_date: '2026-11-20', push_status: 'failed' } },
      { menu_day: { menu_date: '2026-11-02' }, order: { order_date: '2026-11-15', push_status: 'failed' } },
      { menu_day: { menu_date: '2026-11-03' }, order: { order_date: '2026-11-10', push_status: 'pushed' } },
      { menu_day: { menu_date: '2026-11-04' }, order: { push_status: 'failed' } },
    ]
    expect(firstFailedDate(days)).toBe('2026-11-04')
    expect(firstFailedDate([])).toBe('')
    expect(firstFailedDate(null)).toBe('')
  })

  it('draftWithItemsInfo 把重传会影响到的草稿日期压成 MM-DD', () => {
    expect(draftWithItemsInfo(undefined)).toEqual({ count: 0, dates: [] })
    expect(draftWithItemsInfo({})).toEqual({ count: 0, dates: [] })
    expect(draftWithItemsInfo({ draft_with_items: 'oops' })).toEqual({ count: 0, dates: [] })
    expect(
      draftWithItemsInfo({
        draft_with_items: [
          { order_date: '2026-11-05', filled_items: 2 },
          { order_date: '2026-11-03', filled_items: 1 },
          { order_date: null },
        ],
      })
    ).toEqual({ count: 3, dates: ['11-03', '11-05'] })
  })
})

// ── 挂载：月历 ──────────────────────────────────────────────────────
describe('员工餐页 · 月历（失败可见 / 今天 / 身份口径）', () => {
  function monthFixture() {
    return {
      month: MONTH,
      days: [
        {
          menu_day: menuDay(TODAY),
          order: order({ id: 1, order_date: TODAY, status: 'draft', filled_items: 3 }),
        },
        {
          menu_day: menuDay(localDate(-1)),
          order: order({
            id: 2,
            order_date: localDate(-1),
            status: 'submitted',
            push_status: 'failed',
            push_error: 'webhook 超时',
            filled_items: 2,
          }),
        },
      ],
      summary: { days: 2, orders: 2, submitted: 1, draft: 1, failed: 1 },
    }
  }

  function monthDays() {
    return {
      [TODAY]: dayPayload(TODAY, {
        order: order({ id: 1, order_date: TODAY, status: 'draft', filled_items: 3 }),
      }),
      [localDate(-1)]: dayPayload(localDate(-1), {
        order: order({
          id: 2,
          order_date: localDate(-1),
          status: 'submitted',
          push_status: 'failed',
          push_error: 'webhook 超时：群机器人没响应',
          filled_items: 2,
        }),
      }),
    }
  }

  it('超级管理员：摘要带失败数、草稿格徽章是「已填 · N 项」、能下载模板', async () => {
    const { wrapper } = await mountView({
      identity: 'super',
      fetchOptions: { month: monthFixture(), days: monthDays() },
    })

    const banner = wrapper.get('.sm-banner-action')
    expect(banner.text()).toContain('有 1 张没推到核心群，点这里看')
    expect(wrapper.get('.sm-sub').text()).toContain('推送失败 1 张')
    expect(wrapper.get('.sm-sub').text()).toContain('本月 2 天')
    expect(wrapper.text()).toContain('已填 · 3 项')
    const [year, month] = MONTH.split('-')
    const template = wrapper.get('a.sm-template')
    expect(template.attributes('href')).toBe(
      `/api/staff-meal/menu/template?year=${year}&month=${month}`
    )
    expect(template.text()).toBe(`下载 ${year} 年 ${Number(month)} 月菜单模板`)
    // 术语：页面不再出现裸「管理员 / 店长」。
    expect(wrapper.text()).not.toMatch(/(?<!超级)管理员/)
    expect(wrapper.text()).not.toContain('店长')
  })

  it('点失败提示直接打开第一张失败单，并在单日头show失败原因', async () => {
    const { wrapper, fetchMock } = await mountView({
      identity: 'super',
      fetchOptions: { month: monthFixture(), days: monthDays() },
    })

    await wrapper.get('.sm-banner-action').trigger('click')
    await flushPromises()
    await flushPromises()

    expect(requestedPaths(fetchMock)).toContain(`/api/staff-meal/days/${localDate(-1)}`)
    expect(wrapper.text()).toContain('原因：webhook 超时：群机器人没响应')
    // 单日视图不再显示月历那条顶部提示。
    expect(wrapper.find('.sm-banner-action').exists()).toBe(false)
  })

  it('员工视角：摘要换成「今天：未填 · 本月还差 N 天」，今天格子有标记，没有管理员入口', async () => {
    const { wrapper } = await mountView({
      identity: 'staff',
      fetchOptions: {
        month: {
          month: MONTH,
          days: [
            {
              menu_day: menuDay(TODAY),
              order: order({ order_date: TODAY, status: 'draft', filled_items: 0 }),
            },
          ],
          summary: { days: 1, orders: 1, submitted: 0, draft: 1, failed: 0 },
        },
      },
    })

    expect(wrapper.get('.sm-sub').text()).toContain('今天：未填')
    expect(wrapper.get('.sm-sub').text()).toMatch(/本月还差 \d+ 天/)
    expect(wrapper.get('.sm-cell.is-today').text()).toContain(String(new Date().getDate()))
    expect(wrapper.find('a.sm-template').exists()).toBe(false)
    expect(wrapper.find('.sm-toolbar .sm-btn-soft').exists()).toBe(false)
  })

  it('空态文案：员工等超级管理员导入，管理员看到导入指引', async () => {
    const emptyMonth = { month: MONTH, days: [], summary: { days: 0, orders: 0, submitted: 0, draft: 0 } }

    const staff = await mountView({
      identity: 'staff',
      fetchOptions: { month: emptyMonth },
    })
    expect(staff.wrapper.get('.sm-empty').text()).toContain('等超级管理员导入当月菜单后就能填单了')

    const admin = await mountView({
      identity: 'super',
      fetchOptions: { month: emptyMonth },
    })
    expect(admin.wrapper.get('.sm-empty').text()).toContain('导入月度菜单')
  })
})

// ── 挂载：单日流程 ──────────────────────────────────────────────────
function submittedDay() {
  return dayPayload(TODAY, {
    order: order({
      order_date: TODAY,
      status: 'submitted',
      push_status: 'pushed',
      submitter_name: '张三',
      submitted_at: '2026-11-12T18:30:00+08:00',
      filled_items: 2,
    }),
  })
}

function draftDay(filledItems = 1) {
  return dayPayload(TODAY, {
    order: order({
      order_date: TODAY,
      status: 'draft',
      filled_items: filledItems,
      updated_at: '2026-11-13T09:00:00+08:00',
    }),
  })
}

function directPath(date = TODAY) {
  return `${STAFF_MEAL_PATH}?date=${date}`
}

describe('员工餐页 · 单日流程（S3 重推确认 / S5 只读文案 / F14 乐观锁）', () => {
  it('已提交：保存修改是主按钮，更新并重新推送先弹确认再发请求', async () => {
    const { wrapper, fetchMock } = await mountView({
      identity: 'super',
      path: directPath(),
      fetchOptions: { days: { [TODAY]: submittedDay() } },
    })

    const save = findButton(wrapper, '保存修改')
    const repush = findButton(wrapper, '更新并重新推送')
    expect(save.classes()).toContain('sm-btn-primary')
    expect(repush.classes()).toContain('sm-btn-soft')

    await repush.trigger('click')
    await flushPromises()
    const dialog = document.body.querySelector('.sm-dialog')
    expect(dialog).toBeTruthy()
    expect(dialog.textContent).toContain('更新并重新推送？')
    expect(dialog.textContent).toContain('（更新）')
    expect(dialog.textContent).toContain('完整清单')
    // 确认之前不能已经推出去。
    expect(postCalls(fetchMock)).toHaveLength(0)

    document.body.querySelector('.sm-dialog .sm-btn-primary').click()
    await flushPromises()
    await flushPromises()
    await flushPromises()

    expect(document.body.querySelector('.sm-dialog')).toBeNull()
    expect(postCalls(fetchMock).map(([url]) => String(url).split('?')[0])).toEqual([
      `/api/staff-meal/days/${TODAY}/order/submit`,
    ])
  })

  it('首次提交不弹确认，点下去直接提交', async () => {
    const { wrapper, fetchMock } = await mountView({
      identity: 'super',
      path: directPath(),
      fetchOptions: { days: { [TODAY]: draftDay() } },
    })

    await findButton(wrapper, '提交并推送到核心群').trigger('click')
    await flushPromises()
    await flushPromises()
    await flushPromises()

    expect(document.body.querySelector('.sm-dialog')).toBeNull()
    expect(postCalls(fetchMock).map(([url]) => String(url).split('?')[0])).toEqual([
      `/api/staff-meal/days/${TODAY}/order/submit`,
    ])
  })

  it('别人的单：只读 banner 说「只有提交者本人或超级管理员能改」', async () => {
    const locked = dayPayload(TODAY, {
      order: order({
        order_date: TODAY,
        status: 'submitted',
        push_status: 'pushed',
        can_edit: false,
        submitter_name: '李四',
      }),
    })
    const { wrapper } = await mountView({
      identity: 'staff',
      path: directPath(),
      fetchOptions: { days: { [TODAY]: locked } },
    })

    expect(wrapper.get('.sm-banner-soft').text()).toContain(
      '只有提交者本人或超级管理员能改'
    )
    expect(wrapper.text()).not.toMatch(/(?<!超级)管理员/)
    expect(wrapper.text()).not.toContain('店长')
  })

  it('手动保存带上 expected_updated_at', async () => {
    const { wrapper, fetchMock } = await mountView({
      identity: 'staff',
      path: directPath(),
      fetchOptions: { days: { [TODAY]: draftDay() } },
    })

    await findButton(wrapper, '保存草稿').trigger('click')
    await flushPromises()
    await flushPromises()

    const puts = putCalls(fetchMock)
    expect(puts).toHaveLength(1)
    expect(String(puts[0][0])).toBe(`/api/staff-meal/days/${TODAY}/order`)
    expect(JSON.parse(puts[0][1].body).expected_updated_at).toBe('2026-11-13T09:00:00+08:00')
    expect(wrapper.get('.sm-notice').text()).toContain('已保存草稿')
  })

  it('409 冲突：显示后端中文提示并重新拉一遍最新单', async () => {
    const { wrapper, fetchMock } = await mountView({
      identity: 'staff',
      path: directPath(),
      fetchOptions: {
        days: { [TODAY]: draftDay() },
        save: () => jsonResponse({ detail: '这张单刚被别人改过：刷新后再保存' }, 409),
      },
    })

    await findButton(wrapper, '保存草稿').trigger('click')
    await flushPromises()
    await flushPromises()
    await flushPromises()

    expect(wrapper.get('.sm-notice').text()).toContain('这张单刚被别人改过：刷新后再保存')
    const dayGets = requestedPaths(fetchMock).filter(
      (path) => path === `/api/staff-meal/days/${TODAY}`
    )
    expect(dayGets).toHaveLength(2)
  })
})

// ── 挂载：过去日（S6 day_expired）────────────────────────────────────
describe('员工餐页 · 过去日只能存草稿（S6）', () => {
  it('今天之前的单：提交按钮置灰并说明，存草稿照常可用', async () => {
    const past = localDate(-1)
    const { wrapper, fetchMock } = await mountView({
      identity: 'staff',
      path: directPath(past),
      fetchOptions: {
        days: {
          [past]: dayPayload(past, {
            order: order({
              id: 21,
              order_date: past,
              status: 'draft',
              updated_at: '2026-11-12T09:00:00+08:00',
            }),
          }),
        },
      },
    })

    const submit = findButton(wrapper, '提交并推送到核心群')
    expect(submit.attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('这天已经过去了：草稿还能改，但不能再提交或推送')

    await submit.trigger('click')
    await flushPromises()
    expect(postCalls(fetchMock)).toHaveLength(0)
    expect(wrapper.find('.sm-dialog').exists()).toBe(false)

    // 保存草稿不受影响（过去日草稿仍可改、可存）。
    await findButton(wrapper, '保存草稿').trigger('click')
    await flushPromises()
    await flushPromises()
    expect(putCalls(fetchMock)).toHaveLength(1)
  })

  it('跨天后提交：后端 409 day_expired 的中文提示原样显示', async () => {
    const detail = `${TODAY} 已经过去了：草稿还能改，但不能再提交/推送`
    const { wrapper, fetchMock } = await mountView({
      identity: 'staff',
      path: directPath(),
      fetchOptions: {
        days: { [TODAY]: draftDay() },
        submit: () => jsonResponse({ detail }, 409),
      },
    })

    await findButton(wrapper, '提交并推送到核心群').trigger('click')
    await flushPromises()
    await flushPromises()
    await flushPromises()

    expect(wrapper.get('.sm-notice').text()).toContain(
      '已经过去了：草稿还能改，但不能再提交/推送'
    )
    // 只发了一次提交请求，没有重试循环。
    expect(postCalls(fetchMock)).toHaveLength(1)
  })
})

// ── 挂载：自动保存 ──────────────────────────────────────────────────
describe('员工餐页 · 草稿自动保存（S8）', () => {
  it(
    '草稿改了备注：防抖窗口内不发，约 2 秒后自动保存并显示回执',
    async () => {
      const { wrapper, fetchMock } = await mountView({
        identity: 'staff',
        path: directPath(),
        fetchOptions: { days: { [TODAY]: draftDay() } },
      })

      await wrapper.get('textarea').setValue('多买点姜，腌肉用')
      await flushPromises()
      expect(putCalls(fetchMock)).toHaveLength(0)

      await new Promise((resolve) => setTimeout(resolve, AUTO_SAVE_DEBOUNCE_MS + 300))
      await flushPromises()
      await flushPromises()

      const puts = putCalls(fetchMock)
      expect(puts).toHaveLength(1)
      const body = JSON.parse(puts[0][1].body)
      expect(body.remark).toBe('多买点姜，腌肉用')
      expect(body.expected_updated_at).toBe('2026-11-13T09:00:00+08:00')
      expect(wrapper.get('.sm-autosave').text()).toContain('已自动保存')
    },
    15000
  )

  it(
    '已提交的单不自动保存',
    async () => {
      const { wrapper, fetchMock } = await mountView({
        identity: 'staff',
        path: directPath(),
        fetchOptions: { days: { [TODAY]: submittedDay() } },
      })

      await wrapper.get('textarea').setValue('这句话不该被自动写进去')
      await new Promise((resolve) => setTimeout(resolve, AUTO_SAVE_DEBOUNCE_MS + 300))
      await flushPromises()

      expect(putCalls(fetchMock)).toHaveLength(0)
      expect(wrapper.find('.sm-autosave').exists()).toBe(false)
    },
    15000
  )
})

// ── 挂载：?date= 直达 ───────────────────────────────────────────────
describe('员工餐页 · ?date= 直达单日', () => {
  it('合法日期直接进单日，不再拉月历', async () => {
    const target = '2026-11-20'
    const { wrapper, fetchMock } = await mountView({
      identity: 'staff',
      path: directPath(target),
      fetchOptions: {
        days: {
          [target]: dayPayload(target, {
            order: order({ order_date: target, status: 'draft' }),
          }),
        },
      },
    })

    expect(wrapper.get('h1').text()).toContain('11 月 20 日')
    expect(requestedPaths(fetchMock)).toContain(`/api/staff-meal/days/${target}`)
    expect(
      requestedPaths(fetchMock).some((path) => path.startsWith('/api/staff-meal/months/'))
    ).toBe(false)
  })

  it('脏日期回落月历', async () => {
    const { fetchMock } = await mountView({
      identity: 'staff',
      path: directPath('2026-02-30'),
      fetchOptions: {
        month: { month: MONTH, days: [], summary: { days: 0, orders: 0, submitted: 0, draft: 0 } },
      },
    })

    expect(
      requestedPaths(fetchMock).some((path) => path.startsWith('/api/staff-meal/months/'))
    ).toBe(true)
  })
})
