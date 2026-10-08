// @vitest-environment jsdom
import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import AccountSection from '../AccountSection.vue'
import { createSectionModalHost, SETTINGS_MODAL_HOST } from '../sectionContract'

/**
 * 账号节（原「账号与 API Token」）在 ADR 0099 下的**命名 / 文案 / 反馈契约**。
 *
 * 挂载真实组件、喂真夹具，量四件事：
 *   1. 导航与分节标题同名「账号」（壳的导航 + 本节 PanelHeader，两边一起断言）；
 *   2. 默认态可见的说明性文字 ≤ 80 字，且 Token「仅显示一次」那句原样留着；
 *   3. 修改密码 / 生成 Token / 撤销 Token / 退出登录四个写操作的反馈落在本分节内、
 *      贴着触发它的那颗按钮（不再是分节顶部一条管全场）；
 *   4. 390px 下 Token 表格只裁信息列，前缀 / 状态 / 撤销入口一个不少。
 *
 * 弹窗结构不动：Token 弹窗仍由本节 `register('token', ...)` 注册，两步确认仍走
 * `host.requestConfirm(...)` —— 这里用真的 `createSectionModalHost()` 当宿主，
 * 并把两步确认换成"记下回调再手动触发"，好把确认之后的落地动作也测到。
 */

const { api } = vi.hoisted(() => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}))

vi.mock('../../../api/client', () => ({ api }))

const replace = vi.fn(async () => {})
vi.mock('vue-router', () => ({
  useRouter: () => ({
    replace,
    currentRoute: { value: { fullPath: '/settings?section=account' } },
  }),
}))

const here = dirname(fileURLToPath(import.meta.url))
const SETTINGS_DIR = join(here, '..')
const source = readFileSync(join(SETTINGS_DIR, 'AccountSection.vue'), 'utf8')
const shell = readFileSync(join(SETTINGS_DIR, '../SetupView.vue'), 'utf8')

function compact(text) {
  return text.replace(/\s+/g, '')
}

const AUTH_STATUS = { logged_in: true, username: 'admin' }
const TOKENS = {
  tokens: [
    {
      token_hash_prefix: 'a1b2c3d4',
      label: '厨房平板-1',
      created_at: '2026-10-01T10:00:00+08:00',
      expires_at: null,
      revoked_at: null,
    },
    {
      token_hash_prefix: 'e5f6a7b8',
      label: '前台收银',
      created_at: '2026-09-01T10:00:00+08:00',
      expires_at: null,
      revoked_at: '2026-09-20T10:00:00+08:00',
    },
  ],
}

function setupApi({
  authStatus = AUTH_STATUS,
  tokens = TOKENS,
  changePasswordError = null,
  genToken = { api_token: 'luyun_demo_token' },
  genTokenError = null,
  revokeError = null,
} = {}) {
  // 撤销之后列表要跟着变（真接口就是这么返回的）：这里拿一份可变副本，
  // DELETE 成功就把那一行标成已撤销，否则"撤完按钮还在"会把用例测成假的。
  const tokenList = tokens.tokens.map((token) => ({ ...token }))
  api.get.mockImplementation(async (path) => {
    switch (path) {
      case '/api/auth/status':
        return authStatus
      case '/api/auth/tokens':
        return { tokens: tokenList }
      default:
        throw new Error(`未预期的 GET ${path}`)
    }
  })
  api.post.mockImplementation(async (path) => {
    if (path === '/api/auth/change-password') {
      if (changePasswordError) throw changePasswordError
      return { success: true }
    }
    if (path === '/api/auth/token') {
      if (genTokenError) throw genTokenError
      return genToken
    }
    throw new Error(`未预期的 POST ${path}`)
  })
  api.put.mockImplementation(async (path) => {
    throw new Error(`未预期的 PUT ${path}`)
  })
  api.delete.mockImplementation(async (path) => {
    if (revokeError) throw revokeError
    if (!path.startsWith('/api/auth/token/')) throw new Error(`未预期的 DELETE ${path}`)
    const prefix = decodeURIComponent(path.slice('/api/auth/token/'.length))
    for (const token of tokenList) {
      if (token.token_hash_prefix === prefix) token.revoked_at = '2026-10-08T10:00:00+08:00'
    }
    return { success: true }
  })
}

/** 挂载 + 一个真的弹窗宿主（壳里那份就是 `createSectionModalHost()`）。 */
async function mountSection(options) {
  setupApi(options)
  const host = createSectionModalHost()
  const confirms = []
  // 两步确认的控制器由数据节在真页面里注册；这里换成一个"记下回调再手动触发"的替身。
  host.register('confirm', {
    isOpen: () => false,
    close: () => {},
    request: (opts, onConfirm) => confirms.push({ opts, onConfirm }),
  })
  const wrapper = mount(AccountSection, {
    props: { active: true },
    global: { provide: { [SETTINGS_MODAL_HOST]: host } },
  })
  await flushPromises()
  await flushPromises()
  return { wrapper, host, confirms }
}

