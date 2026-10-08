// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import StatusSection from '../StatusSection.vue'

// 状态节（原「系统健康状态」，只读）的**契约测试**，两层：
//
//   A. 源码级：默认态文案预算（无条件渲染的说明性文字 ≤ 100 字）、机制解释只在折叠里、
//      八张卡的顺序与皮、390px 的断行规则；
//   B. 挂载级（jsdom + @vue/test-utils，同 t4/t6 的做法）：喂真 /api/healthz 响应，
//      逐条量「数据库」四态与「Redis 总线」三态的结论文字与色调，以及两条严重度判据
//      ——数据库问题进总体结论、Redis 掉线不进（这两条写反了最要命）。
//
// 挂载用的是真 composable（只桩掉 api/client），所以 503 带 body 的取数路径
// （readResult 的 acceptErrorBody）也一并被测到。

const { api } = vi.hoisted(() => ({ api: { get: vi.fn() } }))
vi.mock('../../../api/client', () => ({ api }))

const here = dirname(fileURLToPath(import.meta.url))
const FILE = join(here, '../StatusSection.vue')
const src = readFileSync(FILE, 'utf8')
const template = src.slice(src.indexOf('<template>'), src.indexOf('<style'))
const script = src.slice(0, src.indexOf('</script>'))
const style = src.slice(src.indexOf('<style'), src.indexOf('</style>'))
const css = style.replace(/\s+/g, '')
const composable = readFileSync(join(here, '../../../composables/useSystemHealth.js'), 'utf8')

function stripTags(text) {
  return text.replace(/<[^>]+>/g, '').replace(/\s+/g, '')
}

/** 折叠区整段去掉（summary 留下）：默认态不该看到折叠里的机制解释。 */
function withoutDetails(source) {
  return source.replace(/<details[\s\S]*?<\/details>/g, (block) => {
    const summary = block.match(/<summary[^>]*>[\s\S]*?<\/summary>/)
    return summary ? summary[0] : ''
  })
}

/** 说明性文字：空态/风险 hint 与折叠 summary；带 v-if/v-else 的算「状态反馈」。 */
function proseLines(source) {
  const unfolded = withoutDetails(source)
  const lines = []
  for (const m of unfolded.matchAll(/<p\b([^>]*)>([\s\S]*?)<\/p>/g)) {
    const attrs = m[1]
    if (!/class="[^"]*(hint|health-warn)/.test(attrs)) continue
    lines.push({ text: stripTags(m[2]), conditional: /\bv-(if|else)/.test(attrs) })
  }
  for (const m of unfolded.matchAll(/<summary[^>]*>([\s\S]*?)<\/summary>/g)) {
    lines.push({ text: stripTags(m[1]), conditional: false })
  }
  return lines.filter((line) => line.text)
}

// ---- 夹具：/api/healthz 与其余四个只读端点 ----
const READY_OK = {
  status: 'healthy', ready: true, db_connected: true, migrations_complete: true,
  key_tables_readable: true, version: '0.9.9', started_at: '2026-10-01T10:20:30',
  startup_id: 'b2f1c0de', timestamp: '2026-10-08T02:00:00', details: ['全部通过'],
}
const PROCESS_OK = {
  status: 'running', uptime: 987654, version: '0.9.9',
  database: { orders: { count: 128394 }, tables: { count: 42 }, dish_stations: { count: 187 } },
  memory: {
    current_usage: { rss_mb: 384.2 }, peak_memory_mb: 612.7, pressure_level: 'normal',
    thresholds: { warning_mb: 512, cleanup_mb: 768, critical_mb: 1024 },
    last_cleanup: '2026-10-07T22:10:00', cleanup_count: 12, gc_collections: 431,
  },
  disk: {
    level: 'ok', threshold_free_mb: 1024,
    worst: { path: '/System/Volumes/Data', free_mb: 89549.4, total_mb: 1000245.0, used_mb: 910695.6, used_pct: 91.0, level: 'ok' },
    paths: [{ path: '/System/Volumes/Data', free_mb: 89549.4, total_mb: 1000245.0, used_pct: 91.0, level: 'ok' }],
  },
}
const SCRAPER_OK = {
  success: true,
  health: {
    biz_date: '2026-10-08', api_failures: 0, api_failures_threshold: 5, delivery_bills_pending: 3,
    last_scrape_at: '2026-10-08T01:58:20', updated_at: '2026-10-08T01:58:30', reconcile_running: false,
    last_reconcile: { at: '2026-10-08T01:30:00', missed_qty: 0, miss_rate_pct: 0.0, report_md: 'data/reconcile/2026-10-08.md' },
  },
}
const RECONCILE_IDLE = { progress: { running: false, current: 0, total: 0, stage_label: '' } }

