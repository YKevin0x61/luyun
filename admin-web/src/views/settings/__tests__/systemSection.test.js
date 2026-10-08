// @vitest-environment jsdom
import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import SystemSection from '../SystemSection.vue'
import { UNHEALTHY_NEXT_STEPS } from '../../../composables/useSystemUpdate'

/**
 * 系统节（原「系统更新」）在 ADR 0099 下的**文案与反馈契约**。
 *
 * 这一节是唯一有"危险写操作 + 长机制说明"的分节，所以这里挂载真实组件、喂真夹具，
 * 量三件事：
 *
 *   1. 默认态可见的说明性文字总量（机制解释必须收进 <details>）；
 *   2. 必须留着的原文（更新后果 / 营业高峰风险 / 未健康三条出路）没被精简掉；
 *   3. 写操作（应用更新 / 应用待执行迁移 / 保存 GitHub 连接）的反馈落在本分节内、
 *      贴着触发它的按钮 —— 而不是飘在分节顶部。
 *
 * 另外锁两条"别再退化回去"的：自检未通过的指引与「部署目录有本地改动」的勾选说明
 * 各只留一处；更新历史的结果列不再把英文原文端出来。
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

const here = dirname(fileURLToPath(import.meta.url))
const SETTINGS_DIR = join(here, '..')
const source = readFileSync(join(SETTINGS_DIR, 'SystemSection.vue'), 'utf8')
const migrationPanel = readFileSync(
  join(SETTINGS_DIR, '../../components/update/DatabaseMigrations.vue'),
  'utf8',
)

function compact(text) {
  return text.replace(/\s+/g, '')
}

function release(tag, publishedAt = '2026-10-01T10:00:00+08:00') {
  return { tag, name: `正式发行 ${tag}`, published_at: publishedAt }
}

const MIGRATIONS = {
  supported: true,
  pending: [],
  applied: Array.from({ length: 19 }, (_, i) => ({
    version: String(i + 1).padStart(4, '0'),
    filename: `${String(i + 1).padStart(4, '0')}_step.sql`,
  })),
  changed: [],
  bootstrap_only: [{ version: '0001', filename: '0001_initial_schema.sql' }],
  incremental_total: 18,
  note: '',
}

/** 一个"干净"的默认态：自检全过、没有本地改动、没有待应用迁移、没有进行中的作业。 */
const VERSION_CHECK = {
  installed_tag: 'v0.8.0',
  app_version: '0.8.0',
  latest_tag: 'v0.9.0',
  update_available: true,
  catalogue_ok: true,
  degraded: false,
  releases: [
    release('v0.9.0'),
    release('v0.8.0'),
    release('v0.7.0'),
    release('v0.6.0'),
    release('v0.5.0'),
  ],
  preflight: {
    healthy_runtime: true,
    apply_allowed: true,
    discard_local_changes_allowed: false,
    checks: [
      { code: 'restart', ok: true, message: '主服务可重启' },
      { code: 'releases', ok: true, message: 'GitHub Releases 可达' },
    ],
  },
  pending_migrations: { count: 0, versions: [] },
}

const JOB_IDLE = { job: { stage: 'idle' }, log_tail: '' }
const GITHUB_CONFIG = { repo: 'acme/luyun', token_configured: false, updated_at: null }
const HISTORY_OK = {
  entries: [
    {
      target_tag: 'v0.8.0',
      previous_tag: 'v0.7.0',
      result: 'succeeded',
      result_label: '成功',
      rolled_back: false,
      duration_seconds: 120,
      finished_at: '2026-09-30T10:00:00+08:00',
    },
  ],
}

