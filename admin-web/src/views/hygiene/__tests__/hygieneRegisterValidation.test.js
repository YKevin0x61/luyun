// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'

import HygieneRegisterView from '../HygieneRegisterView.vue'

// 员工自助注册的校验（审查条目 D12 / D18）。
//
// D12：表单原来只有原生 HTML5 校验（`required` / `pattern` / `minlength`），提交时浏览器
// 先弹**英文**提示（`Please match the requested format.` / `Please lengthen this text to
// 8 characters or more`），与整页中文打架，而且原生校验先拦下来，服务端备好的中文永远
// 走不到。现在 `novalidate` + 自己那份中文判据。
//
// 这里压三件事：
//   1. 每一类错误都是**中文**、且与服务端 `_ERROR_DETAILS` 逐字一致；
//   2. 非法输入**不发请求**（校验在客户端拦下，别让服务端再报一遍同样的错）；
//   3. D18：用户改过输入之后，上一次的提交级错误要清掉。
//
// 组件里那份判据是纯函数（`validateRegistration`），但它是 `<script setup>` 的局部
// 函数，外部 import 不到 —— 所以从**行为**上压：填表、提交、看页面显示什么、发没发请求。

function jsonResponse(data, { ok = true, status = 200 } = {}) {
  return {
    ok,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => data,
  }
}

async function mountRegister() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/register', component: { template: '<div />' } },
      { path: '/login', component: { template: '<div />' } },
    ],
  })
  await router.push('/register')
  await router.isReady()
  const wrapper = mount(HygieneRegisterView, { global: { plugins: [router] } })
  await flushPromises()
  return { wrapper, router }
}

/** 依次填六格并提交。传 `null` 的格子不动（留空）。 */
async function submitWith(wrapper, { name, phone, idCard, healthCert, password, confirm }) {
  const fill = async (selector, value) => {
    if (value === null) return
    const input = wrapper.get(selector)
    await input.setValue(value)
  }
  await fill('#regName', name)
  await fill('#regPhone', phone)
  await fill('#regIdCard', idCard)
  await fill('#regHealthCert', healthCert)
  await fill('#regPassword', password)
  await fill('#regConfirm', confirm)
  await wrapper.get('form').trigger('submit')
  await flushPromises()
}

function alertText(wrapper) {
  const alert = wrapper.find('[role="alert"]')
  return alert.exists() ? alert.text() : ''
}

// 身份证与手机号一样是**必填**（2026-10 花名册改版）：样本是校验位算得过的号，
// 校验位错的用同一个前 17 位、换掉末位（`ID_CARD_BAD`）。
const ID_CARD = '110101199003077213'
const ID_CARD_X = '11010119900307723X'
const ID_CARD_BAD = '110101199003077211'
const VALID = {
  name: '张三',
  phone: '13800138000',
  idCard: ID_CARD,
  healthCert: '2020-01-01',
  password: 'abcd1234',
  confirm: 'abcd1234',
}

let fetchMock

