// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import HygieneRosterView from '../HygieneRosterView.vue'
import { ADMIN_CAP_STAFF_DEFS } from '../../../utils/adminCaps'

/**
 * 花名册改版（2026-10）的交互面：列表分区与分段筛选、行内标签、编辑抽屉、
 * 批准门槛、导出参数。
 *
 * 数据前提照 design §11 那张表造：一个待批准且待补（陈晓）、一个待批准但不待补（李娜）、
 * 一个临期（王强）、一个空档案（赵敏）、一个过期（孙平）、一个已停用（周伟）。
 */

function person(overrides = {}) {
  return {
    id: 1,
    name: '张三',
    phone: '13800000000',
    job_title: '案板',
    permission: '普通员工',
    admin_caps: [],
    approved: true,
    disabled: false,
    created_at: '2026-10-01T09:00:00+08:00',
    id_card_no: '110101199003077213',
    health_cert_date: '2025-12-01',
    health_cert_expires_on: '2026-12-01',
    health_cert_state: 'ok',
    health_cert_days_left: 60,
    base_salary: 6000,
    hire_date: '2026-01-01',
    profile_incomplete: false,
    ...overrides,
  }
}

const 陈晓 = person({
  id: 1, name: '陈晓', phone: '13800000001',
  created_at: '2026-10-02T09:00:00+08:00',
  approved: false, base_salary: null, hire_date: null, profile_incomplete: true,
})
const 李娜 = person({
  id: 2, name: '李娜', phone: '13800000002',
  created_at: '2026-10-03T09:00:00+08:00',
  approved: false, profile_incomplete: false,
})
const 王强 = person({
  id: 3, name: '王强', phone: '13800000003',
  health_cert_state: 'soon', health_cert_expires_on: '2026-10-25', health_cert_days_left: 20,
})
const 赵敏 = person({
  id: 4, name: '赵敏', phone: '13800000004',
  id_card_no: '', health_cert_date: '', health_cert_expires_on: '', health_cert_state: 'none',
  base_salary: null, hire_date: null, profile_incomplete: true,
})
const 孙平 = person({
  id: 5, name: '孙平', phone: '13800000005',
  health_cert_state: 'expired', health_cert_expires_on: '2026-09-01', health_cert_days_left: -40,
})
const 周伟 = person({
  id: 6, name: '周伟', phone: '13800000006', disabled: true,
  id_card_no: '', health_cert_date: '', health_cert_expires_on: '', health_cert_state: 'none',
  base_salary: null, hire_date: null, profile_incomplete: true,
})

let rows = []
let requests = []
/** 写接口的应答：默认成功，可改成失败（验"服务端原话透出来"）。 */
let writeReply = null

function jsonResponse(data, status = 200, headers = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': 'application/json', ...headers }),
    json: async () => data,
    blob: async () => new Blob(['ok']),
  }
}