/** 一切正常的 healthz：db healthy + 磁盘 ok + Redis 已连上。 */
const HEALTHZ_OK = { status: 'ok', db: 'healthy', disk: { level: 'ok', free_mb: 89549.4 }, redis: { configured: true, connected: true } }

/** 桩掉 api/client。healthz 传 { body, status }：非 2xx 要把 body 挂在 err.data 上，真 client 就是这么做的。 */
function mockApi(healthzFixture = { body: HEALTHZ_OK, status: 200 }) {
  api.get.mockImplementation(async (path) => {
    if (path === '/api/system/health') return READY_OK
    if (path === '/api/healthz') {
      if (healthzFixture.status !== 200) {
        throw Object.assign(new Error('未就绪'), { status: healthzFixture.status, data: healthzFixture.body })
      }
      return healthzFixture.body
    }
    if (path === '/api/system/status') return PROCESS_OK
    if (path === '/api/system/scraper-health') return SCRAPER_OK
    if (path === '/api/admin/reconcile-status') return RECONCILE_IDLE
    throw new Error(`未桩的端点：${path}`)
  })
}

async function mountSection(healthzFixture) {
  mockApi(healthzFixture)
  const wrapper = mount(StatusSection, { props: { active: true } })
  await flushPromises()
  return wrapper
}

const CARD_ORDER = ['health-card-disk', 'health-card-memory', 'health-card-failures', 'health-card-counts',
  'health-card-db', 'health-card-readiness', 'health-card-redis', 'health-card-reconcile']

function cardAt(wrapper, id) {
  return wrapper.find(`[aria-labelledby="${id}"]`)
}

function stateOf(wrapper, id) {
  const pill = cardAt(wrapper, id).find('.pill')
  return { label: pill.text(), tone: pill.classes().find((c) => c.startsWith('is-')) }
}

beforeEach(() => {
  api.get.mockReset()
})

describe('状态节 · 默认态文案', () => {
  it('无条件渲染的说明性文字合计 ≤ 100 字，单条 ≤ 25 字', () => {
    const always = proseLines(template).filter((line) => !line.conditional)
    expect(always.length).toBeGreaterThan(0)
    for (const line of always) {
      expect([...line.text].length, `这条太长了：${line.text}`).toBeLessThanOrEqual(25)
    }
    const total = always.reduce((sum, line) => sum + [...line.text].length, 0)
    expect(total, `默认态文案合计 ${total} 字：\n${always.map((l) => l.text).join('\n')}`).toBeLessThanOrEqual(100)
  })

  it('状态反馈（条件渲染）各自只在自己那一档出现，静态合计仍在上限内', () => {
    const conditional = proseLines(template).filter((line) => line.conditional)
    for (const line of conditional) expect([...line.text].length).toBeLessThanOrEqual(25)
    // 含 t10 新增的两卡四条与 t3 保留的空态、磁盘风险；这些句子各在自己那一档出现，
    // 不同时上屏：实测最坏一屏（db 重连 + Redis 掉线 + 磁盘严重）97 字，仍 ≤ 100；
    // 唯一例外是 db error 态那段被截断到 80 字的原始异常（t10 明确要求的排查原文）。
    const total = conditional.reduce((sum, line) => sum + [...line.text].length, 0)
    expect(total, `状态反馈静态合计 ${total} 字：\n${conditional.map((l) => l.text).join('\n')}`).toBeLessThanOrEqual(200)
    expect(template).toContain("v-if=\"dbState?.key === 'reconnecting'\"")
    expect(template).toContain("v-if=\"redisState?.key === 'offline'\"")
  })

  it('图表实现细节与聚合机制一句不留', () => {
    for (const gone of ['只读聚合', '等比', '量表', '本页不触发', '图表为', '报告：', '以右侧数字为准']) {
      expect(template, `${gone} 不该留在默认态`).not.toContain(gone)
    }
    // 「删掉」而不是「挪进折叠」：折叠里本来就有这些数据（分区清单 / 对账报告组），
    // 断言它们的来源还在，删卡片上那句不算丢信息。
    expect(composable).toContain("title: '磁盘分区'")
    expect(composable).toContain("title: '对账报告'")
    expect(composable).toContain("title: '进程与资源'")
  })

  it('明细收进默认折叠的 <details>，并带看得见的开合标记', () => {
    expect(template.match(/<details\b/g)?.length).toBe(1)
    expect(template).toContain('<details v-if="sysHealthRawFacts.length" class="section-help">')
    expect(template.match(/<details[^>]*\sopen/)).toBeNull()
    expect(template).toContain('name="chevron-right"')
    expect(template).toContain('<span>全部原始指标</span>')
    expect(css).toContain('.section-help[open].section-help__icon{transform:rotate(90deg);}')
  })
})