beforeEach(() => {
  for (const fn of [api.get, api.post, api.put, api.patch, api.delete]) fn.mockReset()
  replace.mockReset()
  replace.mockImplementation(async () => {})
})

function fieldsetByLegend(wrapper, legend) {
  return wrapper
    .findAll('fieldset')
    .find((node) => node.find('legend').exists() && node.find('legend').text() === legend)
}

function buttonByText(wrapper, text) {
  return wrapper.findAll('button').find((node) => node.text() === text)
}

const NOTE_SELECTOR = '.hint, .section-lead, .alert, .panel-header__description, .panel-header__note'

function visibleNotes(wrapper) {
  return wrapper
    .findAll(NOTE_SELECTOR)
    .map((node) => node.text().replace(/\s+/g, ''))
    .filter((text) => text.length > 0)
}

describe('命名统一为「账号」', () => {
  it('壳的导航与本节的页头标题同名', () => {
    // 导航在壳里（本节不改它，只核对两边是同一条名字）。
    expect(shell).toMatch(/id: 'account', label: '账号'/)
    expect(source).toMatch(/title="账号"/)
    // 旧那两套名字不再出现在渲染面上（文件头的「原「账号与 API Token」」是内容归属说明）。
    const template = source.slice(source.indexOf('<template>'), source.indexOf('<style'))
    expect(template).not.toContain('账号与 API Token')
    expect(template).not.toContain('账号与会话')
  })

  it('渲染出来的页头标题就是「账号」', async () => {
    const { wrapper } = await mountSection()
    expect(wrapper.get('.panel-header__title').text()).toBe('账号')
    expect(wrapper.text()).not.toContain('账号与会话')
    wrapper.unmount()
  })
})

describe('默认态：说明性文字 ≤ 80 字，Token「仅显示一次」保留', () => {
  it('默认态可见说明合计 ≤ 80 字，且只剩三句', async () => {
    const { wrapper } = await mountSection()
    const notes = visibleNotes(wrapper)
    expect(notes, `默认态可见说明：${notes.join(' | ')}`).toEqual([
      '已登录为admin。',
      '至少8位字符。',
      '生成后仅显示一次，请立即复制保存；撤销后调用方立即失效。',
    ])
    expect(notes.join('').length).toBeLessThanOrEqual(80)
    wrapper.unmount()
  })

  it('退出后的后果不在页头重复（它在两步确认里说）', async () => {
    const { wrapper, confirms } = await mountSection()
    expect(wrapper.get('.panel-header__description').text()).toBe('已登录为 admin。')
    await buttonByText(wrapper, '退出登录').trigger('click')
    expect(confirms[0].opts.message).toContain('退出后需要重新输入超级管理员密码才能回到管理页面。')
    wrapper.unmount()
  })

  it('未登录时页头照旧把 composable 的提示带出来', async () => {
    const { wrapper } = await mountSection({ authStatus: { logged_in: false } })
    expect(wrapper.get('.panel-header__description').text()).toBe('当前未登录，请刷新页面或重新登录。')
    wrapper.unmount()
  })

  it('Token 弹窗里「仅显示一次」的原文还在（明文只显示这一次）', async () => {
    const { wrapper, host } = await mountSection()
    const copy = host.token.copy()
    expect(copy.title).toBe('API Token 已生成')
    expect(copy.message).toBe('请立即复制并保存，关闭后将无法再次查看完整 Token。')
    expect(host.token.state).toBeTruthy()
    // 面板上那句提示也是默认态可见的（不是折叠里的）。
    expect(wrapper.text()).toContain('生成后仅显示一次，请立即复制保存')
    wrapper.unmount()
  })
})

