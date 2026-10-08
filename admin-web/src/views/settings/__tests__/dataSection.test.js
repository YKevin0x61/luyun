// @vitest-environment jsdom
//
// 数据节（DataSection）的验收测试（t4 / ADR 0099）。
//
// 本文件是**真挂载**（jsdom + @vue/test-utils）而不是读源码的契约断言：本节的三条验收
// （默认态可见说明文字 ≤ 200 字、四处已知重复各留一处、写操作反馈紧贴触发按钮）只有在
// 真渲染里才量得准 —— 折叠区算不算可见、v-if 的文案在默认态在不在 DOM 里、提示条在按钮
// 上面还是下面，读源码都答不上来。
//
// 三处替身：
//   · `api/client` 按 URL 返回固定 payload（本节不碰网络，也不改请求逻辑）；
//   · `BackupOverview` / `BackupPointList` 用 stub —— 它们是本节之外的组件（本次范围外），
//     它们自己的分组提示（如「每次回滚前会自动再建一份」）不计进"本节可见文字"；
//   · `SETTINGS_MODAL_HOST` 给一个假宿主 —— 弹窗的「框」在壳里，本节只注册状态与文案，
//     宿主收到的两步确认明细可以用来核对"预览与弹窗同一份数据"。
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { readFileSync } from 'node:fs'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const apiGet = vi.fn()
const apiPost = vi.fn()
const apiPut = vi.fn()
const apiUpload = vi.fn()
const apiDownloadUrl = vi.fn()

vi.mock('../../../api/client', () => ({
  api: {
    get: (...args) => apiGet(...args),
    post: (...args) => apiPost(...args),
    put: (...args) => apiPut(...args),
    upload: (...args) => apiUpload(...args),
    downloadUrl: (...args) => apiDownloadUrl(...args),
  },
}))

// 本节只用到 useRouter()（composable 不引 router 单例，由页面这一层给它）。
vi.mock('vue-router', () => ({
  useRouter: () => ({
    push: () => {},
    replace: () => {},
    currentRoute: { value: { fullPath: '/settings?section=data' } },
  }),
}))

const { default: DataSection } = await import('../DataSection.vue')
const { default: LuyunFileDropzone } = await import('../../../components/ui/LuyunFileDropzone.vue')
const { SETTINGS_MODAL_HOST } = await import('../sectionContract.js')

const here = dirname(fileURLToPath(import.meta.url))
const BACKUP_DIR = join(here, '../../../components/backup')

// ============================================================================
// 固定 payload：形状与后端 / composable 一致（取自 composables/__tests__ 的既有替身）
// ============================================================================

function healthPayload() {
  return {
    success: true,
    health: {
      status: 'ok',
      summary: '最近备份点校验通过，可恢复',
      next_step: '保持定期导出并复制到别处',
      last_success_at: '2026-09-01T10:00:00+08:00',
      last_success_medium_label: '导出备份',
      checks: [{ code: 'has_backup', ok: true, message: '存在备份点' }],
      counts: { total: 3, usable: 2, unusable: 1, by_medium: {} },
      coverage: ['credentials'],
      coverage_labels: ['凭据'],
      total_bytes: 2048,
      computed_at: '2026-09-01T10:05:00+08:00',
    },
  }
}

function pointsPayload() {
  return {
    success: true,
    backend: 'postgres',
    export_app_db_supported: true,
    app_db_export_format: 'pgdump',
    points: [
      {
        id: 'snapshot:20260901_100000',
        medium: 'local_snapshot',
        created_at: '2026-09-01T10:00:00+08:00',
        provenance_label: '更新作业前',
        size_bytes: 1024,
        contents: ['app_pg'],
        contents_labels: ['业务数据'],
        recoverable: true,
        detail: { ts: '20260901_100000', files: [] },
      },
    ],
    health: healthPayload().health,
    not_backed_up: [],
    medium_labels: { local_snapshot: '本机回滚快照', export_backup: '导出备份', cold_backup: '冷备' },
    medium_purposes: {},
  }
}