beforeEach(() => {
  fetchMock = vi.fn(async () => jsonResponse({ ok: true }))
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('/register 校验（D12）：中文提示，且不发请求', () => {
  it('表单不再走原生校验：novalidate + 撤掉 required/pattern/minlength', async () => {
    const { wrapper } = await mountRegister()

    // `novalidate` 是这一条的全部意义：有它浏览器才不抢着弹英文气泡。
    expect(wrapper.get('form').attributes('novalidate')).toBeDefined()
    const html = wrapper.html()
    expect(html).not.toMatch(/\srequired(\s|>)/)
    expect(html).not.toMatch(/pattern=/)
    expect(html).not.toMatch(/minlength=/)
    // 密码留给密码管理器（D17：原来整表 autocomplete="off" 会把刚设的密码丢掉）。
    expect(wrapper.get('#regPassword').attributes('autocomplete')).toBe('new-password')
    expect(wrapper.get('form').attributes('autocomplete')).toBeUndefined()
  })

  it('姓名空 → 中文「请填写员工姓名」，且一个请求都不发', async () => {
    const { wrapper } = await mountRegister()

    await submitWith(wrapper, { ...VALID, name: '   ' })

    expect(alertText(wrapper)).toBe('请填写员工姓名')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('手机号 10 位 → 中文「请输入有效的中国大陆手机号」（不是英文 format 提示）', async () => {
    const { wrapper } = await mountRegister()

    await submitWith(wrapper, { ...VALID, phone: '1380013800' })

    expect(alertText(wrapper)).toBe('请输入有效的中国大陆手机号')
    expect(alertText(wrapper)).not.toMatch(/Please|format/i)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('手机号段不合法（12 开头）也一样拦下', async () => {
    const { wrapper } = await mountRegister()

    await submitWith(wrapper, { ...VALID, phone: '12800138000' })

    expect(alertText(wrapper)).toBe('请输入有效的中国大陆手机号')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('身份证空 → 中文「请填写身份证号」，且一个请求都不发', async () => {
    const { wrapper } = await mountRegister()

    await submitWith(wrapper, { ...VALID, idCard: '   ' })

    expect(alertText(wrapper)).toBe('请填写身份证号')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('身份证位数不够 → 「身份证号应为 18 位」', async () => {
    const { wrapper } = await mountRegister()

    await submitWith(wrapper, { ...VALID, idCard: '11010119900307721' })

    expect(alertText(wrapper)).toBe('身份证号应为 18 位')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('身份证校验位不对 → 「身份证号校验位不对」', async () => {
    const { wrapper } = await mountRegister()

    await submitWith(wrapper, { ...VALID, idCard: ID_CARD_BAD })

    expect(alertText(wrapper)).toBe('身份证号校验位不对')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('末位小写 x 归一成大写再提交（服务端同一口径）', async () => {
    const { wrapper } = await mountRegister()

    await submitWith(wrapper, { ...VALID, idCard: '11010119900307723x' })

    const call = fetchMock.mock.calls.find(([url]) => String(url).includes('/api/hygiene/staff/register'))
    expect(call).toBeTruthy()
    expect(JSON.parse(call[1].body).id_card_no).toBe(ID_CARD_X)
  })

  it('健康证办理日期空 → 「请选择健康证办理日期」', async () => {
    const { wrapper } = await mountRegister()

    await submitWith(wrapper, { ...VALID, healthCert: '' })

    expect(alertText(wrapper)).toBe('请选择健康证办理日期')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('健康证办理日期晚于今天 → 「健康证办理日期不能是将来」', async () => {
    const { wrapper } = await mountRegister()

    await submitWith(wrapper, { ...VALID, healthCert: '2999-01-01' })

    expect(alertText(wrapper)).toBe('健康证办理日期不能是将来')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('校验顺序与服务端一致：姓名 → 手机号 → 身份证 → 健康证 → 密码长度 → 两次一致', async () => {
    const { wrapper } = await mountRegister()

    // 手机号坏 + 身份证空：先报手机号。
    await submitWith(wrapper, { ...VALID, phone: '138', idCard: '' })
    expect(alertText(wrapper)).toBe('请输入有效的中国大陆手机号')

    // 身份证坏 + 健康证空：先报身份证。
    await submitWith(wrapper, { ...VALID, idCard: ID_CARD_BAD, healthCert: '' })
    expect(alertText(wrapper)).toBe('身份证号校验位不对')

    // 健康证空 + 密码太短：先报健康证（实名两项排在密码之前）。
    await submitWith(wrapper, { ...VALID, healthCert: '', password: 'abc', confirm: 'abc' })
    expect(alertText(wrapper)).toBe('请选择健康证办理日期')

    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('密码 7 位 → 中文「密码至少 8 位」（不是英文 lengthen 提示）', async () => {
    const { wrapper } = await mountRegister()

    await submitWith(wrapper, { ...VALID, password: 'abcd123', confirm: 'abcd123' })

    expect(alertText(wrapper)).toBe('密码至少 8 位')
    expect(alertText(wrapper)).not.toMatch(/Please|lengthen/i)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('两次不一致 → 中文「两次输入的密码不一致」，且排在长度校验之后', async () => {
    const { wrapper } = await mountRegister()

    // 两个都够长但不一致 → 报不一致。
    await submitWith(wrapper, { ...VALID, confirm: 'abcd12345' })
    expect(alertText(wrapper)).toBe('两次输入的密码不一致')
    expect(fetchMock).not.toHaveBeenCalled()

    // 短的优先报长度（与服务端同一顺序），不是先报不一致。
    await submitWith(wrapper, { ...VALID, password: 'abc', confirm: 'abcd' })
    expect(alertText(wrapper)).toBe('密码至少 8 位')
  })

  it('校验通过：发请求、成功态换成「等管理员批准」', async () => {
    const { wrapper } = await mountRegister()

    await submitWith(wrapper, VALID)

    const call = fetchMock.mock.calls.find(([url]) => String(url).includes('/api/hygiene/staff/register'))
    expect(call).toBeTruthy()
    // 六项一起发：身份证与健康证办理日期是 2026-10 花名册改版新加的**必填**两项。
    expect(JSON.parse(call[1].body)).toEqual({
      name: '张三',
      phone: '13800138000',
      id_card_no: ID_CARD,
      health_cert_date: '2020-01-01',
      password: 'abcd1234',
    })
    expect(wrapper.text()).toContain('已提交')
    expect(wrapper.text()).toContain('批准')
  })

  it('姓名与手机号提交前 trim（空格不算数）', async () => {
    const { wrapper } = await mountRegister()

    await submitWith(wrapper, { ...VALID, name: ' 李四 ', phone: ' 13900139000 ' })

    const call = fetchMock.mock.calls.find(([url]) => String(url).includes('/api/hygiene/staff/register'))
    expect(JSON.parse(call[1].body)).toMatchObject({ name: '李四', phone: '13900139000' })
  })

  it('服务端的中文错误照旧透出来（该手机号已注册）', async () => {
    fetchMock.mockImplementation(async () =>
      jsonResponse({ detail: '该手机号已注册' }, { ok: false, status: 400 }),
    )
    const { wrapper } = await mountRegister()

    await submitWith(wrapper, VALID)

    expect(alertText(wrapper)).toBe('该手机号已注册')
  })
})

describe('/register 改输入就清错（D18）', () => {
  it('提交级错误在用户改动任一格时清掉，不必再点一次提交', async () => {
    const { wrapper } = await mountRegister()

    await submitWith(wrapper, { ...VALID, confirm: 'abcd12345' })
    expect(alertText(wrapper)).toBe('两次输入的密码不一致')

    // 把确认密码改成与密码一致：提示当场消失（原来要再点一次提交才清）。
    await wrapper.get('#regConfirm').setValue('abcd1234')
    await flushPromises()

    expect(alertText(wrapper)).toBe('')
  })

  it('接口级错误也一样：改输入就清', async () => {
    fetchMock.mockImplementation(async () =>
      jsonResponse({ detail: '该手机号已注册' }, { ok: false, status: 400 }),
    )
    const { wrapper } = await mountRegister()

    await submitWith(wrapper, VALID)
    expect(alertText(wrapper)).toBe('该手机号已注册')

    await wrapper.get('#regPhone').setValue('13900139000')
    await flushPromises()

    expect(alertText(wrapper)).toBe('')
  })

  it('错误提示与表单字段关联（aria-describedby / aria-invalid），读屏读得到', async () => {
    const { wrapper } = await mountRegister()

    await submitWith(wrapper, { ...VALID, phone: '1380013800' })

    expect(wrapper.get('[role="alert"]').attributes('id')).toBe('regError')
    expect(wrapper.get('#regPhone').attributes('aria-describedby')).toBe('regError')
    expect(wrapper.get('#regPhone').attributes('aria-invalid')).toBe('true')
  })
})