beforeEach(() => {
  rows = [陈晓, 李娜, 王强, 赵敏, 孙平, 周伟]
  requests = []
  writeReply = null
  vi.stubGlobal('URL', {
    ...URL,
    createObjectURL: vi.fn(() => 'blob:mock'),
    revokeObjectURL: vi.fn(),
  })
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  vi.stubGlobal('fetch', vi.fn(async (url, options = {}) => {
    const raw = String(url)
    const path = raw.split('?')[0]
    const method = (options.method || 'GET').toUpperCase()
    requests.push({ raw, path, method, body: options.body ? JSON.parse(options.body) : null })
    if (method !== 'GET') {
      if (writeReply) return writeReply
      return jsonResponse({ employee: { ...李娜 } })
    }
    if (path === '/api/hygiene/admin/roster') return jsonResponse({ employees: rows, zones: [] })
    if (path === '/api/hygiene/admin/roster-export.csv') {
      return jsonResponse({}, 200, { 'content-disposition': 'attachment; filename="roster-2026-10-07.csv"' })
    }
    return jsonResponse({})
  }))
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

async function mountRoster() {
  const wrapper = mount(HygieneRosterView)
  await flushPromises()
  await flushPromises()
  return wrapper
}

function rowButton(wrapper, name) {
  return wrapper.findAll('.roster-row').find((node) => node.text().includes(name))
}

function button(wrapper, label) {
  return wrapper.findAll('button').find((node) => node.text() === label)
}

async function openDrawer(wrapper, name) {
  const row = rowButton(wrapper, name)
  expect(row, `${name} 那一行`).toBeTruthy()
  await row.trigger('click')
  await flushPromises()
}

describe('花名册 · 列表分区与分段筛选', () => {
  it('默认「全部」：待批准置顶独立分区，已停用默认隐藏', async () => {
    const wrapper = await mountRoster()

    const headers = wrapper.findAll('.table-card-header h3').map((node) => node.text())
    expect(headers[0]).toContain('待批准')
    expect(headers[1]).toContain('在职')
    expect(headers.join(' ')).not.toContain('已停用')

    // 待批准那一段在最上面：前两行就是那两个人（注册时间升序）。
    const names = wrapper.findAll('.roster-row .roster-person-copy strong').map((node) => node.text())
    expect(names[0]).toBe('陈晓')
    expect(names[1]).toBe('李娜')
    // 已停用的周伟一个都不在。
    expect(wrapper.text()).not.toContain('周伟')
  })

  it('分段「待补 N」只数未停用且档案缺项的人，「已停用 N」是全部停用的人', async () => {
    const wrapper = await mountRoster()

    const segs = wrapper.findAll('.roster-seg-item').map((node) => node.text())
    // 待补：陈晓 + 赵敏（周伟停用了，不算）；已停用：周伟 1 个；全部不显示数字。
    expect(segs[0]).toBe('全部')
    expect(segs[1]).toBe('待补 2')
    expect(segs[2]).toBe('已停用 1')

    await wrapper.findAll('.roster-seg-item')[1].trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('陈晓')
    expect(wrapper.text()).toContain('赵敏')
    expect(wrapper.text()).not.toContain('王强')

    await wrapper.findAll('.roster-seg-item')[2].trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('周伟')
    expect(wrapper.text()).not.toContain('陈晓')
  })

  it('搜索是本地过滤：命中姓名或手机号，计数不跟着变，无结果给一行空态', async () => {
    const wrapper = await mountRoster()

    await wrapper.get('.roster-search input').setValue('13800000005')
    await flushPromises()
    expect(wrapper.text()).toContain('孙平')
    expect(wrapper.text()).not.toContain('王强')
    // 计数看整份名单，不随搜索变。
    expect(wrapper.findAll('.roster-seg-item').map((node) => node.text())[1]).toBe('待补 2')

    await wrapper.get('.roster-search input').setValue('没有人')
    await flushPromises()
    expect(wrapper.get('.roster-empty').text()).toBe('没有找到匹配的人')
  })

  it('两个分区都空、名单一个人都没有时，给「还没有人注册」', async () => {
    rows = []
    const wrapper = await mountRoster()

    expect(wrapper.get('.roster-empty').text()).toBe('还没有人注册，点「邀请店员」')
    // 名单空了导出也点不动。
    expect(button(wrapper, '导出').attributes('disabled')).toBeDefined()
    expect(button(wrapper, '导出').attributes('title')).toBe('这个分组没有可导出的人')
  })
})

describe('花名册 · 列表行内容', () => {
  it('行里只有姓名、手机号、状态、派生标签、待补与健康证标签', async () => {
    const wrapper = await mountRoster()
    const html = wrapper.html()

    expect(rowButton(wrapper, '王强').text()).toContain('健康证 2026-10-25 到期')
    expect(rowButton(wrapper, '孙平').text()).toContain('健康证 2026-09-01 已过期')
    expect(rowButton(wrapper, '赵敏').text()).toContain('待补')
    expect(rowButton(wrapper, '王强').text()).toContain('普通员工 · 无')
    expect(rowButton(wrapper, '李娜').text()).toContain('待批准')
    expect(rowButton(wrapper, '王强').text()).toContain('已批准')

    // 列表行里不许出现身份证号与底薪（它们只在抽屉与导出里）。
    expect(rowButton(wrapper, '王强').html()).not.toContain('身份证号')
    expect(rowButton(wrapper, '王强').html()).not.toContain('底薪')
    expect(rowButton(wrapper, '王强').text()).not.toContain('6000')
    // 当天的班次与工作区也不再出现在这一页的任何位置。
    expect(html).not.toContain('当天班次')
    expect(html).not.toContain('当天区域')
    expect(html).not.toContain('改今天')
  })

  it('健康证正常（ok）与没办（none）都不出标签', async () => {
    const wrapper = await mountRoster()

    expect(rowButton(wrapper, '李娜').text()).not.toContain('健康证')
    expect(rowButton(wrapper, '赵敏').text()).not.toContain('健康证')
  })

  it('过期用朱砂那一档、临期用琥珀那一档', async () => {
    const wrapper = await mountRoster()

    const soon = rowButton(wrapper, '王强').findAll('.roster-chip').map((n) => n.classes()).flat()
    expect(soon).toContain('is-warn')
    const expired = rowButton(wrapper, '孙平').findAll('.roster-chip').map((n) => n.classes()).flat()
    expect(expired).toContain('is-danger')
  })
})

describe('花名册 · 编辑抽屉', () => {
  it('点行打开抽屉：11 项字段按定稿顺序，手机号与注册时间只读', async () => {
    const wrapper = await mountRoster()
    await openDrawer(wrapper, '王强')

    const drawer = wrapper.get('.roster-drawer')
    expect(drawer.get('.roster-drawer-title').text()).toBe('王强')
    const labels = drawer.findAll('.roster-field-head > span:first-child').map((node) => node.text())
    expect(labels).toEqual([
      '姓名', '职位', '手机号', '注册时间', '身份证号', '健康证办理日期',
      '底薪', '入职日期', '卫生权限',
    ])
    expect(drawer.text()).toContain('管理权限')

    // 手机号与注册时间是只读文本（不是 input）。
    expect(drawer.get('.roster-readonly.is-mono').text()).toBe('13800000003')
    expect(drawer.findAll('.roster-readonly')[1].text()).toBe('2026-10-01 09:00')
    // 有效期至来自服务端派生值。
    expect(drawer.text()).toContain('有效期至 2026-10-25')
    // 底薪与身份证号在这里（与列表行相反）。
    expect(drawer.text()).toContain('身份证号')
    expect(drawer.text()).toContain('元/月')
  })

  it('没有改动时「保存」不可点（title 说明原因），改一格就可用', async () => {
    const wrapper = await mountRoster()
    await openDrawer(wrapper, '王强')

    expect(button(wrapper, '保存').attributes('disabled')).toBeDefined()
    expect(button(wrapper, '保存').attributes('title')).toBe('没有改动')

    await wrapper.get('.roster-drawer input.input').setValue('王强强')
    await flushPromises()
    expect(button(wrapper, '保存').attributes('disabled')).toBeUndefined()
  })

  it('保存发一次 PATCH：七个键，底薪空值发 null，不含 permission', async () => {
    const wrapper = await mountRoster()
    await openDrawer(wrapper, '赵敏')

    const inputs = wrapper.get('.roster-drawer').findAll('input.input')
    // 顺序：姓名 / 职位 / 身份证号 / 健康证办理日期 / 底薪 / 入职日期
    await inputs[0].setValue('赵敏敏')
    await inputs[2].setValue('110101199003077213')
    await inputs[3].setValue('2024-05-06')
    await inputs[4].setValue('5200')
    await inputs[5].setValue('2024-06-01')
    await flushPromises()

    await button(wrapper, '保存').trigger('click')
    await flushPromises()

    const patch = requests.find((item) => item.method === 'PATCH')
    expect(patch.path).toBe('/api/hygiene/admin/roster/4')
    expect(patch.body).toEqual({
      name: '赵敏敏',
      job_title: '案板',
      id_card_no: '110101199003077213',
      health_cert_date: '2024-05-06',
      base_salary: 5200,
      hire_date: '2024-06-01',
      admin_caps: [],
    })
    // 成功反馈落在页顶那一行。
    expect(wrapper.get('.roster-saved').text()).toBe('已保存')
  })

  it('有未保存改动时，关闭要先过「放弃改动」确认框', async () => {
    const wrapper = await mountRoster()
    await openDrawer(wrapper, '王强')

    await wrapper.get('.roster-drawer input.input').setValue('王强强')
    await flushPromises()
    await wrapper.get('.roster-drawer-head button').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('放弃改动')
    // 取消 → 抽屉还在。
    await button(wrapper, '取消').trigger('click')
    await flushPromises()
    expect(wrapper.find('.roster-drawer').exists()).toBe(true)

    // 确认 → 抽屉关掉。
    await wrapper.get('.roster-drawer-head button').trigger('click')
    await flushPromises()
    await button(wrapper, '放弃').trigger('click')
    await flushPromises()
    expect(wrapper.find('.roster-drawer').exists()).toBe(false)
  })

  it('没有改动时直接关，不弹确认框', async () => {
    const wrapper = await mountRoster()
    await openDrawer(wrapper, '王强')

    await wrapper.get('.roster-drawer-head button').trigger('click')
    await flushPromises()

    expect(wrapper.find('.roster-drawer').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('放弃改动')
  })
})

describe('花名册 · 批准门槛与动作', () => {
  it('两项缺一样都不可点，旁边一行说清缺什么', async () => {
    const wrapper = await mountRoster()
    await openDrawer(wrapper, '陈晓')

    const approve = button(wrapper, '批准')
    expect(approve.attributes('disabled')).toBeDefined()
    expect(wrapper.get('.roster-drawer-foot .roster-field-note').text()).toBe('还缺底薪和入职日期')
    expect(approve.attributes('title')).toBe('还缺底薪和入职日期')
  })

  it('只缺一样时那句话跟着变', async () => {
    rows = [person({ id: 7, name: '钱多', phone: '13800000007', approved: false, base_salary: null, hire_date: '2026-02-01', profile_incomplete: true })]
    let wrapper = await mountRoster()
    await openDrawer(wrapper, '钱多')
    expect(wrapper.get('.roster-drawer-foot .roster-field-note').text()).toBe('还缺底薪')
    wrapper.unmount()

    rows = [person({ id: 8, name: '吴迪', phone: '13800000008', approved: false, base_salary: 5000, hire_date: null, profile_incomplete: true })]
    wrapper = await mountRoster()
    await openDrawer(wrapper, '吴迪')
    expect(wrapper.get('.roster-drawer-foot .roster-field-note').text()).toBe('还缺入职日期')
    wrapper.unmount()
  })

  it('两项齐备：批准可点、门槛那一行不显示，点下去发 approve', async () => {
    const wrapper = await mountRoster()
    await openDrawer(wrapper, '李娜')

    expect(wrapper.find('.roster-drawer-foot .roster-field-note').exists()).toBe(false)
    const approve = button(wrapper, '批准')
    expect(approve.attributes('disabled')).toBeUndefined()

    await approve.trigger('click')
    await flushPromises()

    const post = requests.find((item) => item.method === 'POST')
    expect(post.path).toBe('/api/hygiene/admin/roster/2/approve')
    // 批准成功后关抽屉，反馈落在页顶（带姓名）。
    expect(wrapper.find('.roster-drawer').exists()).toBe(false)
    expect(wrapper.get('.roster-saved').text()).toBe('已批准 李娜')
  })

  it('原地补上两项后，批准当场变可用（先把草稿落地再批）', async () => {
    const wrapper = await mountRoster()
    await openDrawer(wrapper, '陈晓')

    expect(button(wrapper, '批准').attributes('disabled')).toBeDefined()

    const inputs = wrapper.get('.roster-drawer').findAll('input.input')
    await inputs[4].setValue('6000')
    await inputs[5].setValue('2026-09-01')
    await flushPromises()

    expect(button(wrapper, '批准').attributes('disabled')).toBeUndefined()
    expect(wrapper.find('.roster-drawer-foot .roster-field-note').exists()).toBe(false)

    await button(wrapper, '批准').trigger('click')
    await flushPromises()

    // 草稿先 PATCH 落地，再 POST 批准：服务端按库里的值判门槛，少这一步必然 400。
    const methods = requests.filter((item) => item.method !== 'GET').map((item) => item.method)
    expect(methods).toEqual(['PATCH', 'POST'])
  })

  it('批准被服务端拒了：原话显示在抽屉里，抽屉不关、草稿还在', async () => {
    writeReply = jsonResponse({ detail: '底薪与入职日期补齐后才能批准' }, 400)
    const wrapper = await mountRoster()
    await openDrawer(wrapper, '李娜')

    await button(wrapper, '批准').trigger('click')
    await flushPromises()

    expect(wrapper.get('.roster-drawer .roster-error').text()).toBe('底薪与入职日期补齐后才能批准')
    expect(wrapper.find('.roster-drawer').exists()).toBe(true)
  })

  it('停用要先过确认框，确认后关抽屉、页顶报一行', async () => {
    const wrapper = await mountRoster()
    await openDrawer(wrapper, '王强')

    await button(wrapper, '停用').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('停用员工')
    expect(wrapper.text()).toContain('停用 王强 后不能登录，记录还在。')

    // 确认框里那颗「停用」是最后一颗。
    const confirms = wrapper.findAll('button').filter((node) => node.text() === '停用')
    await confirms[confirms.length - 1].trigger('click')
    await flushPromises()

    const post = requests.find((item) => item.method === 'POST')
    expect(post.path).toBe('/api/hygiene/admin/roster/3/disable')
    expect(wrapper.get('.roster-saved').text()).toBe('已停用 王强')
  })

  it('已停用的人给「启用」，且不看批准门槛', async () => {
    const wrapper = await mountRoster()
    await wrapper.findAll('.roster-seg-item')[2].trigger('click')
    await flushPromises()
    await openDrawer(wrapper, '周伟')

    expect(button(wrapper, '停用')).toBeUndefined()
    expect(wrapper.find('.roster-drawer-foot .roster-field-note').exists()).toBe(false)

    await button(wrapper, '启用').trigger('click')
    await flushPromises()

    const post = requests.find((item) => item.method === 'POST')
    expect(post.path).toBe('/api/hygiene/admin/roster/6/enable')
    expect(wrapper.get('.roster-saved').text()).toBe('已启用 周伟')
  })
})

describe('花名册 · 导出', () => {
  it('请求带上当前分段与搜索词（所见即所得），下载文件名走服务端', async () => {
    const wrapper = await mountRoster()

    await wrapper.findAll('.roster-seg-item')[1].trigger('click')
    await wrapper.get('.roster-search input').setValue('赵')
    await flushPromises()

    await button(wrapper, '导出').trigger('click')
    await flushPromises()

    const call = requests.find((item) => item.path === '/api/hygiene/admin/roster-export.csv')
    expect(call).toBeTruthy()
    expect(call.raw).toContain('filter=missing')
    expect(call.raw).toContain('q=%E8%B5%B5')
    expect(URL.createObjectURL).toHaveBeenCalled()
  })

  it('默认分段导出 all，搜索框空时不带 q', async () => {
    const wrapper = await mountRoster()

    await button(wrapper, '导出').trigger('click')
    await flushPromises()

    const call = requests.find((item) => item.path === '/api/hygiene/admin/roster-export.csv')
    expect(call.raw).toBe('/api/hygiene/admin/roster-export.csv?filter=all')
  })

  it('导出失败时页顶显示服务端原话', async () => {
    const wrapper = await mountRoster()
    vi.stubGlobal('fetch', vi.fn(async (url) => {
      const path = String(url).split('?')[0]
      if (path === '/api/hygiene/admin/roster') return jsonResponse({ employees: rows })
      if (path === '/api/hygiene/admin/roster-export.csv') {
        return jsonResponse({ detail: '这个分组没有可导出的人' }, 400)
      }
      return jsonResponse({})
    }))

    await button(wrapper, '导出').trigger('click')
    await flushPromises()

    expect(wrapper.get('.roster-error').text()).toBe('这个分组没有可导出的人')
  })
})

describe('花名册 · 工具栏', () => {
  it('标题行只有「花名册」+ 搜索 + 分段 + 邀请店员 + 导出，没有 eyebrow / 副标题 / 刷新', async () => {
    const wrapper = await mountRoster()
    const head = wrapper.get('.roster-head')

    expect(head.findAll('h1')).toHaveLength(1)
    expect(head.get('h1').text()).toBe('花名册')
    expect(head.findAll('p')).toHaveLength(0)
    expect(head.find('.rule-help').exists()).toBe(false)
    expect(head.find('.hy-eyebrow').exists()).toBe(false)
    expect(button(wrapper, '刷新')).toBeUndefined()
    expect(button(wrapper, '邀请店员')).toBeTruthy()
    expect(button(wrapper, '导出')).toBeTruthy()
  })

  it('「邀请店员」弹层：二维码 + 链接 + 复制按钮 + 一行说明', async () => {
    const wrapper = await mountRoster()

    await button(wrapper, '邀请店员').trigger('click')
    await flushPromises()

    const box = wrapper.get('.modal-box')
    expect(box.get('canvas').attributes('aria-label')).toBe('员工入口二维码')
    expect(box.get('code').text()).toContain('/workbench/me/today')
    expect(button(wrapper, '复制链接')).toBeTruthy()
    expect(box.get('.editor-lead').text()).toBe('扫这个码自助注册，批准后登录')
  })
})