function setupApi({
  versionCheck = VERSION_CHECK,
  job = JOB_IDLE,
  healthCheck = null,
  history = HISTORY_OK,
  github = GITHUB_CONFIG,
  migrations = MIGRATIONS,
  applyError = null,
  applyResult = null,
  saveError = null,
} = {}) {
  api.get.mockImplementation(async (path) => {
    switch (path) {
      case '/api/release-update/version-check':
        return versionCheck
      case '/api/release-update/job':
        return job
      case '/api/release-update/history':
        return history
      case '/api/release-update/github-config':
        return github
      case '/api/db-migrations':
        return migrations
      default:
        throw new Error(`未预期的 GET ${path}`)
    }
  })
  api.post.mockImplementation(async (path) => {
    if (path === '/api/db-migrations/apply') {
      return { success: true, applied: [{ version: '0019', filename: '0019_step.sql' }] }
    }
    if (path === '/api/release-update/apply') {
      if (applyError) throw applyError
      return applyResult || { job: { stage: 'queued', target_tag: 'v0.9.0' } }
    }
    if (path === '/api/release-update/job/health-check') {
      return healthCheck || job
    }
    throw new Error(`未预期的 POST ${path}`)
  })
  api.put.mockImplementation(async (path) => {
    if (path === '/api/release-update/github-config') {
      if (saveError) throw saveError
      return { ...github, token_configured: true, updated_at: '2026-10-08T10:00:00+08:00' }
    }
    throw new Error(`未预期的 PUT ${path}`)
  })
}

async function mountSection(options) {
  setupApi(options)
  const wrapper = mount(SystemSection, { props: { active: true } })
  await flushPromises()
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  for (const fn of [api.get, api.post, api.put, api.patch, api.delete]) fn.mockReset()
})

const NOTE_SELECTOR = '.hint, .section-lead, .alert, .update-overview__alert'

/** 折叠块（<details> 没 open）里的字不算"默认态可见"。 */
function isFolded(element) {
  return Boolean(element.closest('details:not([open])'))
}

/** 真正看得见的那段文字：把默认收起的 <details> 整块摘掉再取文本。 */
function visibleText(element) {
  const clone = element.cloneNode(true)
  for (const node of clone.querySelectorAll('details:not([open])')) node.remove()
  return clone.textContent.replace(/\s+/g, ' ').trim()
}

function visibleNotes(wrapper, { skip = [] } = {}) {
  return wrapper
    .findAll(NOTE_SELECTOR)
    .filter((node) => !isFolded(node.element))
    .filter((node) => !skip.some((selector) => node.element.closest(selector)))
    .map((node) => node.text().replace(/\s+/g, ''))
    .filter((text) => text.length > 0)
}

function countText(haystack, needle) {
  return haystack.split(needle).length - 1
}

function fieldsetByLegend(wrapper, legend) {
  return wrapper
    .findAll('fieldset')
    .find((node) => node.find('legend').exists() && node.find('legend').text() === legend)
}

function buttonByText(wrapper, text) {
  return wrapper.findAll('button').find((node) => node.text() === text)
}

describe('默认态：说明性文字 ≤ 200 字，机制解释全在折叠里', () => {
  it('本节自己的默认可见说明合计 ≤ 200 字', async () => {
    const wrapper = await mountSection()
    const notes = visibleNotes(wrapper, { skip: ['.update-overview', '.db-migrations'] })
    const total = notes.join('').length
    // 失败时把清单打出来，看是哪里又长回来的。
    expect(notes, `默认态可见说明：${notes.join(' | ')}`).toEqual(['自检全部通过才能应用更新。'])
    expect(total).toBeLessThanOrEqual(200)
    wrapper.unmount()
  })

  it('连相邻面板（版本状态卡 / 数据库迁移）一起算也 ≤ 200 字', async () => {
    const wrapper = await mountSection()
    const total = visibleNotes(wrapper).join('').length
    expect(total).toBeLessThanOrEqual(200)
    wrapper.unmount()
  })

  it('机制解释都在默认收起的 <details> 里', async () => {
    const wrapper = await mountSection()
    const helps = wrapper.findAll('details.section-help')
    expect(helps.map((node) => node.find('summary').text())).toEqual([
      '自检在查什么',
      '怎么选版本',
      '作业进度怎么看',
      '历史怎么看',
    ])
    for (const help of helps) expect(help.element.open).toBe(false)

    for (const sentence of [
      '每一项都在服务端现场探测',
      '目录默认排除预发布',
      '进度写在本机状态文件里',
      '保留最近 30 条',
    ]) {
      const node = wrapper
        .findAll(NOTE_SELECTOR)
        .find((candidate) => candidate.text().includes(sentence))
      expect(node, `${sentence} 应该在某个 .hint 里`).toBeTruthy()
      expect(isFolded(node.element), `${sentence} 必须默认收起`).toBe(true)
    }

    // GitHub 连接整块是折叠的：面板本身 + 面板里的说明。
    expect(wrapper.get('details.github-panel').element.open).toBe(false)
    expect(wrapper.get('details.github-panel').text()).toContain('仓库为公开仓')
    wrapper.unmount()
  })
})