/** 保留与清理的预览：2 份快照 + 1 份导出备份将被删，2 份受保护。 */
function cleanupPreview() {
  return {
    snapshot: {
      keep: 5,
      protected: [{ id: 'snapshot:20260901_100000', ts: '20260901_100000', provenance_label: '更新作业前', reason: '更新前快照，永不自动清理', size_bytes: 10 }],
      delete: [
        { id: 'snapshot:20260801_100000', ts: '20260801_100000', provenance_label: '手动', size_bytes: 20 },
        { id: 'snapshot:20260701_100000', ts: '20260701_100000', provenance_label: '手动', size_bytes: 21 },
      ],
      kept: [],
    },
    export: {
      keep: 5,
      protected: [],
      delete: [{ id: 'export:old.luyunbak', name: 'luyun_backup_20260701.luyunbak', size_bytes: 30 }],
      kept: [],
    },
    cold: {
      keep: 14,
      protected: [{ id: 'cold:20260901_080000', ts: '20260901_080000', reason: '冷备未报告校验结论', size_bytes: 40 }],
      delete: [],
      kept: [],
    },
  }
}

function retentionPayload() {
  return {
    success: true,
    config: { snapshot_keep: 5, export_keep: 5, cold_keep: 14 },
    limits: {
      snapshot_keep_default: 5, snapshot_keep_min: 1, snapshot_keep_max: 20,
      export_keep_default: 5, export_keep_min: 1, export_keep_max: 20,
      cold_keep_default: 14, cold_keep_min: 1, cold_keep_max: 90,
    },
    preview: cleanupPreview(),
  }
}

function dbCredPayload(overrides = {}) {
  return {
    is_postgres: true,
    backend: 'postgres',
    user: 'luyun',
    host: 'localhost',
    port: 5432,
    database: 'luyun',
    password_length: 32,
    dsn: 'postgresql://luyun:***@localhost:5432/luyun',
    env_file: '/etc/luyun/env.production',
    env_file_writable: true,
    env_override: false,
    ...overrides,
  }
}

/** 预览结果：PG 整库快照（has_app_pg）→ 恢复模式只剩整库覆盖。 */
function pgPreviewPayload(overrides = {}) {
  const payload = {
    success: true,
    import_token: 'tok-1',
    meta: {
      exported_at: '2026-09-01T09:00:00+08:00',
      app_version: '0.2.0',
      includes: { runtime: false, app_pg: true, recipes_db: false, standard_photos: false, other_photos: false },
    },
    credentials_preview: { phone_masked: '138****0000', shop_id: '100001', company_id: '200002', shop_name: 'LuckIn' },
    has_app_pg: true,
    photos: {
      standard: { label: '标准图', included: false, count: 0, declared_count: 0, missing_references: 0 },
      other: { label: '其它照片', included: false, count: 0, declared_count: 0, missing_references: 0 },
    },
    missing: [{ content: 'runtime', label: '运行配置' }],
    validation: { in_backup: { ok: true, errors: [] }, cross_point: { has_difference: false } },
    restore_allowed: true,
    requires_force: false,
    default_apply: { credentials: true, runtime: false, app_db: true, app_pg: true, recipes_db: false, standard_photos: false, other_photos: false },
  }
  return { ...payload, ...overrides }
}

/** 预览结果：备份自身不一致（损坏）→ 阻止恢复且不可覆盖。 */
function corruptPreviewPayload() {
  return pgPreviewPayload({
    validation: {
      in_backup: { ok: false, errors: ['归档校验和不一致，文件可能被改动'] },
      cross_point: { has_difference: false },
    },
    restore_allowed: false,
  })
}

// ============================================================================
// 替身服务端 + 挂载
// ============================================================================

let server
const mounted = []

function apiError(message, { status, detail } = {}) {
  return Object.assign(new Error(message), { status, detail })
}