describe('写操作反馈就近', () => {
  it('修改密码：结果贴在提交按钮上方，且不在分节顶部重复', async () => {
    const { wrapper } = await mountSection()
    const panel = fieldsetByLegend(wrapper, '登录密码')
    await panel.get('#oldPassword').setValue('old-pass-1')
    await panel.get('#newPassword').setValue('new-pass-1')
    await panel.get('form').trigger('submit')
    await flushPromises()

    const feedback = panel.get('.alert.feedback')
    expect(feedback.text()).toBe('密码已更新')
    expect(feedback.element.nextElementSibling.classList.contains('actions')).toBe(true)
    // 表单清空 = 真的提交过（composable 的行为没被本节改掉）。
    expect(panel.get('#oldPassword').element.value).toBe('')
    // 分节顶部那条只在退出登录时出现。
    expect(wrapper.find('.settings-section > .alert').exists()).toBe(false)
    wrapper.unmount()
  })

  it('修改密码失败也落在同一处，并留着不清屏', async () => {
    const { wrapper } = await mountSection({
      changePasswordError: new Error('旧密码不正确'),
    })
    const panel = fieldsetByLegend(wrapper, '登录密码')
    await panel.get('#oldPassword').setValue('bad-old-1')
    await panel.get('#newPassword').setValue('new-pass-1')
    await panel.get('form').trigger('submit')
    await flushPromises()
    expect(panel.get('.alert.feedback').text()).toBe('修改密码失败：旧密码不正确')
    expect(wrapper.find('.settings-section > .alert').exists()).toBe(false)
    wrapper.unmount()
  })

  it('生成 Token：成功由 Token 弹窗反馈，失败贴在「生成新 Token」上方', async () => {
    const { wrapper, host } = await mountSection()
    await wrapper.get('#tokenLabel').setValue('厨房平板-2')
    await buttonByText(wrapper, '生成新 Token').trigger('click')
    await flushPromises()
    expect(api.post).toHaveBeenCalledWith('/api/auth/token', { label: '厨房平板-2' })
    expect(host.token.isOpen()).toBe(true)
    expect(host.token.state.plaintext).toBe('luyun_demo_token')
    wrapper.unmount()

    const failing = await mountSection({ genTokenError: new Error('标签重复') })
    const tokenPanel = fieldsetByLegend(failing.wrapper, 'API Token（KDS 等设备）')
    await buttonByText(tokenPanel, '生成新 Token').trigger('click')
    await flushPromises()
    const feedback = tokenPanel.get('.alert.feedback')
    expect(feedback.text()).toBe('生成 Token 失败：标签重复')
    expect(feedback.element.nextElementSibling.classList.contains('actions')).toBe(true)
    expect(failing.wrapper.find('.settings-section > .alert').exists()).toBe(false)
    failing.wrapper.unmount()
  })

  it('撤销 Token：反馈落在被撤的那一行，按钮位换成结论', async () => {
    const { wrapper, confirms } = await mountSection()
    const rows = wrapper.findAll('.token-table tbody tr')
    const activeRow = rows.find((row) => row.text().includes('a1b2c3d4'))
    await buttonByText(activeRow, '撤销').trigger('click')

    expect(confirms).toHaveLength(1)
    expect(confirms[0].opts.title).toBe('确认撤销 API Token')
    expect(confirms[0].opts.checkboxes[0].required).toBe(true)

    await confirms[0].onConfirm()
    await flushPromises()
    expect(api.delete).toHaveBeenCalledWith('/api/auth/token/a1b2c3d4')

    const feedbackRow = wrapper.get('.token-feedback-row')
    expect(feedbackRow.get('.alert.feedback').text()).toBe('Token 已撤销')
    // 就在被撤那一行的正下方，且原「撤销」按钮已经不在那一行里。
    const revokedRow = wrapper.findAll('.token-table tbody tr').find((row) => row.text().includes('a1b2c3d4'))
    expect(revokedRow.element.nextElementSibling).toBe(feedbackRow.element)
    expect(buttonByText(revokedRow, '撤销')).toBeUndefined()
    wrapper.unmount()
  })

  it('撤销失败：原因留在那一行，按钮还在（可以重试）', async () => {
    const { wrapper, confirms } = await mountSection({ revokeError: new Error('网关超时') })
    const activeRow = wrapper.findAll('.token-table tbody tr').find((row) => row.text().includes('a1b2c3d4'))
    await buttonByText(activeRow, '撤销').trigger('click')
    await confirms[0].onConfirm()
    await flushPromises()
    expect(wrapper.get('.token-feedback-row .alert.feedback').text()).toBe('撤销失败：网关超时')
    expect(buttonByText(wrapper, '撤销')).toBeTruthy()
    wrapper.unmount()
  })

  it('退出登录：按钮变「退出中…」，反馈贴在页头下面，随后客户端路由离开', async () => {
    // 让 router.replace 悬着：这样"退出中"那一刻的按钮状态可以确定地断言到
    // （登出的落地动作会一直 await 到导航返回）。
    let releaseReplace
    replace.mockImplementation(() => new Promise((resolve) => { releaseReplace = resolve }))
    const { wrapper, confirms } = await mountSection()
    await buttonByText(wrapper, '退出登录').trigger('click')
    expect(confirms[0].opts.title).toBe('确认退出登录')
    expect(confirms[0].opts.danger).toBe(true)

    const pending = confirms[0].onConfirm() // 不 await：下面要看"还悬着"时的界面
    await flushPromises()
    expect(wrapper.get('.settings-section > .alert').text()).toBe('正在退出登录…')
    expect(buttonByText(wrapper, '退出中…').attributes('disabled')).toBeDefined()

    releaseReplace()
    await pending
    await flushPromises()
    expect(replace).toHaveBeenCalledWith({
      path: '/login',
      query: { next: '/settings?section=account' },
    })
    // 走完就回到可点状态（成功那条不留痕，页面已经离开了）。
    expect(buttonByText(wrapper, '退出登录')).toBeTruthy()
    wrapper.unmount()
  })

  it('弹窗里「复制」失败时，反馈落在 API Token 面板（弹窗里没有第二个位置）', async () => {
    const { wrapper, host } = await mountSection()
    const tokenPanel = fieldsetByLegend(wrapper, 'API Token（KDS 等设备）')
    // jsdom 默认没有 navigator.clipboard → 走失败分支。
    await host.token.copyToken()
    await flushPromises()
    expect(tokenPanel.get('.alert.feedback').text()).toBe('复制失败，请手动选择复制')
    wrapper.unmount()
  })
})