describe('状态节 · 两张依赖组件卡', () => {
  it('八张卡并列，顺序为 数据量 → 数据库 → 就绪检查 → Redis 总线 → 对账进度', async () => {
    const wrapper = await mountSection()
    const ids = wrapper.findAll('.health-card').map((card) => card.attributes('aria-labelledby'))
    expect(ids).toEqual(CARD_ORDER)
    // 依赖组件紧挨着它们影响的那张就绪卡（前一后一）。
    expect(ids.indexOf('health-card-db')).toBe(ids.indexOf('health-card-readiness') - 1)
    expect(ids.indexOf('health-card-redis')).toBe(ids.indexOf('health-card-readiness') + 1)
    // 同一套皮：卡头图标 + 结论胶囊。
    for (const id of ['health-card-db', 'health-card-redis']) {
      expect(cardAt(wrapper, id).find('.health-card__title svg').exists()).toBe(true)
      expect(cardAt(wrapper, id).find('.pill').exists()).toBe(true)
    }
    wrapper.unmount()
  })

  it('数据库四态：healthy → 绿「已连接」，并写明每次都是实跑探测', async () => {
    const wrapper = await mountSection()
    expect(stateOf(wrapper, 'health-card-db')).toEqual({ label: '已连接', tone: 'is-ok' })
    expect(cardAt(wrapper, 'health-card-db').text()).toContain('每次检查都实跑一次连库探测。')
    wrapper.unmount()
  })

  it('数据库四态：reconnecting → 黄「重连中」+ 后果说明（503 带 body 也拿得到值）', async () => {
    // 重连窗口内 healthz 返回 503，但 db/disk/redis 都在 body 里 —— 真 client 把 body 挂在 err.data 上。
    const wrapper = await mountSection({ body: { ...HEALTHZ_OK, status: 'degraded', db: 'reconnecting' }, status: 503 })
    expect(stateOf(wrapper, 'health-card-db')).toEqual({ label: '重连中', tone: 'is-warn' })
    expect(cardAt(wrapper, 'health-card-db').text()).toContain('连库请求会被中间件挡下返回 503，恢复后自动放行。')
    expect(cardAt(wrapper, 'health-card-db').classes()).toContain('is-warning')
    wrapper.unmount()
  })

  it('数据库四态：uninitialized → 中性「未初始化」', async () => {
    const wrapper = await mountSection({ body: { ...HEALTHZ_OK, status: 'degraded', db: 'uninitialized' }, status: 503 })
    expect(stateOf(wrapper, 'health-card-db')).toEqual({ label: '未初始化', tone: 'is-neutral' })
    wrapper.unmount()
  })

  it('数据库四态：error:… 与裸 error 都是红「连接失败」+ 截断原文', async () => {
    const longError = `error: ${'连接被拒绝 '.repeat(40)}`
    const wrapper = await mountSection({ body: { ...HEALTHZ_OK, status: 'degraded', db: longError }, status: 503 })
    expect(stateOf(wrapper, 'health-card-db')).toEqual({ label: '连接失败', tone: 'is-error' })
    const card = cardAt(wrapper, 'health-card-db')
    expect(card.classes()).toContain('is-critical')
    const err = card.find('.hint.is-error')
    expect(err.text()).toContain('原始异常：')
    expect(err.text().endsWith('…')).toBe(true)
    expect([...err.text()].length).toBeLessThanOrEqual(150)
    wrapper.unmount()

    // health_check 自己吞下异常时上报的是裸 'error'（db_core/stats.py）——同样是连接失败。
    const bare = await mountSection({ body: { ...HEALTHZ_OK, status: 'degraded', db: 'error' }, status: 503 })
    expect(stateOf(bare, 'health-card-db')).toEqual({ label: '连接失败', tone: 'is-error' })
    bare.unmount()
  })

  it('Redis 三态：connected → 绿「已连接」，并写明只读状态位', async () => {
    const wrapper = await mountSection()
    expect(stateOf(wrapper, 'health-card-redis')).toEqual({ label: '已连接', tone: 'is-ok' })
    expect(cardAt(wrapper, 'health-card-redis').text()).toContain('只读状态位，不主动 ping。')
    wrapper.unmount()
  })

  it('Redis 三态：configured 但没连上 → 黄「已掉线」+ 影响面', async () => {
    const wrapper = await mountSection({ body: { ...HEALTHZ_OK, redis: { configured: true, connected: false } }, status: 200 })
    expect(stateOf(wrapper, 'health-card-redis')).toEqual({ label: '已掉线', tone: 'is-warn' })
    expect(cardAt(wrapper, 'health-card-redis').text()).toContain('跨进程实时更新失效，本机功能不受影响。')
    expect(cardAt(wrapper, 'health-card-redis').classes()).toContain('is-warning')
    wrapper.unmount()
  })

  it('Redis 三态：configured=false → 中性「未启用」+ 只在哪种形态出现', async () => {
    const wrapper = await mountSection({ body: { ...HEALTHZ_OK, redis: { configured: false, connected: false } }, status: 200 })
    expect(stateOf(wrapper, 'health-card-redis')).toEqual({ label: '未启用', tone: 'is-neutral' })
    expect(cardAt(wrapper, 'health-card-redis').text()).toContain('只在未起订阅任务时出现，正常部署启动期就拦住。')
    wrapper.unmount()
  })

  it('严重度不得写反：Redis 掉线只标黄，总体结论仍是健康', async () => {
    const wrapper = await mountSection({ body: { ...HEALTHZ_OK, redis: { configured: true, connected: false } }, status: 200 })
    const card = cardAt(wrapper, 'health-card-redis')
    expect(card.classes()).not.toContain('is-critical')
    expect(stateOf(wrapper, 'health-card-redis').tone).not.toBe('is-error')
    // 本地派发不经过总线：采集 / 打印 / KDS 正常，Redis 不进 worse()，总体仍是健康。
    expect(wrapper.find('.health-overview__pill').text()).toBe('总体健康')
    wrapper.unmount()
  })

  it('严重度不得写反：数据库重连（后端 status=degraded）把总体推到「注意」', async () => {
    const wrapper = await mountSection({ body: { ...HEALTHZ_OK, status: 'degraded', db: 'reconnecting' }, status: 503 })
    expect(wrapper.find('.health-overview__pill').text()).toBe('总体注意')
    wrapper.unmount()
  })

  it('没拿到 healthz 数据时两张卡给空态文案，不抛错', async () => {
    // healthz 直接失败且没有 body（断网 / 500 无 body）→ sysHealthProbe 为 null。
    const wrapper = await mountSection({ body: undefined, status: 500 })
    expect(cardAt(wrapper, 'health-card-db').find('.pill').exists()).toBe(false)
    expect(cardAt(wrapper, 'health-card-redis').find('.pill').exists()).toBe(false)
    expect(cardAt(wrapper, 'health-card-db').text()).toContain('未获取到数据库状态。')
    expect(cardAt(wrapper, 'health-card-redis').text()).toContain('未获取到 Redis 状态。')
    expect(wrapper.findAll('.health-card')).toHaveLength(8)
    wrapper.unmount()
  })

  it('去重：就绪卡里那行同源的「数据库」没了，探针结论与折叠里的原始串都还在', async () => {
    const wrapper = await mountSection({ body: { ...HEALTHZ_OK, status: 'degraded', db: 'error: boom' }, status: 503 })
    const facts = cardAt(wrapper, 'health-card-readiness').find('.health-facts')
    expect(facts.findAll('dt').map((d) => d.text())).toEqual(['探针结论'])
    // 就绪卡自己的 facts 不再带探针的 db 那一行（网格里的「数据库连接 / 数据库迁移」是
    // 四项就绪检查，不是同一事实）。
    expect(facts.text()).not.toContain('数据库')
    expect(facts.text()).toContain('探针结论')
    // 排查用的原文留在折叠区（composable 的「访问探针」组），不算默认态重复。
    const raw = wrapper.find('details.section-help').text()
    expect(raw).toContain('数据库')
    expect(raw).toContain('error: boom')
    expect(raw).toContain('探针结论')
    wrapper.unmount()
  })

  it('两张卡只读、不新增请求：取数仍只用已在拉的那五个端点，总体结论不读 redis', async () => {
    const wrapper = await mountSection()
    for (const id of ['health-card-db', 'health-card-redis']) {
      expect(cardAt(wrapper, id).find('button').exists()).toBe(false)
    }
    expect(wrapper.findAll('.alert')).toHaveLength(0)
    expect(api.get.mock.calls.map(([path]) => path)).toEqual([
      '/api/system/health', '/api/healthz', '/api/system/status', '/api/system/scraper-health', '/api/admin/reconcile-status',
    ])
    // composable 一字未改：总体结论只读 probe.status（数据库问题借此进结论），从不读 redis。
    const overall = composable.slice(composable.indexOf('const sysHealthOverall = computed'),
      composable.indexOf('const sysHealthOverallLabel'))
    expect(overall).toContain('sysHealthProbe.value.status')
    expect(overall).not.toContain('redis')
    wrapper.unmount()
  })
})