function createServer() {
  return {
    health: healthPayload(),
    points: pointsPayload(),
    retention: retentionPayload(),
    dbCred: dbCredPayload(),
    exportStart: { success: true, job_id: 'job-1' },
    exportStartError: null,
    exportJob: { state: 'done', name: 'luyun_backup.luyunbak', stage: 'done', done: 1, total: 1, unit: 'count' },
    importPreview: null,
    importPreviewError: null,
    applyResult: { applied_labels: ['业务数据'], snapshot_ts: '20260901_130000', session_invalidated: false },
    applyError: null,
    validateResult: { result: { ok: true, messages: [], recoverable: true } },
    rollbackResult: { applied_labels: ['业务数据'], snapshot_ts: '20260901_130000' },
    rollbackError: null,
    cleanupPreview: cleanupPreview(),
    cleanupResult: { success: true, deleted: [{ id: 'snapshot:20260801_100000' }], freed_bytes: 2048, preview: cleanupPreview() },
    cleanupError: null,
    retentionSaveResult: { success: true, cleanup: { deleted: [], freed_bytes: 0 } },
    retentionSaveError: null,
    dbResetResult: { success: true, user: 'luyun', env_file: '/etc/luyun/env.production', password_length: 32, restart_scheduled: true },
    dbResetError: null,
    calls: [],
  }
}

function installApi() {
  apiGet.mockImplementation(async (url) => {
    server.calls.push(['GET', url])
    if (url === '/api/backup/health') return server.health
    if (url === '/api/backup/points') return server.points
    if (url === '/api/backup/retention') return server.retention
    if (url === '/api/admin/db-credentials') return server.dbCred
    if (url.startsWith('/api/backup/export/jobs/')) return server.exportJob
    throw new Error(`测试没有为 GET ${url} 准备桩`)
  })

  apiPost.mockImplementation(async (url) => {
    server.calls.push(['POST', url])
    if (url === '/api/backup/export/jobs') {
      if (server.exportStartError) throw server.exportStartError
      return server.exportStart
    }
    if (url === '/api/backup/health/refresh') return server.health
    if (url === '/api/backup/cleanup/preview') return { success: true, preview: server.cleanupPreview }
    if (url === '/api/backup/cleanup') {
      if (server.cleanupError) throw server.cleanupError
      return server.cleanupResult
    }
    if (url === '/api/admin/db-credentials/reset') {
      if (server.dbResetError) throw server.dbResetError
      return server.dbResetResult
    }
    if (url.startsWith('/api/backup/points/')) return server.validateResult
    if (url.startsWith('/api/backup/snapshots/')) {
      if (server.rollbackError) throw server.rollbackError
      return server.rollbackResult
    }
    throw new Error(`测试没有为 POST ${url} 准备桩`)
  })

  apiPut.mockImplementation(async (url) => {
    server.calls.push(['PUT', url])
    if (url === '/api/backup/retention') {
      if (server.retentionSaveError) throw server.retentionSaveError
      return server.retentionSaveResult
    }
    throw new Error(`测试没有为 PUT ${url} 准备桩`)
  })

  apiUpload.mockImplementation(async (url) => {
    server.calls.push(['UPLOAD', url])
    if (url === '/api/backup/import/preview') {
      if (server.importPreviewError) throw server.importPreviewError
      if (!server.importPreview) throw new Error('测试没有为预览准备桩')
      return server.importPreview
    }
    if (url === '/api/backup/import/apply') {
      if (server.applyError) throw server.applyError
      return server.applyResult
    }
    throw new Error(`测试没有为 UPLOAD ${url} 准备桩`)
  })

  apiDownloadUrl.mockImplementation(() => {})
}

/** 弹窗宿主替身：框在壳里，这里只收注册进来的控制器与两步确认请求。 */
function makeHost() {
  const host = {
    controllers: {},
    confirmRequests: [],
    register(kind, controller) { host.controllers[kind] = controller },
    requestConfirm(opts, onConfirm) { host.confirmRequests.push({ opts, onConfirm }) },
  }
  return host
}