describe('390px：Token 表格只裁信息列，操作入口不丢', () => {
  it('窄屏 media query 只隐藏创建 / 过期时间两列', () => {
    const mobile = compact(source.slice(source.indexOf('@media (max-width: 700px)')))
    expect(mobile).toContain(
      '.token-tableth:nth-child(3),.token-tabletd:nth-child(3),'
      + '.token-tableth:nth-child(4),.token-tabletd:nth-child(4){display:none;}',
    )
    // 前缀（1）/ 标签（2）/ 状态（5）/ 撤销（6）一个都不许裁。
    for (const column of ['1', '2', '5', '6', 'last-child']) {
      expect(mobile).not.toContain(`nth-child(${column})`)
      if (column === 'last-child') expect(mobile).not.toContain('last-child')
    }
  })

  it('有效行有撤销按钮、已撤销行没有；关键列表头齐', async () => {
    const { wrapper } = await mountSection()
    const headers = wrapper.findAll('.token-table thead th').map((th) => th.text())
    expect(headers.slice(0, 5)).toEqual(['前缀', '标签', '创建时间', '过期时间', '状态'])
    const rows = wrapper.findAll('.token-table tbody tr')
    const activeRow = rows.find((row) => row.text().includes('a1b2c3d4'))
    expect(buttonByText(activeRow, '撤销')).toBeTruthy()
    const deadRow = rows.find((row) => row.text().includes('e5f6a7b8'))
    expect(deadRow.text()).toContain('已撤销')
    expect(buttonByText(deadRow, '撤销')).toBeUndefined()
    // 前缀 / 状态这两列是窄屏留下的关键列，内容必须在。
    expect(activeRow.text()).toContain('a1b2c3d4…')
    expect(activeRow.text()).toContain('有效')
    wrapper.unmount()
  })
})

describe('契约：本票只动展示层', () => {
  it('分节不自己发请求，弹窗注册与两步确认通道不变', () => {
    expect(source).toMatch(/\(\{ showAlert, clearAlert/)
    expect(source).not.toMatch(/\bapi\.(get|post|put|patch|delete)\(/)
    expect(compact(source)).toContain("register('token',{")
    expect(compact(source)).toContain('modalHost?.requestConfirm(')
    expect(source).toContain('<style scoped>')
    expect(source.match(/<style(?! scoped)/)).toBeNull()
  })

  it('useAccountSettings 一字未改（git diff HEAD 为空）', () => {
    // 本票只重排展示：composable 不在改动范围内。只有"根本没有 git / 不是仓库"
    // （预打包源码树）才跳过；CI 是 git 检出，会真跑这一条。
    let repo
    try {
      repo = execFileSync('git', ['-C', here, 'rev-parse', '--show-toplevel'], {
        encoding: 'utf8',
        stdio: ['ignore', 'pipe', 'pipe'],
      }).trim()
    } catch {
      return
    }
    const paths = ['admin-web/src/composables/useAccountSettings.js']
    // 先证明 pathspec 真的命中仓库里的文件：`git diff` 对不匹配的 pathspec **静默**返回空，
    // 少了这一步，路径写错时这条断言会「看起来通过」。
    const tracked = execFileSync('git', ['-C', repo, 'ls-files', '--error-unmatch', '--', ...paths], {
      encoding: 'utf8',
    })
    expect(tracked.trim().split('\n')).toEqual(paths)
    const diff = execFileSync('git', ['-C', repo, 'diff', 'HEAD', '--', ...paths], {
      encoding: 'utf8',
    })
    expect(diff.trim()).toBe('')
  })
})