describe('状态节 · 反馈仍在触发它的按钮那一块', () => {
  it('加载与失败反馈走 HealthSummary：按钮上的「检查中…」+ 条内错误行', () => {
    expect(template).toContain('<HealthSummary')
    expect(template).toContain(':loading="sysHealthLoading"')
    expect(template).toContain(':error="sysHealthError"')
    expect(template).toContain('@refresh="loadSysHealth"')
    expect(script).toContain('sysHealthLoading')
    expect(script).toContain('sysHealthError')
    expect(script).toContain('loadSysHealth')
  })

  it('本节是只读的：不挂空提示条、不直连接口', () => {
    expect(script).not.toContain('const alert = reactive(')
    expect(script).not.toContain('function showAlert(')
    expect(script).not.toContain('function clearAlert(')
    expect(template).not.toContain('v-if="alert.show"')
    expect(src).not.toMatch(/\bapi\.(get|post|put|patch|delete)\(/)
    // 契约注释里写死了「为什么没有提示条」，别把这一行改掉。
    expect(script).toContain('不渲染就地提示条')
  })
})

describe('状态节 · 390px 与契约保持', () => {
  it('卡片宽屏自适应、窄屏单列，长路径与 facts 都能断行', () => {
    expect(css).toContain('.health-cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr))')
    expect(css).toContain('.health-cards{grid-template-columns:minmax(0,1fr);}')
    expect(css).toContain('.health-card{display:flex;flex-direction:column;gap:8px;min-width:0')
    // facts 的值（长路径、时间戳）与折叠里的键（整条分区路径）都能折行。
    expect(css).toContain('.health-factsdd{')
    expect(css).toContain('overflow-wrap:anywhere;')
    expect(css).toMatch(/\.health-factsdt\{color:var\(--text-dim\);min-width:0;overflow-wrap:anywhere;\}/)
    // 卡片里的标签一行且不收缩（收缩会让标签比文字还窄、压到值上）。
    expect(css).toMatch(/\.health-card\.health-factsdt\{white-space:nowrap;flex:00auto;\}/)
    // 空态/风险文案也可能是长串（错误信息），一律能断。
    expect(css).toMatch(/\.hint\{[^}]*overflow-wrap:anywhere;\}/)
    // 两张依赖卡：标题 + 胶囊同一行、可换行；卡片按档位描边（色彩只是辅助）。
    expect(css).toMatch(/\.health-card__head\{display:flex;align-items:center;gap:8px;flex-wrap:wrap;\}/)
    expect(css).toContain('.health-card.is-critical{border-color:rgba(239,68,68,0.4);}')
    // 本节不造横向滚动、不写死像素宽度。
    expect(css).not.toContain('overflow-x:auto')
    expect(template).not.toMatch(/width:\s*\d{3,}px/)
  })

  it('壳的分节契约一条不落（props / 自取数据 / 无 emit / 样式范围）', () => {
    expect(script).toContain('原「系统健康状态」')
    expect(script).toContain('active: { type: Boolean, default: false }')
    expect(script).toContain('() => props.active')
    expect(script).toContain('{ immediate: true }')
    expect(script).toContain('useSystemHealth({})')
    expect(src).not.toContain('defineEmits')
    expect(src).not.toContain('$emit')
    expect(src).not.toContain('SETTINGS_MODAL_HOST')
    expect(src).toContain('<style scoped>')
    expect(src.match(/<style(?! scoped)/)).toBeNull()
  })
})