async function settle() {
  for (let i = 0; i < 8; i += 1) await flushPromises()
}

async function mountSection({ host = makeHost() } = {}) {
  const wrapper = mount(DataSection, {
    props: { active: true },
    global: {
      // symbol 键的 provide 走插件（VTU 的 global.provide 是 Object.keys 遍历，符号键会被丢掉）
      plugins: [{ install: (app) => { app.provide(SETTINGS_MODAL_HOST, host) } }],
      stubs: { BackupOverview: true, BackupPointList: true },
    },
  })
  mounted.push(wrapper)
  await settle()
  return { wrapper, host }
}

beforeEach(() => {
  server = createServer()
  apiGet.mockReset()
  apiPost.mockReset()
  apiPut.mockReset()
  apiUpload.mockReset()
  apiDownloadUrl.mockReset()
  installApi()
})

afterEach(() => {
  while (mounted.length) mounted.pop().unmount()
  document.body.innerHTML = ''
})

// ============================================================================
// 量"默认态看得见"的文字：v-show 藏起来的、<details> 没展开的都不算
// ============================================================================

function isSelfHidden(el) {
  if (el.style && el.style.display === 'none') return true
  return el.tagName === 'DETAILS' && !el.open
}

function visibleText(root) {
  const chunks = []
  const visit = (el) => {
    for (const node of el.childNodes) {
      if (node.nodeType === 3) { chunks.push(node.data); continue }
      if (node.nodeType !== 1 || isSelfHidden(node)) continue
      visit(node)
    }
  }
  visit(root)
  return chunks.join('').replace(/\s+/g, ' ').trim()
}

/** 折叠区（<details>）里的文字：机制解释只该住在这里。 */
function detailsText(root) {
  return [...root.querySelectorAll('details')].map((d) => d.textContent).join(' ')
}

/** 默认态可见的"说明性文字"：`.hint` 且不在折叠区 / 隐藏节点里。 */
function explainTexts(wrapper) {
  const root = wrapper.element
  return [...root.querySelectorAll('.hint')]
    .filter((el) => {
      for (let node = el; node && node !== root; node = node.parentElement) {
        if (isSelfHidden(node)) return false
      }
      return true
    })
    .map((el) => el.textContent.replace(/\s+/g, ' ').trim())
    .filter(Boolean)
}

function cjkCount(text) {
  return (text.match(/[\u4e00-\u9fff]/g) || []).length
}

function occurrences(text, needle) {
  return text.split(needle).length - 1
}

function panelByLegend(wrapper, legend) {
  const panels = wrapper.findAll('fieldset').filter((f) => f.find('legend').text() === legend)
  expect(panels, `分节「${legend}」应当有且只有一块`).toHaveLength(1)
  return panels[0]
}

function findButton(root, label) {
  const btn = root.findAll('button').find((b) => b.text().includes(label))
  expect(btn, `没有找到按钮「${label}」`).toBeTruthy()
  return btn
}

/** a 是否在 b 之后（DOM 顺序）。 */
function isAfter(a, b) {
  return Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING)
}

async function pickBackupFile(wrapper, name = 'luyun_backup.luyunbak') {
  wrapper.findComponent(LuyunFileDropzone).vm.$emit('change', new File(['x'], name))
  await settle()
}

async function previewBackup(wrapper, payload = 'pg') {
  if (payload) server.importPreview = payload === 'pg' ? pgPreviewPayload() : payload
  await pickBackupFile(wrapper)
  await wrapper.find('#importPass').setValue('secret1')
  await findButton(panelByLegend(wrapper, '恢复'), '预览').trigger('click')
  await settle()
}

async function fillExportPassphrase(wrapper, value = 'secret1') {
  await wrapper.find('#exportPass').setValue(value)
  await wrapper.find('#exportPass2').setValue(value)
}