describe('必须保留可见原文：更新后果 / 营业高峰风险 / 未健康三条出路', () => {
  it('确认应用更新里把更新后果写在明面上', async () => {
    const wrapper = await mountSection()
    await wrapper.get('.release-list tbody tr .btn-primary').trigger('click')
    const confirm = fieldsetByLegend(wrapper, '确认应用更新')
    expect(confirm).toBeTruthy()
    const lead = confirm.find('p.hint.section-lead')
    expect(isFolded(lead.element)).toBe(false)
    expect(lead.text()).toContain('将把运行实例切换到')
    expect(lead.text()).toContain('作业会先强制备份，再下载发行包、切换应用目录、按需同步依赖并重启主服务。')
    // 两份勾选说明都还在（勾选框 + 各自的说明句）。
    expect(confirm.text()).toContain('我已知晓营业高峰风险，仍然执行更新')
    wrapper.unmount()
  })

  it('营业高峰被拦下：风险说明与覆盖勾选项一起显示，反馈就在按钮上方', async () => {
    const wrapper = await mountSection({
      applyError: Object.assign(new Error('peak'), {
        status: 409,
        detail: { reason: 'peak_hours', message: '当前处于营业高峰时段，请确认后勾选覆盖再试' },
      }),
    })
    await wrapper.get('.release-list tbody tr .btn-primary').trigger('click')
    await buttonByText(wrapper, '确认开始更新').trigger('click')
    await flushPromises()

    const confirm = fieldsetByLegend(wrapper, '确认应用更新')
    expect(confirm.text()).toContain('当前处于营业高峰时段。若仍要继续，请勾选下方覆盖项后再次确认。')
    expect(confirm.text()).toContain('我已知晓营业高峰风险，仍然执行更新')
    // 反馈落在框里、紧贴按钮：提示条的下一个兄弟就是操作区。
    const feedback = confirm.get('.alert.feedback')
    expect(feedback.text()).toContain('营业高峰')
    expect(feedback.element.nextElementSibling.classList.contains('actions')).toBe(true)
    // 分节顶部那条不接这个活。
    expect(wrapper.find('.settings-section > .alert').exists()).toBe(false)
    wrapper.unmount()
  })

  it('未健康时三条出路整段可见', async () => {
    const wrapper = await mountSection({
      job: {
        job: {
          stage: 'succeeded_but_unhealthy',
          target_tag: 'v0.9.0',
          previous_tag: 'v0.8.0',
          previous_ref: 'v0.8.0',
          log_path: '/var/log/luyun/update_job.log',
          health_detail: '数据库未连接',
        },
        log_tail: '',
      },
    })
    const health = fieldsetByLegend(wrapper, '健康确认结果')
    expect(health).toBeTruthy()
    expect(health.text()).toContain('重启后健康确认未通过')
    const steps = health.findAll('.unhealthy-next-steps li')
    expect(steps.map((node) => node.text())).toEqual(UNHEALTHY_NEXT_STEPS)
    for (const step of steps) expect(isFolded(step.element)).toBe(false)
    // 三条出路里的宿主机命令是给"页面打不开"预备的，别被折起来。
    expect(health.text()).toContain('sudo systemctl reset-failed luyun.service')
    wrapper.unmount()
  })
})

