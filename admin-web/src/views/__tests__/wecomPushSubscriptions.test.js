// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import WecomPushView from '../WecomPushView.vue'

// 票 06 页面那一半的验收：页内 tab 骨架、渠道卡片上那几样、订阅矩阵的勾选与请求体、
// 渠道超过 8 个时切成「先选内容、再勾群」的多选列表。
//
// 断言的是**渲染出来的东西**与**页面发出去的请求**，不是组件内部状态：组件怎么组织
// 这些数据可以改，勾一下要发什么请求、超阈值之后页面长什么样不能改。
//
// 与 `wecomPushLayout.test.js`（读源码钉窄屏那几条 CSS）分工：那一份挡的是"样式被
// 顺手简化掉"，这一份挡的是"功能没接上"。

const API_VERSION = 'v2'

/** 一条渠道的完整卡片形状（读接口给的就是这个）。 */
function channel(id, name, overrides = {}) {
  return {
    id,
    name,
    enabled: true,
    notes: '',
    webhook_url_masked: `https://qyapi.weixin.qq.com/***${id}`,
    job_count: 0,
    last_sent_at: '',
    groups: [],
    topics: [],
    ...overrides,
  }
}

function topic(id, name, subscribed = [], overrides = {}) {
  return {
    id,
    name,
    triggers: ['event'],
    contains_employee_photos: false,
    default_schedule_time: null,
    channels: subscribed.map((channelId) => ({ id: channelId, enabled: true })),
    ...overrides,
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

/** 记录每一次请求；`channels` 用闭包给，方便多选列表那一档造 9 个渠道。
 *
 * `writeStatus` 非 200 时**写请求**一律按该状态失败（读请求照常），用来演「保存失败」。 */
function probeFetch({ channels = [], topics = [], groups = [], writeStatus = 200 }) {
  const calls = []
  const fetchMock = vi.fn(async (url, options = {}) => {
    const path = String(url)
    const method = options.method || 'GET'
    calls.push({ path, method, body: options.body })
    if (method !== 'GET' && writeStatus !== 200) {
      return jsonResponse({ detail: '数据验证失败' }, writeStatus)
    }
    if (path.includes('/api/wecom-push/meta')) {
      return jsonResponse({ success: true, api_version: API_VERSION, job_templates: [] })
    }
    if (path.includes('/api/wecom-push/subscriptions')) {
      return jsonResponse({
        success: true, api_version: API_VERSION, channels, topics,
      })
    }
    if (path.includes('/api/wecom-push/channel-groups')) {
      return jsonResponse({ success: true, groups })
    }
    if (path.includes('/api/wecom-push/webhooks')) {
      return jsonResponse({ success: true, webhooks: [], channels, topics, groups })
    }
    if (path.includes('/api/wecom-push/jobs')) return jsonResponse({ success: true, jobs: [] })
    if (path.includes('/api/wecom-push/logs')) return jsonResponse({ success: true, logs: [] })
    if (path.includes('/api/stations')) return jsonResponse([])
    return jsonResponse({}, 404)
  })
  fetchMock.calls = calls
  return fetchMock
}

async function mountView(options) {
  const fetchMock = probeFetch(options)
  vi.stubGlobal('fetch', fetchMock)
  const pinia = createPinia()
  setActivePinia(pinia)
  const wrapper = mount(WecomPushView, { global: { plugins: [pinia] } })
  await flushPromises()
  await flushPromises()
  return { wrapper, fetchMock }
}

/** 页内 tab：按可见文字点。 */
async function openTab(wrapper, name) {
  const tab = wrapper.findAll('.view-tab').find((node) => node.text() === name)
  expect(tab, `tab「${name}」不在页面上`).toBeTruthy()
  await tab.trigger('click')
  await flushPromises()
}

beforeEach(() => {
  vi.unstubAllGlobals()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('企微推送页的 tab 骨架（不改路由）', () => {
  it('五个页内 tab 都在，默认停在「渠道」', async () => {
    const { wrapper } = await mountView({ channels: [channel(1, '门店群')], topics: [topic('sales_report', '销售报表')] })

    // 「变更历史」是票 11 补上的第五个（配置变更留痕，只读）。
    expect(wrapper.findAll('.view-tab').map((node) => node.text())).toEqual([
      '渠道', '订阅', '定时任务', '发送记录', '变更历史',
    ])
    expect(wrapper.find('.view-tab.active').text()).toBe('渠道')
    expect(wrapper.text()).toContain('渠道群组')
  })

  it('切到「订阅」看到矩阵，切到「发送记录」看到记录表', async () => {
    const { wrapper } = await mountView({
      channels: [channel(1, '门店群')],
      topics: [topic('sales_report', '销售报表', [1])],
    })

    await openTab(wrapper, '订阅')
    expect(wrapper.find('table.wp-matrix').exists()).toBe(true)
    expect(wrapper.find('table.wp-matrix').text()).toContain('销售报表')

    await openTab(wrapper, '发送记录')
    expect(wrapper.text()).toContain('暂无发送记录')

    await openTab(wrapper, '定时任务')
    expect(wrapper.text()).toContain('推送任务')
  })
})

describe('渠道 tab 的卡片', () => {
  it('显示启停 / 备注 / 所属群组 / 订阅内容 / 最近一次发送成功时间', async () => {
    const { wrapper } = await mountView({
      channels: [channel(1, '门店群', {
        notes: '早班用',
        job_count: 2,
        last_sent_at: '2026-10-05T21:30:12+08:00',
        groups: [{ id: 3, name: '日报群组', enabled: true }],
        topics: [{ id: 'sales_report', name: '销售报表', via_group: true }],
      })],
      topics: [topic('sales_report', '销售报表', [1])],
      groups: [{ id: 3, name: '日报群组', enabled: true, notes: '', member_channel_ids: [1] }],
    })

    const text = wrapper.text()
    expect(text).toContain('启用')
    expect(text).toContain('早班用')
    expect(text).toContain('日报群组')
    expect(text).toContain('销售报表')
    expect(text).toContain('2026-10-05 21:30:12')
    expect(text).toContain('含群组订阅')
  })

  it('删除渠道前先给出后果提示，取消后不发请求', async () => {
    // 任务不再绑定渠道（票 08）：删除不再会被「被任务引用」拦下来，确认框只说清
    // 订阅与群组成员会跟着走。
    const { wrapper, fetchMock } = await mountView({
      channels: [channel(1, '门店群', { job_count: 1, topics: [{ id: 'sales_report', name: '销售报表' }] })],
      topics: [topic('sales_report', '销售报表')],
    })
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)

    const del = wrapper.findAll('button').find((node) => node.text() === '删除')
    await del.trigger('click')
    await flushPromises()

    expect(confirmSpy).toHaveBeenCalled()
    expect(String(confirmSpy.mock.calls[0][0])).toContain('订阅')
    expect(String(confirmSpy.mock.calls[0][0])).not.toContain('推送任务')
    // 用户点了「取消」：一条写请求都不该发出去
    expect(fetchMock.calls.filter((call) => call.method !== 'GET')).toEqual([])
  })

  // U6（旧清单 A29）：「测试」按钮一点即外发，走查时真的把消息发进了门店群。
  it('「测试」先确认：文案点名目标群并说清撤不回来', async () => {
    const { wrapper } = await mountView({
      channels: [channel(1, '门店群')],
      topics: [topic('sales_report', '销售报表', [1])],
    })
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)

    const test = wrapper.findAll('button').find((node) => node.text() === '测试')
    expect(test, '找不到「测试」按钮').toBeTruthy()
    await test.trigger('click')
    await flushPromises()

    expect(confirmSpy).toHaveBeenCalledTimes(1)
    const text = String(confirmSpy.mock.calls[0][0])
    expect(text).toContain('门店群')
    expect(text).toContain('无法撤回')
  })

  it('确认框点「取消」：一条外发请求都不发', async () => {
    const { wrapper, fetchMock } = await mountView({
      channels: [channel(1, '门店群')],
      topics: [topic('sales_report', '销售报表', [1])],
    })
    vi.spyOn(window, 'confirm').mockReturnValue(false)

    await wrapper.findAll('button').find((node) => node.text() === '测试').trigger('click')
    await flushPromises()

    expect(fetchMock.calls.filter((call) => call.path.includes('/test'))).toEqual([])
  })

  it('确认后才真的发测试请求（点「确定」这一路没被挡掉）', async () => {
    const { wrapper, fetchMock } = await mountView({
      channels: [channel(1, '门店群')],
      topics: [topic('sales_report', '销售报表', [1])],
    })
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    await wrapper.findAll('button').find((node) => node.text() === '测试').trigger('click')
    await flushPromises()

    const tests = fetchMock.calls.filter((call) => call.path.includes('/api/wecom-push/webhooks/1/test'))
    expect(tests).toHaveLength(1)
    expect(tests[0].method).toBe('POST')
  })
})

describe('订阅矩阵', () => {
  const CHANNELS = [channel(1, '门店群'), channel(2, '日报群')]
  const TOPICS = [
    topic('sales_report', '销售报表', [1]),
    topic('hygiene_photo', '验收照片', [], { contains_employee_photos: true }),
  ]

  it('渠道不超过阈值时是勾选矩阵：行=内容类型，列=渠道', async () => {
    const { wrapper } = await mountView({ channels: CHANNELS, topics: TOPICS })
    await openTab(wrapper, '订阅')

    const table = wrapper.find('table.wp-matrix')
    expect(table.exists()).toBe(true)
    const header = table.findAll('thead th').map((cell) => cell.text())
    expect(header[0]).toBe('内容类型')
    expect(header.slice(1).join('|')).toContain('门店群')
    expect(header.slice(1).join('|')).toContain('日报群')
    expect(table.findAll('tbody tr')).toHaveLength(2)
  })

  it('勾选某个内容类型给某个渠道：请求体带 topic / channel / enabled', async () => {
    const { wrapper, fetchMock } = await mountView({ channels: CHANNELS, topics: TOPICS })
    await openTab(wrapper, '订阅')

    // 第一行（销售报表）里「日报群」那一格的勾选框：当前没勾 → 点一下就是勾上
    const cells = wrapper.find('table.wp-matrix').findAll('tbody tr')[0].findAll('td')
    await cells[2].find('button').trigger('click')
    await flushPromises()

    const post = fetchMock.calls.find((call) => call.method === 'POST')
    expect(post, '勾选没有发出请求').toBeTruthy()
    expect(post.path).toContain('/api/wecom-push/subscriptions')
    expect(JSON.parse(post.body)).toMatchObject({
      topic_id: 'sales_report',
      target_channel_id: 2,
      enabled: true,
    })
  })

  // U3：每格只有一个 16×16 的勾选框、`<td>` 自己不可点 —— 64 格 × 3 档实测 192 次点空。
  it('点格子空白处就算勾选（整格是热区），且只切一下', async () => {
    const { wrapper, fetchMock } = await mountView({ channels: CHANNELS, topics: TOPICS })
    await openTab(wrapper, '订阅')

    const cell = wrapper.find('table.wp-matrix').findAll('tbody tr')[0].findAll('td')[2]
    // 直接点 `<td>`（真实鼠标点在勾选框旁边的空白处就是这一路）
    await cell.trigger('click')
    await flushPromises()

    const posts = fetchMock.calls.filter((call) => call.method === 'POST')
    expect(posts).toHaveLength(1)
    expect(JSON.parse(posts[0].body)).toMatchObject({
      topic_id: 'sales_report', target_channel_id: 2, enabled: true,
    })
    // 再点一次：**一次点击只切一下**（td 接管 + 勾选框自己那一路会双触发的话，
    // 这里一次点击就会发出两条相反的请求）
    await cell.trigger('click')
    await flushPromises()
    expect(fetchMock.calls.filter((call) => call.method === 'POST')).toHaveLength(2)
  })

  it('点在勾选框本身也只切一下（不会 td + 勾选框各切一次）', async () => {
    const { wrapper, fetchMock } = await mountView({ channels: CHANNELS, topics: TOPICS })
    await openTab(wrapper, '订阅')

    const box = wrapper.find('table.wp-matrix').findAll('tbody tr')[0].findAll('td')[2]
      .find('button[role=checkbox]')
    await box.trigger('click')
    await flushPromises()

    expect(fetchMock.calls.filter((call) => call.method === 'POST')).toHaveLength(1)
  })

  it('勾选框是键盘可达的原生按钮，并带程序化名称（U3 / U10）', async () => {
    const { wrapper } = await mountView({ channels: CHANNELS, topics: TOPICS })
    await openTab(wrapper, '订阅')

    const box = wrapper.find('table.wp-matrix').findAll('tbody tr')[0].findAll('td')[2]
      .find('button[role=checkbox]')
    expect(box.element.tagName).toBe('BUTTON')
    expect(box.attributes('tabindex')).not.toBe('-1')
    // 读屏读得出"哪一类内容发给哪个群"，而不是光一句「复选框」
    expect(box.attributes('aria-label')).toBe('销售报表 发给 日报群')
    // 命中区 ≥44×44（真正接点击的是这一层）
    const cell = wrapper.find('table.wp-matrix').findAll('tbody tr')[0].findAll('td')[2]
    expect(cell.find('.wp-matrix-hit').exists()).toBe(true)
  })

  it('零订阅的内容类型：行高亮 + 页顶提示条', async () => {
    const { wrapper } = await mountView({ channels: CHANNELS, topics: TOPICS })
    await openTab(wrapper, '订阅')

    const rows = wrapper.find('table.wp-matrix').findAll('tbody tr')
    expect(rows[1].classes()).toContain('wp-matrix-zero')
    expect(rows[0].classes()).not.toContain('wp-matrix-zero')

    const banner = wrapper.findAll('.dash-error-banner').map((node) => node.text()).join('\n')
    expect(banner).toContain('验收照片')
    expect(banner).toContain('不会发出')
  })

  it('卫生类内容所在行标注「含员工实拍照片」', async () => {
    const { wrapper } = await mountView({ channels: CHANNELS, topics: TOPICS })
    await openTab(wrapper, '订阅')

    const rows = wrapper.find('table.wp-matrix').findAll('tbody tr')
    expect(rows[1].text()).toContain('含员工实拍照片')
    expect(rows[0].text()).not.toContain('含员工实拍照片')
  })

  it('渠道超过 8 个自动切成「先选内容、再勾群」的多选列表', async () => {
    const many = Array.from({ length: 9 }, (_, index) => channel(index + 1, `群 ${index + 1}`))
    const { wrapper, fetchMock } = await mountView({
      channels: many,
      topics: [topic('sales_report', '销售报表', [1])],
    })
    await openTab(wrapper, '订阅')

    expect(wrapper.find('table.wp-matrix').exists()).toBe(false)
    const picks = wrapper.findAll('.wp-pick-row')
    expect(picks).toHaveLength(1)
    expect(picks[0].text()).toContain('销售报表')

    // 先选内容 → 再勾群 → 保存：只发**变化**的那几条（原本只有 1 勾着）
    await picks[0].trigger('click')
    await flushPromises()
    const rows = wrapper.findAll('.luyun-check-row')
    expect(rows.length).toBe(9)
    await rows[1].find('button[role=checkbox]').trigger('click')
    await flushPromises()

    const save = wrapper.findAll('button').find((node) => node.text() === '保存订阅')
    await save.trigger('click')
    await flushPromises()

    const posts = fetchMock.calls.filter((call) => call.method === 'POST')
    expect(posts).toHaveLength(1)
    expect(JSON.parse(posts[0].body)).toMatchObject({
      topic_id: 'sales_report', target_channel_id: 2, enabled: true,
    })
  })
})

// D1 的现场：渠道 10 个（> 8）时列表形态，读接口的行是 `channels: [{id, enabled}]`，
// 页面却对它做了 `Number(对象)` ⇒ NaN ⇒ 10 个渠道全未勾（同一行徽章却写「1 个渠道」），
// 取消勾选发出去的是 `target_channel_id: null` ⇒ 422，而复选框已经弹回未勾。
// 这一组用例钉的是**渲染出来的勾选态**与**页面发出去的请求体**。
describe('多选列表（渠道 > 8）的勾选与保存反馈', () => {
  const MANY = Array.from({ length: 9 }, (_, index) => channel(index + 1, `群 ${index + 1}`))
  // 「卫生提醒」订了「群 3」（读接口给的是 `[{id: 3, enabled: true}]`）
  const TOPICS = [topic('hygiene_reminder', '卫生提醒', [3])]

  async function openPicker(wrapper) {
    await openTab(wrapper, '订阅')
    await wrapper.findAll('.wp-pick-row')[0].trigger('click')
    await flushPromises()
  }

  function checkboxes(wrapper) {
    return wrapper.findAll('.luyun-check-row').map(
      (row) => row.find('button[role=checkbox]').attributes('aria-checked'),
    )
  }

  async function save(wrapper) {
    const button = wrapper.findAll('button').find((node) => node.text() === '保存订阅')
    expect(button, '找不到「保存订阅」').toBeTruthy()
    await button.trigger('click')
    await flushPromises()
  }

  it('已有订阅显示为已勾（与同一行的「N 个渠道」徽章说的是同一件事）', async () => {
    const { wrapper } = await mountView({ channels: MANY, topics: TOPICS })
    await openPicker(wrapper)

    expect(wrapper.findAll('.wp-pick-row')[0].text()).toContain('1 个渠道')
    const checked = checkboxes(wrapper)
    expect(checked).toHaveLength(9)
    expect(checked[2]).toBe('true')
    expect(checked.filter((value) => value === 'true')).toHaveLength(1)
  })

  it('「订了但渠道停着」不算已勾：徽章与勾选数用同一份判据', async () => {
    const many = MANY.map((item, index) => (index === 1 ? { ...item, enabled: false } : item))
    const { wrapper } = await mountView({
      channels: many,
      topics: [topic('hygiene_reminder', '卫生提醒', [], {
        // 群 2 停着（订阅保留、投递跳过），群 3 正常
        channels: [{ id: 2, enabled: false }, { id: 3, enabled: true }],
      })],
    })
    await openPicker(wrapper)

    expect(wrapper.findAll('.wp-pick-row')[0].text()).toContain('1 个渠道')
    expect(checkboxes(wrapper).filter((value) => value === 'true')).toHaveLength(1)
    expect(checkboxes(wrapper)[2]).toBe('true')
    expect(checkboxes(wrapper)[1]).toBe('false')
  })

  it('取消勾选发出的是带真实渠道 id 的停用请求', async () => {
    const { wrapper, fetchMock } = await mountView({ channels: MANY, topics: TOPICS })
    await openPicker(wrapper)

    // 取消「群 3」这一勾 → 保存：请求体必须是停用**渠道 3**，不是 target_channel_id: null
    await wrapper.findAll('.luyun-check-row')[2].find('button[role=checkbox]').trigger('click')
    await flushPromises()
    await save(wrapper)

    const posts = fetchMock.calls.filter((call) => call.method === 'POST')
    expect(posts).toHaveLength(1)
    expect(JSON.parse(posts[0].body)).toEqual({
      topic_id: 'hygiene_reminder', enabled: false, target_channel_id: 3,
    })
    expect(wrapper.text()).toContain('停用 1 个渠道')
  })

  it('没有差异时不发请求，也不假报「已保存」', async () => {
    const { wrapper, fetchMock } = await mountView({ channels: MANY, topics: TOPICS })
    await openPicker(wrapper)

    await save(wrapper)

    expect(fetchMock.calls.filter((call) => call.method === 'POST')).toEqual([])
    expect(wrapper.text()).toContain('没有改动，无需保存')
    expect(wrapper.text()).not.toContain('订阅已保存')
  })

  it('保存失败：红条说明失败、不假报成功，勾选态也不弹回', async () => {
    const { wrapper, fetchMock } = await mountView({
      channels: MANY, topics: TOPICS, writeStatus: 422,
    })
    await openPicker(wrapper)

    // 勾一个原本没订的渠道（群 1）→ 保存被后端拒绝
    await wrapper.findAll('.luyun-check-row')[0].find('button[role=checkbox]').trigger('click')
    await flushPromises()
    await save(wrapper)

    expect(fetchMock.calls.filter((call) => call.method === 'POST')).toHaveLength(1)
    expect(wrapper.text()).toContain('数据验证失败')
    expect(wrapper.text()).not.toContain('订阅已保存')
    // 店长刚点的勾还在（悄悄弹回去 = 让人以为改好了）
    expect(checkboxes(wrapper)[0]).toBe('true')
  })
})

describe('接口版本提示条', () => {
  it('对得上就不出现；对不上则出现且不阻断操作', async () => {
    const { wrapper } = await mountView({
      channels: [channel(1, '门店群')],
      topics: [topic('sales_report', '销售报表')],
    })
    expect(wrapper.text()).not.toContain('接口版本不一致')

    const stale = probeFetch({ channels: [], topics: [] })
    // 这一档让 /meta 报一个与前端常量不同的版本
    vi.stubGlobal('fetch', vi.fn(async (url, options = {}) => {
      if (String(url).includes('/api/wecom-push/meta')) {
        return jsonResponse({ success: true, api_version: 'v0', job_templates: [] })
      }
      return stale(url, options)
    }))
    const pinia = createPinia()
    setActivePinia(pinia)
    const second = mount(WecomPushView, { global: { plugins: [pinia] } })
    await flushPromises()
    await flushPromises()

    expect(second.text()).toContain('接口版本不一致')
    // 不阻断：表单与按钮照旧在
    expect(second.find('button[type="submit"]').exists()).toBe(true)
  })
})

// U13：渠道 > 8 走「先选内容、再勾渠道」的多选列表；内容类型为 0 时页面写着
// 「10 个渠道 · 0 类内容」+「先选一类内容，再勾渠道。」—— 让店长去选一个不存在的东西。
// 矩阵分支有对应空态（「还没有渠道。先到『渠道』tab 新增一个。」），多选分支漏了。
describe('订阅多选列表在 0 类内容时的空态（U13）', () => {
  const TEN = Array.from({ length: 10 }, (_, index) => channel(index + 1, `群 ${index + 1}`))

  it('没有内容类型时给空态，不再让人"先选一类内容"', async () => {
    const { wrapper } = await mountView({ channels: TEN, topics: [] })
    await openTab(wrapper, '订阅')

    expect(wrapper.text()).toContain('注册表里还没有内容类型')
    expect(wrapper.text()).not.toContain('先选一类内容，再勾渠道。')
    // 一个可点的内容类型都没有，就不该渲染那一列按钮
    expect(wrapper.findAll('button.wp-pick-row')).toHaveLength(0)
  })

  it('有内容类型时多选列表照旧（空态不能把正常形态顶掉）', async () => {
    const { wrapper } = await mountView({
      channels: TEN, topics: [topic('sales_report', '销售报表', [1])],
    })
    await openTab(wrapper, '订阅')

    expect(wrapper.findAll('button.wp-pick-row')).toHaveLength(1)
    expect(wrapper.text()).toContain('先选一类内容，再勾渠道。')
    expect(wrapper.text()).not.toContain('注册表里还没有内容类型')
  })
})

// U15：群组卡把**全部渠道**平铺成勾选行（3 群组 × 10 渠道 = 30 行，1440 下整页 2588px），
// 而"这个群有哪些渠道"正是这一页要看的东西。默认只列已勾选的，其余折进「展开全部 N 个渠道」。
describe('渠道群组卡的成员折叠（U15）', () => {
  const THREE = [channel(1, '一楼前厅群'), channel(2, '店长日报群'), channel(3, '卫生群')]
  const GROUPS = [
    { id: 1, name: '日报群组', enabled: true, notes: '', member_channel_ids: [1] },
    { id: 2, name: '空群组', enabled: true, notes: '', member_channel_ids: [] },
  ]

  /** 群组卡：卡内**直接子元素**里的标题（`strong.wp-hook-name`）正好等于群组名。
   *
   * 两个坑都在定位上：①按"卡里出现过这个名字"找会先命中渠道表单那张卡（它的
   * 「所属群组（可多选）」列着全部群组名）；②`find('.wp-hook-head')` 是后代查询，
   * 外层「渠道群组」大卡也会命中（它包着所有群组卡 + 新增表单），于是成员行里混进
   * 表单那一行「启用」。`:scope >` 只认自己的标题。 */
  function groupCard(wrapper, name) {
    const card = wrapper.findAll('.card').find((node) => {
      const head = node.element.querySelector(':scope > .wp-hook-head')
      const title = head && head.querySelector('strong.wp-hook-name')
      return !!title && title.textContent.trim() === name
    })
    expect(card, `找不到群组卡「${name}」`).toBeTruthy()
    return card
  }

  function expandButton(card) {
    return card.findAll('button').find((node) => node.text().startsWith('展开全部'))
  }

  it('默认只列已勾选的成员，其余折进「展开全部 N 个渠道」', async () => {
    const { wrapper } = await mountView({ channels: THREE, topics: [], groups: GROUPS })
    const card = groupCard(wrapper, '日报群组')

    const rows = card.findAll('label.luyun-check-row')
    expect(rows).toHaveLength(1)
    expect(rows[0].text()).toContain('一楼前厅群')
    expect(expandButton(card).text()).toBe('展开全部 3 个渠道')
  })

  it('展开后列出全部渠道，还能收回去', async () => {
    const { wrapper } = await mountView({ channels: THREE, topics: [], groups: GROUPS })

    await expandButton(groupCard(wrapper, '日报群组')).trigger('click')
    await flushPromises()
    expect(groupCard(wrapper, '日报群组').findAll('label.luyun-check-row')).toHaveLength(3)
    // 展开按钮自己变成「收起」，否则展开了就收不回去
    const collapse = groupCard(wrapper, '日报群组').findAll('button')
      .find((node) => node.text() === '收起')
    expect(collapse).toBeTruthy()

    await collapse.trigger('click')
    await flushPromises()
    expect(groupCard(wrapper, '日报群组').findAll('label.luyun-check-row')).toHaveLength(1)
  })

  it('一个成员都没有的群组不留一排空行，只写一句并给展开入口', async () => {
    const { wrapper } = await mountView({ channels: THREE, topics: [], groups: GROUPS })
    const card = groupCard(wrapper, '空群组')

    expect(card.findAll('label.luyun-check-row')).toHaveLength(0)
    expect(card.text()).toContain('未勾选任何渠道')
    expect(expandButton(card)).toBeTruthy()
  })

  it('折叠只是显示：勾一个没列出来的渠道照样发请求（成员关系没被折叠影响）', async () => {
    const { wrapper, fetchMock } = await mountView({
      channels: THREE, topics: [], groups: GROUPS,
    })

    await expandButton(groupCard(wrapper, '日报群组')).trigger('click')
    await flushPromises()
    // 展开后第 2 行是「店长日报群」（不属于这个群组）
    const row = groupCard(wrapper, '日报群组').findAll('label.luyun-check-row')[1]
    expect(row.text()).toContain('店长日报群')
    await row.find('button[role=checkbox]').trigger('click')
    await flushPromises()

    const writes = fetchMock.calls.filter((call) => call.method !== 'GET')
    expect(writes).toHaveLength(1)
    expect(writes[0].path).toContain('/channel-groups/1/members')
  })
})