/**
 * 走一遍壳里那个两步确认框：勾上必勾项 → 点「确认」。
 * 本节注册的控制器就是 composable 的 `confirmState` / `onConfirmClick`，壳渲染的框调的
 * 正是它们 —— 所以这里走的是和壳一样的路径，而不是绕过弹窗直接调动作。
 */
async function confirmThroughShell(host) {
  const confirm = host.controllers.confirm
  expect(confirm.state.open, '两步确认应当已经打开').toBe(true)
  for (const box of confirm.state.checkboxes) confirm.checked[box.key] = true
  await confirm.submit()
  await settle()
}

// ============================================================================

describe('默认态：只留操作必需与风险/后果提示', () => {
  it('默认态可见的说明性文字 ≤ 200 字', async () => {
    const { wrapper } = await mountSection()
    const hints = explainTexts(wrapper)
    const joined = hints.join('')
    const cjk = cjkCount(joined)
    const nonSpace = joined.replace(/\s+/g, '').length
    const detail = `默认态可见说明文字 ${cjk} 个汉字 / ${nonSpace} 个非空白字符，逐条：${hints.join(' ｜ ')}`

    expect(cjk, detail).toBeLessThanOrEqual(200)
    // 连拉丁字符一起算也不许超（"字"按最严的口径量）
    expect(nonSpace, detail).toBeLessThanOrEqual(200)
    // 默认态不该堆一大串说明：逐条都是短句
    for (const hint of hints) expect(hint.length, detail).toBeLessThanOrEqual(60)
  })

  it('机制解释进默认折叠的 details，默认态一个字都看不到', async () => {
    const { wrapper } = await mountSection()
    const all = wrapper.text()
    const visible = visibleText(wrapper.element)
    const folded = detailsText(wrapper.element)

    const details = wrapper.findAll('details')
    // 两块机制解释：导出的「这个包里装了什么」与保留清理的「保留与清理怎么算」
    expect(details.length).toBeGreaterThanOrEqual(2)
    for (const d of details) expect(d.element.open, '机制解释必须默认收起').toBe(false)

    for (const mechanism of ['可打包 POS 凭据', 'pg_dump', 'deploy/README.md', '第 10.4 节', '更新作业前的本机回滚快照与最近一份永不自动删除']) {
      expect(folded, `「${mechanism}」应当住在折叠区里`).toContain(mechanism)
      expect(visible, `「${mechanism}」不该出现在默认态`).not.toContain(mechanism)
    }
    // 折叠区的内容仍在 DOM 里（不是删掉，只是收起）
    expect(all).toContain('deploy/README.md')
  })

  it('风险与后果类提示保留可见原文，破坏性按钮都是 danger 样式', async () => {
    const { wrapper } = await mountSection()
    const visible = visibleText(wrapper.element)

    // 口令遗失无法解密（导出面板）
    expect(visible).toContain('口令遗失将无法解密恢复，请牢记')
    // 清理的后果（保留与清理面板）
    expect(visible).toContain('保存后立即清理，超出份数的备份点会被删除')
    // 重置密码的后果
    expect(visible).toContain('重置后新密码只写入 env 文件，页面不显示明文')

    // 破坏性操作仍是危险按钮（清理 / 重置密码）
    expect(findButton(panelByLegend(wrapper, '保留与清理'), '立即清理').classes()).toContain('btn-danger')
    expect(findButton(panelByLegend(wrapper, '重置数据库密码'), '重置密码').classes()).toContain('btn-danger')
  })
})