describe('重复项各留一处', () => {
  it('自检未通过的指引只出现在发行目录面板（被禁用的入口就在那儿）', async () => {
    const wrapper = await mountSection({
      versionCheck: {
        ...VERSION_CHECK,
        preflight: {
          ...VERSION_CHECK.preflight,
          healthy_runtime: false,
          apply_allowed: false,
          checks: [
            { code: 'restart', ok: false, message: '主服务不可重启' },
            { code: 'releases', ok: true, message: 'GitHub Releases 可达' },
          ],
        },
      },
    })
    const html = wrapper.html()
    expect(countText(html, '先修好上方')).toBe(1)
    expect(countText(html, '重启能力、GitHub Releases 可达性等')).toBe(1)
    // 原自检块里那句同义指引已经删掉（不再在自检与发行目录各说一遍）。
    expect(compact(source)).not.toContain('当前不是健康的运行实例')
    expect(compact(source)).not.toContain('已禁用「应用更新」')
    wrapper.unmount()
  })

  it('「部署目录有本地改动」只在确认框里说一次，勾选项就在旁边', async () => {
    const wrapper = await mountSection({
      versionCheck: {
        ...VERSION_CHECK,
        preflight: {
          ...VERSION_CHECK.preflight,
          apply_allowed: false,
          discard_local_changes_allowed: true,
        },
      },
    })
    // 还没点「应用此版本」时，自检块不再重复那句勾选说明。
    expect(wrapper.html()).not.toContain('部署目录有本地改动')
    expect(compact(source)).not.toContain('默认禁止更新')

    await wrapper.get('.release-list tbody tr .btn-primary').trigger('click')
    const confirm = fieldsetByLegend(wrapper, '确认应用更新')
    expect(countText(confirm.html(), '部署目录有本地改动')).toBe(1)
    expect(countText(confirm.html(), '我确认丢弃部署目录中的本地改动')).toBe(1)
    wrapper.unmount()
  })

  it('数据库迁移面板的 scoped 样式没有被回退掉，仍排在本节里', () => {
    // 父分节的 <style scoped> 够不到子组件的 legend / p / ul —— 这一块必须留在面板自己文件里。
    expect(migrationPanel).toContain('<style scoped>')
    expect(migrationPanel).toMatch(/\.db-migrations legend\s*\{/)
    expect(migrationPanel).toMatch(/\.db-migrations \.hint\s*\{/)
    expect(migrationPanel).toMatch(/\.migration-applied > summary/)
    expect(migrationPanel).toMatch(/\.migration-applied ul \{ list-style: none/)
    // 面板仍挂在系统节里，且排在发行版目录之前。
    expect(source).toContain("import DatabaseMigrations from '../../components/update/DatabaseMigrations.vue'")
    expect(source).toContain('<DatabaseMigrations />')
    expect(source.indexOf('<DatabaseMigrations />')).toBeLessThan(
      source.indexOf('正式发行版目录'),
    )
  })
})

describe('更新历史的结果列：英文原文不再直接渲染', () => {
  it('老记录只有英文 result / 英文 result_label 时，展示层映射成中文', async () => {
    const wrapper = await mountSection({
      history: {
        entries: [
          {
            target_tag: 'v0.9.0',
            previous_tag: 'v0.8.0',
            result: 'succeeded_but_unhealthy',
            result_label: 'succeeded_but_unhealthy',
            rolled_back: false,
            duration_seconds: 42,
            finished_at: '2026-10-07T10:00:00+08:00',
          },
          {
            target_tag: 'v0.8.0',
            previous_tag: 'v0.7.0',
            result: 'cancelled',
            rolled_back: false,
            duration_seconds: 12,
            finished_at: '2026-10-06T10:00:00+08:00',
          },
          {
            target_tag: 'v0.7.0',
            previous_tag: 'v0.6.0',
            result: 'failed',
            result_label: '失败',
            rolled_back: true,
            rollback_ok: true,
            duration_seconds: 30,
            finished_at: '2026-10-05T10:00:00+08:00',
          },
        ],
      },
    })
    const rows = wrapper.findAll('.history-list tbody tr')
    expect(rows).toHaveLength(3)
    const results = rows.map((row) => row.findAll('td')[2].text())
    expect(results[0]).toContain('已切换但未健康')
    expect(results[1]).toContain('已取消')
    expect(results[2]).toContain('失败')
    for (const cell of results) {
      expect(cell).not.toMatch(/succeeded|cancelled|failed|unhealthy/)
    }
    // 接口字段没被改写：fixture 里那份还是原文，页面只在渲染时映射。
    expect(api.get.mock.results.length).toBeGreaterThan(0)
    wrapper.unmount()
  })

  it('展示层映射是本节的（composable 与接口字段都没动）', () => {
    expect(source).toContain('HISTORY_RESULT_LABELS')
    expect(source).toContain('function historyResultLabel(entry)')
    expect(source).toContain(':label="historyResultLabel(entry)"')
    expect(source).not.toContain('entry.result_label || entry.result')
  })
})

describe('失败原因（F-05）：中文标签 + 摘要，原文折起来', () => {
  /** 极端夹具：多行 + 单行 500+ 字符（systemd 输出就是这个形状）。 */
  const LONG_LINE = `Failed to restart luyun.service: Unit luyun.service not found. ${'x'.repeat(500)}`
  const MULTI_LINE_ERROR = [
    'Job for luyun.service failed because the control process exited with error code.',
    'See "systemctl status luyun.service" and "journalctl -xeu luyun.service" for details.',
    LONG_LINE,
  ].join('\n')

  function resultCell(wrapper, index = 0) {
    return wrapper.findAll('.history-list tbody tr')[index].findAll('td')[2]
  }

  async function mountWithHistory(entries) {
    return mountSection({ history: { entries } })
  }

  it('默认只显示「失败原因」+ 首行摘要（≤60 字），完整原文在默认收起的 details 里', async () => {
    const wrapper = await mountWithHistory([
      {
        target_tag: 'v0.9.0',
        previous_tag: 'v0.8.0',
        result: 'failed',
        result_label: '失败',
        rolled_back: false,
        duration_seconds: 12,
        finished_at: '2026-10-08T10:10:10+08:00',
        error: MULTI_LINE_ERROR,
      },
    ])
    const cell = resultCell(wrapper)
    const summary = cell.get('.history-error__summary')

    // 中文标签 + 摘要，摘要按首行截断到 60 字以内（省略号也算在预算里）。
    expect(cell.get('.history-error__label').text()).toBe('失败原因：')
    expect(summary.text().length).toBeLessThanOrEqual(60)
    expect(summary.text().startsWith('Job for luyun.service failed')).toBe(true)
    expect(summary.text().endsWith('…')).toBe(true)

    // 完整原文只在折叠里，而且是默认收起的。
    const raw = cell.get('details.history-error__raw')
    expect(raw.element.open).toBe(false)
    expect(raw.get('pre').text()).toBe(MULTI_LINE_ERROR)
    expect(isFolded(raw.element)).toBe(true)

    // 默认可见的那段文字里没有整段英文原文（只有被截断的首行）。
    const visible = visibleText(cell.element)
    expect(visible).toContain('失败原因：')
    expect(visible).not.toContain(LONG_LINE)
    expect(visible).not.toContain('journalctl -xeu')
    expect(visible.length).toBeLessThan(60 + 30)
    // 原文一个字没改（长度逐字对齐夹具）。
    expect(raw.get('pre').text().length).toBe(MULTI_LINE_ERROR.length)
    wrapper.unmount()
  })

  it('单行 500+ 字符的极端 error 也只给 60 字摘要，原文仍可展开', async () => {
    const wrapper = await mountWithHistory([
      {
        target_tag: 'v0.9.0',
        previous_tag: 'v0.8.0',
        result: 'failed',
        result_label: '失败',
        duration_seconds: 1,
        finished_at: '2026-10-08T10:10:10+08:00',
        error: LONG_LINE,
      },
    ])
    const cell = resultCell(wrapper)
    expect(cell.get('.history-error__summary').text().length).toBe(60)
    expect(cell.get('details.history-error__raw pre').text()).toBe(LONG_LINE)
    expect(visibleText(cell.element)).not.toContain('x'.repeat(60))
    wrapper.unmount()
  })

  it('短原因也照同一套渲染：摘要就是原句，原文仍然折在 details 里（复制路径统一）', async () => {
    const wrapper = await mountWithHistory([
      {
        target_tag: 'v0.9.0',
        previous_tag: 'v0.8.0',
        result: 'failed',
        result_label: '失败',
        duration_seconds: 3,
        finished_at: '2026-10-08T10:10:10+08:00',
        error: 'health check failed',
      },
    ])
    const cell = resultCell(wrapper)
    expect(cell.get('.history-error__summary').text()).toBe('health check failed')
    const raw = cell.get('details.history-error__raw')
    expect(raw.element.open).toBe(false)
    expect(raw.get('pre').text()).toBe('health check failed')
    expect(visibleText(cell.element)).not.toContain('完整原文 health check failed')
    wrapper.unmount()
  })

  it('结果单元格允许在任意字符处换行（390px 不撑破表格）', () => {
    const styles = compact(source.slice(source.indexOf('<style')))
    expect(styles).toContain('.history-list.history-result{overflow-wrap:anywhere;}')
    // 只给结果列：整表都 anywhere 会把最小内容宽度也一起改掉，版本 / 时间列会被挤成竖排。
    expect(styles).not.toContain('.history-listtd{overflow-wrap:anywhere;}')
    expect(styles).toContain('.history-error__rawpre{')
    expect(styles).toContain('word-break:break-word;overflow-wrap:anywhere;')
  })

  it('「更新作业进度」里当前作业的失败原因仍然默认可见（只处理历史里的过去失败）', async () => {
    const failing = await mountSection({
      job: {
        job: {
          stage: 'failed',
          target_tag: 'v0.9.0',
          error: 'systemctl restart failed: unit luyun.service not found',
          log_path: '/var/log/luyun/update_job.log',
        },
        log_tail: '',
      },
    })
    const panel = fieldsetByLegend(failing, '更新作业进度')
    const errorLine = panel
      .findAll('.alert')
      .find((node) => node.text().includes('unit luyun.service not found'))
    expect(errorLine).toBeTruthy()
    expect(isFolded(errorLine.element)).toBe(false)
    expect(errorLine.element.closest('details')).toBeNull()
    expect(panel.text()).toContain('systemctl restart failed: unit luyun.service not found')
    failing.unmount()
  })
})

describe('写操作反馈就近', () => {
  it('保存 GitHub 连接：反馈落在折叠面板内、紧贴保存按钮', async () => {
    const wrapper = await mountSection()
    const panel = wrapper.get('details.github-panel')
    await buttonByText(panel, '保存连接').trigger('click')
    await flushPromises()

    const feedback = panel.get('.alert.feedback')
    expect(feedback.text()).toContain('GitHub 配置已保存')
    expect(feedback.element.nextElementSibling.classList.contains('actions')).toBe(true)
    expect(panel.html()).toContain('保存连接')
    // 不在分节顶部重复一份。
    expect(wrapper.find('.settings-section > .alert').exists()).toBe(false)
    wrapper.unmount()
  })

  it('保存失败也一样留在面板里，不清屏', async () => {
    const wrapper = await mountSection({ saveError: new Error('限流') })
    const panel = wrapper.get('details.github-panel')
    await buttonByText(panel, '保存连接').trigger('click')
    await flushPromises()
    expect(panel.get('.alert.feedback').text()).toContain('保存 GitHub 配置失败')
    expect(wrapper.find('.settings-section > .alert').exists()).toBe(false)
    wrapper.unmount()
  })

  it('加载 GitHub 配置失败留在分节顶部（面板默认收起，落进去就看不见了）', async () => {
    setupApi()
    api.get.mockImplementation(async (path) => {
      if (path === '/api/release-update/github-config') throw new Error('网关超时')
      if (path === '/api/release-update/version-check') return VERSION_CHECK
      if (path === '/api/release-update/job') return JOB_IDLE
      if (path === '/api/release-update/history') return HISTORY_OK
      if (path === '/api/db-migrations') return MIGRATIONS
      throw new Error(`未预期的 GET ${path}`)
    })
    const wrapper = mount(SystemSection, { props: { active: true } })
    await flushPromises()
    await flushPromises()
    const top = wrapper.get('.settings-section > .alert')
    expect(top.text()).toContain('加载 GitHub 配置失败')
    expect(wrapper.get('details.github-panel').element.open).toBe(false)
    expect(wrapper.get('details.github-panel').find('.alert.feedback').exists()).toBe(false)
    wrapper.unmount()
  })

  it('应用更新：提交成功后的反馈落在作业进度区（确认框此时关掉了）', async () => {
    const wrapper = await mountSection()
    await wrapper.get('.release-list tbody tr .btn-primary').trigger('click')
    await buttonByText(wrapper, '确认开始更新').trigger('click')
    await flushPromises()

    expect(fieldsetByLegend(wrapper, '确认应用更新')).toBeUndefined()
    const jobPanel = fieldsetByLegend(wrapper, '更新作业进度')
    const feedback = jobPanel.get('.alert.feedback')
    expect(feedback.text()).toContain('已开始应用更新：v0.9.0')
    // 就在阶段进度正下方：结论跟着进度走（那个确认框在这时已经关掉了）。
    const progress = jobPanel.get('.stages')
    expect(
      progress.element.compareDocumentPosition(feedback.element) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
    expect(wrapper.find('.settings-section > .alert').exists()).toBe(false)
    wrapper.unmount()
  })

  it('应用待执行迁移：反馈留在迁移面板里', async () => {
    const wrapper = await mountSection({
      migrations: {
        ...MIGRATIONS,
        pending: [{ version: '0019', filename: '0019_step.sql' }],
      },
    })
    const panel = wrapper.get('fieldset.db-migrations')
    expect(panel.text()).toContain('有 1 条迁移待应用：0019_step.sql')
    await buttonByText(panel, '应用待执行迁移').trigger('click')
    await flushPromises()
    await buttonByText(wrapper, '应用').trigger('click')
    await flushPromises()
    await flushPromises()

    const status = wrapper.get('fieldset.db-migrations p[role="status"]')
    expect(status.text()).toBe('已应用：0019')
    // 走的是面板自己的回执行，不是分节顶部的提示条。
    expect(wrapper.find('.settings-section > .alert').exists()).toBe(false)
    wrapper.unmount()
  })

  it('版本检测失败仍留在分节顶部（它上面那张卡里的按钮就是触发点）', async () => {
    setupApi()
    api.get.mockImplementation(async (path) => {
      if (path === '/api/release-update/version-check') throw new Error('网络不通')
      if (path === '/api/release-update/job') return JOB_IDLE
      if (path === '/api/release-update/history') return HISTORY_OK
      if (path === '/api/release-update/github-config') return GITHUB_CONFIG
      if (path === '/api/db-migrations') return MIGRATIONS
      throw new Error(`未预期的 GET ${path}`)
    })
    const wrapper = mount(SystemSection, { props: { active: true } })
    await flushPromises()
    await flushPromises()
    const top = wrapper.get('.settings-section > .alert')
    expect(top.text()).toContain('版本检测失败')
    expect(top.element.nextElementSibling.className).toContain('update-overview')
    wrapper.unmount()
  })

  it('重开页面看到的历史失败：原因照旧显示（那时没有提示条可替）', async () => {
    const wrapper = await mountSection({
      job: {
        job: {
          stage: 'failed',
          target_tag: 'v0.9.0',
          error: 'systemctl restart failed: unit luyun.service not found',
          log_path: '/var/log/luyun/update_job.log',
        },
        log_tail: '',
      },
    })
    const jobPanel = fieldsetByLegend(wrapper, '更新作业进度')
    expect(jobPanel.find('.alert.feedback').exists()).toBe(false)
    expect(jobPanel.text()).toContain('systemctl restart failed: unit luyun.service not found')
    wrapper.unmount()
  })

  it('就绪检查报失败：原因只显示一次（提示条带着日志路径，下面不再重复一句）', async () => {
    const failure = {
      job: {
        stage: 'failed',
        target_tag: 'v0.9.0',
        error: 'systemctl restart failed: unit luyun.service not found',
        log_path: '/var/log/luyun/update_job.log',
      },
      log_tail: '',
    }
    const wrapper = await mountSection({
      job: { job: { stage: 'restarting', target_tag: 'v0.9.0' }, log_tail: '' },
      healthCheck: failure,
    })
    const health = fieldsetByLegend(wrapper, '健康确认结果')
    expect(health).toBeTruthy()
    await buttonByText(health, '重新检测').trigger('click')
    await flushPromises()

    const jobPanel = fieldsetByLegend(wrapper, '更新作业进度')
    const shown = jobPanel.text()
    expect(shown).toContain('应用更新失败：systemctl restart failed: unit luyun.service not found')
    expect(countText(jobPanel.html(), 'unit luyun.service not found')).toBe(1)
    expect(jobPanel.get('.alert.feedback').text()).toContain('/var/log/luyun/update_job.log')
    wrapper.unmount()
  })
})

describe('契约：本票只动展示层', () => {
  it('分节不自己发请求，也不改接口字段', () => {
    expect(source).toMatch(/\(\{ showAlert, clearAlert/)
    expect(source).not.toMatch(/\bapi\.(get|post|put|patch|delete)\(/)
    expect(source).toContain('<style scoped>')
    expect(source.match(/<style(?! scoped)/)).toBeNull()
  })

  it('请求逻辑与迁移服务一字未改（git diff HEAD 为空）', () => {
    // 本票只重排展示：composable 与 db_migrations 服务不在改动范围内。
    // 只有"根本没有 git / 不是仓库"（预打包源码树）才跳过；CI 是 git 检出，会真跑这一条。
    let repo
    try {
      repo = execFileSync('git', ['-C', here, 'rev-parse', '--show-toplevel'], {
        encoding: 'utf8',
        stdio: ['ignore', 'pipe', 'pipe'],
      }).trim()
    } catch {
      return
    }

    const paths = [
      'admin-web/src/composables/useSystemUpdate.js',
      'services/db_migrations.py',
    ]
    // 先证明 pathspec 真的命中仓库里的文件：`git diff` 对不匹配的 pathspec **静默**返回空，
    // 少了这一步，路径写错时这条断言会「看起来通过」（写这条测试时就踩过一次）。
    const tracked = execFileSync('git', ['-C', repo, 'ls-files', '--error-unmatch', '--', ...paths], {
      encoding: 'utf8',
    })
    expect(tracked.trim().split('\n')).toHaveLength(paths.length)

    const diff = execFileSync('git', ['-C', repo, 'diff', 'HEAD', '--', ...paths], {
      encoding: 'utf8',
    })
    expect(diff.trim()).toBe('')
  })

  it('长路径/长 tag 在窄屏能换行，不会把分节撑宽', () => {
    const styles = compact(source.slice(source.indexOf('<style')))
    expect(styles).toContain('.meta-grid>div,.meta-grid.v{min-width:0;}')
    expect(styles).toContain('.meta-grid.v{overflow-wrap:anywhere;}')
    expect(styles).toContain('.actions.btn{max-width:100%;overflow-wrap:anywhere;}')
  })
})