describe('恢复的风险原文：损坏不可覆盖、PG 只能整库覆盖', () => {
  it('备份自身不一致 → 原文可见、阻止恢复且无法强制继续', async () => {
    const { wrapper } = await mountSection()
    await previewBackup(wrapper, corruptPreviewPayload())

    const restore = panelByLegend(wrapper, '恢复')
    const visible = visibleText(restore.element)
    expect(visible).toContain('这份备份自身不一致，已阻止恢复（无法强制继续）')
    expect(visible).toContain('归档校验和不一致，文件可能被改动')
    // 预览失败反馈也在恢复面板内（紧贴「预览」按钮）
    const alert = restore.find('.alert')
    expect(alert.text()).toContain('这份备份自身不一致，已阻止恢复')

    // 不能强制继续：恢复按钮禁用
    expect(findButton(restore, '确认恢复').attributes('disabled')).toBeDefined()
    expect(visible).not.toContain('我已知晓并强制继续')
  })

  it('PG 整库快照：恢复模式只给覆盖，且「整库覆盖不能合并」全节只出现一次', async () => {
    const { wrapper } = await mountSection()
    await previewBackup(wrapper)

    const visible = visibleText(wrapper.element)
    expect(visible).toContain('这份备份的业务数据是 PostgreSQL 整库快照，只能整库覆盖恢复，不能与现有数据合并')
    expect(visible).toContain('覆盖恢复 · 整库替换')
    expect(visible).not.toContain('合并去重追加')

    // 同一句话不再在导出面板复述一遍（连折叠区一起算，也只有一次）
    const all = wrapper.text()
    expect(occurrences(all, '整库覆盖')).toBe(1)
    expect(occurrences(all, '不能与现有数据合并')).toBe(1)
  })
})

describe('已知重复：各处只留一处', () => {
  it('.luyunbak 口令提示只在导出面板有一处', async () => {
    const { wrapper } = await mountSection()
    const all = wrapper.text()
    expect(occurrences(all, '口令遗失将无法解密恢复')).toBe(1)
    expect(occurrences(all, '由口令加密')).toBe(1)

    // 恢复面板不再重复解释 .luyunbak 与口令（它只管"上传 + 解密 + 预览"）
    const restore = visibleText(panelByLegend(wrapper, '恢复').element)
    expect(restore).not.toContain('.luyunbak 导出备份')
    expect(restore).not.toContain('口令加密')
    expect(restore).not.toContain('口令遗失')
  })

  it('「回滚前会自动再建一份快照」只在备份点列表里，本节不复述', async () => {
    const { wrapper } = await mountSection()
    expect(wrapper.text()).not.toContain('回滚前会自动')
    expect(wrapper.text()).not.toContain('自动再建')

    const list = readFileSync(join(BACKUP_DIR, 'BackupPointList.vue'), 'utf8')
    expect(occurrences(list, '回滚前会自动再建一份')).toBe(1)
  })

  it('清理预览与确认弹窗明细同一份数据：份数只在弹窗里，条数对得上', async () => {
    const { wrapper, host } = await mountSection()
    const retention = panelByLegend(wrapper, '保留与清理')

    // 分节里只列条目：2 份快照 + 1 份导出备份待删，2 份受保护
    const rows = retention.findAll('.cleanup-list li')
    const deletedRows = rows.filter((r) => r.text().includes('将删除'))
    expect(deletedRows).toHaveLength(3)
    expect(rows.filter((r) => r.text().includes('受保护'))).toHaveLength(2)
    // 份数摘要不在分节里写第二遍
    expect(visibleText(retention.element)).not.toMatch(/将删除本机回滚快照\s*\d+\s*份/)

    await findButton(retention, '立即清理').trigger('click')
    await settle()

    const confirm = host.controllers.confirm
    expect(confirm.state.open).toBe(true)
    const details = confirm.state.details.join(' ')
    expect(details).toContain('冷备：删除 0 份')
    expect(details).toContain('受保护的条目不会删除：2 份')
    // 弹窗里的份数 = 分节里列出的条目数（同一份 cleanupPreview）：导出的名字带 .luyunbak
    const exportDeleted = deletedRows.filter((r) => r.text().includes('.luyunbak')).length
    const snapshotDeleted = deletedRows.length - exportDeleted
    expect(details).toContain(`本机回滚快照：删除 ${snapshotDeleted} 份`)
    expect(details).toContain(`导出备份：删除 ${exportDeleted} 份`)
  })

  it('重置密码按钮的禁用原因不复述上面那块 alert：同一件事只说一遍', async () => {
    server.dbCred = dbCredPayload({ password_length: 0 })
    const { wrapper } = await mountSection()
    const dbPanel = panelByLegend(wrapper, '重置数据库密码')
    const text = dbPanel.text().replace(/\s+/g, '')

    // 无密码门店：上面那块 alert 已把"不用也不给重置"说清楚
    expect(text).toContain('当前连接串未包含密码（本机trust认证等），无需也无法在此重置')
    expect(occurrences(text, '当前连接串未包含密码')).toBe(1)
    // 按钮仍然禁用（禁用原因由那块 alert 承载，不再另写一行）
    expect(findButton(dbPanel, '重置密码').attributes('disabled')).toBeDefined()
  })

  it('操作在按钮上、结果在按钮下：清理预览不会把按钮顶走', async () => {
    const { wrapper } = await mountSection()
    const retention = panelByLegend(wrapper, '保留与清理')
    const preview = retention.find('.cleanup-preview')
    expect(preview.exists()).toBe(true)

    for (const label of ['刷新', '保存保留配置', '立即清理']) {
      expect(isAfter(findButton(retention, label).element, preview.element), `「${label}」应当在清理预览之前`).toBe(true)
    }
  })
})

describe('写操作反馈：落在本分节内、紧贴触发它的按钮', () => {
  it('导出成功 / 失败都出现在导出面板、按钮下方', async () => {
    const { wrapper } = await mountSection()
    const exportPanel = panelByLegend(wrapper, '导出备份')
    const button = findButton(exportPanel, '生成并下载备份')

    // 失败：服务端拒绝
    server.exportStartError = apiError('磁盘空间不足', { status: 507 })
    await fillExportPassphrase(wrapper)
    await button.trigger('click')
    await settle()
    let alert = exportPanel.find('.alert')
    expect(alert.text()).toContain('导出失败：磁盘空间不足')
    expect(isAfter(button.element, alert.element)).toBe(true)

    // 成功：任务式导出跑完 → 就地给回执（并已发起下载）
    server.exportStartError = null
    await fillExportPassphrase(wrapper)
    await button.trigger('click')
    await settle()
    alert = exportPanel.find('.alert')
    expect(alert.text()).toContain('已开始下载加密导出备份')
    expect(isAfter(button.element, alert.element)).toBe(true)
    expect(apiDownloadUrl).toHaveBeenCalled()

    // 反馈不外溢：别的动作区不跟着亮
    expect(panelByLegend(wrapper, '恢复').find('.alert').exists()).toBe(false)
    expect(panelByLegend(wrapper, '保留与清理').find('.alert').exists()).toBe(false)
  })

  it('恢复：预览失败与确认恢复失败都落在恢复面板内', async () => {
    const { wrapper, host } = await mountSection()
    const restore = panelByLegend(wrapper, '恢复')

    // 预览阶段失败（解密错）
    server.importPreviewError = apiError('口令不正确', { status: 400 })
    await pickBackupFile(wrapper)
    await wrapper.find('#importPass').setValue('bad')
    const previewButton = findButton(restore, '预览')
    await previewButton.trigger('click')
    await settle()
    let alert = restore.find('.alert')
    expect(alert.text()).toContain('预览失败：口令不正确')
    expect(isAfter(previewButton.element, alert.element)).toBe(true)

    // 预览成功后走两步确认：确认恢复失败 → 反馈仍落在恢复面板（不是备份点那一块）
    server.importPreviewError = null
    await previewBackup(wrapper)
    const applyButton = findButton(restore, '确认恢复')
    await applyButton.trigger('click')
    await settle()

    server.applyError = apiError('恢复过程中断', { status: 500 })
    await confirmThroughShell(host)
    // 恢复面板里有两条反馈条（预览 / 确认恢复各一条），失败那条要落在「确认恢复」按钮之后
    const failureAlert = restore.findAll('.alert').find((a) => a.text().includes('恢复失败'))
    expect(failureAlert, '恢复失败的就地反馈应当在恢复面板里').toBeTruthy()
    expect(failureAlert.text()).toContain('恢复失败：恢复过程中断')
    expect(isAfter(applyButton.element, failureAlert.element)).toBe(true)
    // 备份状态 / 备份点那一条没有跟着亮
    expect(wrapper.find('.settings-section > .alert').exists()).toBe(false)
  })

  it('保存保留配置 / 立即清理的成功与失败都落在保留与清理面板内', async () => {
    const { wrapper, host } = await mountSection()
    const retention = panelByLegend(wrapper, '保留与清理')

    // 保存失败
    server.retentionSaveError = apiError('写入被拒绝', { status: 500 })
    await findButton(retention, '保存保留配置').trigger('click')
    await settle()
    await confirmThroughShell(host)
    let alert = retention.find('.alert')
    expect(alert.text()).toContain('保存保留配置失败：写入被拒绝')

    // 立即清理：确认后成功
    server.retentionSaveError = null
    await findButton(retention, '立即清理').trigger('click')
    await settle()
    await confirmThroughShell(host)
    alert = retention.find('.alert')
    expect(alert.text()).toContain('已清理 1 个备份点')
    expect(isAfter(findButton(retention, '立即清理').element, alert.element)).toBe(true)

    // 清理失败
    server.cleanupError = apiError('磁盘忙', { status: 500 })
    await findButton(retention, '立即清理').trigger('click')
    await settle()
    await confirmThroughShell(host)
    alert = retention.find('.alert')
    expect(alert.text()).toContain('清理失败：磁盘忙')
  })

  it('重置数据库密码：成功与失败都落在重置面板、按钮下方', async () => {
    const { wrapper, host } = await mountSection()
    const dbPanel = panelByLegend(wrapper, '重置数据库密码')
    const button = findButton(dbPanel, '重置密码')

    // 失败：后台超管密码不对（弹窗里报错，同时本节给一条就地回执）
    server.dbResetError = apiError('密码不正确', { status: 403 })
    await button.trigger('click')
    await settle()
    host.controllers.dbReset.state.password = 'wrong'
    await host.controllers.dbReset.submit()
    await settle()
    let alert = dbPanel.find('.alert')
    expect(alert.text()).toContain('重置未确认：密码不正确')
    expect(isAfter(button.element, alert.element)).toBe(true)

    // 成功：新密码只写入 env 文件，页面给结果块 + 就地回执
    server.dbResetError = null
    await button.trigger('click')
    await settle()
    host.controllers.dbReset.state.password = 'admin-pass'
    await host.controllers.dbReset.submit()
    await settle()
    alert = dbPanel.find('.alert')
    expect(alert.text()).toContain('数据库密码已重置')
    expect(dbPanel.text()).toContain('/etc/luyun/env.production')
    expect(isAfter(button.element, alert.element)).toBe(true)
  })

  it('三个弹窗注册给壳，本节不注册 token', async () => {
    const { host } = await mountSection()
    expect(Object.keys(host.controllers).sort()).toEqual(['confirm', 'dbReset', 'importSuccess'])
    expect(host.controllers.confirm.state).toBeTruthy()
    // 两步确认是共用通道：request 就是 composable 那条 requestConfirm，采集/账号节的危险动作也走它
    expect(host.controllers.confirm.request).toBeTypeOf('function')
    expect(host.controllers.confirm.submit).toBeTypeOf('function')
    expect(host.controllers.dbReset.copy().title).toBe('重置数据库密码')
    expect(host.controllers.importSuccess.copy().title).toBe('恢复完成')
  })
})
