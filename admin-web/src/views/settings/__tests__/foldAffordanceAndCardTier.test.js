import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

// t16：t15 复核的两条 low finding。
//
// F15-03 全页 16 个折叠里有 6 个**没有任何开合提示** —— 根因是 `summary { display: flex }`
// 之后 Chromium 连原生三角都不渲染，于是它们看起来像普通小标题。四个在分节自己的文件里
// （数据节 2 个 `.more`、系统节「完整原文」与「GitHub 连接」），补的是与 `.section-help`
// 同一套 chevron 皮；另外两个（备份点列表的两个分组头）由子组件渲染、不在本目录，
// 只能从数据节用 `:deep()` 把 `display` 改回 `list-item` 让原生三角回来（t15 的建议之一），
// 高度仍吃 t13 那条 44px。
//
// F15-04 同一网格里「这张卡不正常」有两种表达：数据库 / Redis 两张卡用 `.is-warning/.is-critical`
// 描边，六张量表卡从不描边、只用 `.health-warn` 色块。本次把有档位来源的四张接上同一套语法；
// **量表卡的档位一个字都没新算** —— 磁盘 / 内存 / 采集失败直接用各自量表已有的 `level`，
// 就绪检查用既有的 `ready` 与 `hasFailure`。另外两张（数据量 / 对账进度）没有档位来源
// （`buildCountBars`、`buildReconcileProgress` 都不产出 level），编一个阈值属于新增判据，
// 本票不做 —— 下面把这条也钉住，免得后人以为漏了。
const here = dirname(fileURLToPath(import.meta.url))
const SETTINGS = join(here, '..')
const read = (name) => readFileSync(join(SETTINGS, name), 'utf8')
const charts = readFileSync(join(here, '../../../utils/systemHealthCharts.js'), 'utf8')
const health = readFileSync(join(here, '../../../composables/useSystemHealth.js'), 'utf8')

function compact(source) {
  return source.replace(/\s+/g, '')
}

const collect = read('CollectSection.vue')
const status = read('StatusSection.vue')
const data = read('DataSection.vue')
const system = read('SystemSection.vue')
const account = read('AccountSection.vue')
const sections = { collect, status, data, system, account }

describe('折叠的开合提示（F15-03）：6 处补上可见标记', () => {
  it('数据节的两个 .more 用与 .section-help 同一套 chevron', () => {
    const src = compact(data)
    expect(src).toContain('<summary><SvgIconname="chevron-right":size="14"class="more__icon"/><span>这个包里装了什么</span></summary>')
    expect(src).toContain('<summary><SvgIconname="chevron-right":size="14"class="more__icon"/><span>保留与清理怎么算</span></summary>')
    // 皮：暗色 → 展开提亮 + 转 90°
    expect(src).toContain('.more__icon{color:var(--text-dim);transition:transform0.15sease;}')
    expect(src).toContain('.more[open]>summary,.more[open].more__icon{color:var(--text);}')
    expect(src).toContain('.more[open].more__icon{transform:rotate(90deg);}')
  })

  it('系统节的「完整原文」与「GitHub 连接」同样', () => {
    const src = compact(system)
    expect(src).toContain('<summary><SvgIconname="chevron-right":size="14"class="history-error__raw__icon"/><span>完整原文</span></summary>')
    expect(src).toContain('<summary><SvgIconname="chevron-right":size="14"class="github-panel__icon"/><span>GitHub连接（公开仓通常无需配置）</span></summary>')
    expect(src).toContain('.history-error__raw__icon{color:var(--text-dim);transition:transform0.15sease;}')
    expect(src).toContain('.history-error__raw[open].history-error__raw__icon{transform:rotate(90deg);}')
    expect(src).toContain('.github-panel__icon{color:var(--text-dim);transition:transform0.15sease;}')
    expect(src).toContain('.github-panel[open].github-panel__icon{transform:rotate(90deg);}')
  })

  it('备份点列表的两个分组头由数据节用 :deep() 恢复原生三角', () => {
    // 这两个 summary 在 components/backup/BackupPointList.vue 里（t16 范围外），
    // 高度与样式写在它的 scoped 里；父级只能这样压过去（(0,3,1) > 子组件 scoped 的 (0,2,1)）。
    const src = compact(data)
    expect(src).toContain('.settings-section:deep(.points__shared>summary),.settings-section:deep(.points__excluded>summary){display:list-item;padding-top:11px;transition:color0.15sease;}')
    expect(src).toContain('.settings-section:deep(.points__shared[open]>summary),.settings-section:deep(.points__excluded[open]>summary){color:var(--text);}')
  })

  it('分节文件里每个 <summary> 都带 chevron（子组件的那两个另有 :deep() 兜着）', () => {
    for (const [name, src] of Object.entries(sections)) {
      const summaries = (src.match(/<summary/g) || []).length
      const chevrons = (src.match(/name="chevron-right"/g) || []).length
      expect(chevrons, `${name}: ${summaries} 个 summary / ${chevrons} 个 chevron`).toBe(summaries)
    }
  })

  it('chevron 的旋转动画尊重 prefers-reduced-motion', () => {
    expect(compact(data)).toContain('@media(prefers-reduced-motion:reduce){.more__icon{transition:none;}')
    expect(compact(data)).toContain('.settings-section:deep(.points__excluded>summary){transition:none;}}')
    expect(compact(system)).toContain('.section-help__icon,.history-error__raw__icon,.github-panel__icon{transition:none;}')
    expect(compact(collect)).toContain('@media(prefers-reduced-motion:reduce){.section-help__icon{transition:none;}}')
    expect(compact(status)).toContain('@media(prefers-reduced-motion:reduce){.section-help__icon{transition:none;}}')
  })
})

describe('卡片描边档位（F15-04）：八张卡同一套「不正常」语法', () => {
  it('四张有档位来源的量表卡接上同一套描边', () => {
    const src = compact(status)
    expect(src).toContain(':class="tierClass(sysHealthDiskGauge.level)"')
    expect(src).toContain(':class="tierClass(sysHealthMemoryGauge.level)"')
    expect(src).toContain(':class="tierClass(sysHealthFailureGauge.level)"')
    expect(src).toContain(':class="tierClass(readinessTier)"')
    // 语法只有一套：与数据库 / Redis 两张卡共用的 .is-warning / .is-critical。
    expect(src).toContain('functiontierClass(level){returnlevel===\'warning\'||level===\'critical\'?`is-${level}`:\'\'}')
    expect(src).toContain('.health-card.is-warning{border-color:rgba(245,158,11,0.35);}')
    expect(src).toContain('.health-card.is-critical{border-color:rgba(239,68,68,0.4);}')
    // 数据库 / Redis 的映射不回退。
    expect(src).toContain(':class="dbState?`is-${dbState.level}`:\'\'"')
    expect(src).toContain(':class="redisState?`is-${redisState.level}`:\'\'"')
  })

  it('就绪卡的档位只读既有信号（ready 与 hasFailure），没有新阈值', () => {
    const src = compact(status)
    const tier = src.slice(src.indexOf('constreadinessTier=computed('), src.indexOf('/**Redisthreestates'))
    expect(tier).toContain('if(!sysHealthReady.value)return\'unknown\'')
    expect(tier).toContain('if(sysHealthReady.value.ready===false)return\'critical\'')
    expect(tier).toContain('returnsysHealthReadyHasFailure.value?\'warning\':\'ok\'')
    // 判据来源仍然是 composable 里那两个（这里只是读它们）。
    expect(compact(health)).toContain('constsysHealthReadyHasFailure=computed(()=>sysHealthReadyChecks.value.some((c)=>!c.ok))')
  })

  it('判据本身一字未改：阈值表与三张量表的 level 判定仍是原样', () => {
    const src = compact(charts)
    expect(src).toContain("constDISK_LEVEL_LABELS={ok:'充足',warning:'偏低',critical:'严重不足',unknown:'未知'}")
    expect(src).toContain("constPRESSURE_LEVELS={normal:'ok',warning:'warning',high:'warning',critical:'critical',unknown:'unknown'}")
    expect(src).toContain("constFAILURE_LEVEL_LABELS={ok:'正常',warning:'有失败',critical:'超阈值',unknown:'未获取'}")
    expect(src).toContain('if(threshold!==null&&failures>=threshold)level=\'critical\'')
    expect(src).toContain('elseif(failures>0)level=\'warning\'')
    expect(src).toContain('constlevel=DISK_LEVEL_LABELS[d.level]?d.level:\'unknown\'')
  })

  it('数据量 / 对账进度不描边（它们没有档位来源，编一个就是新增判据）', () => {
    const src = compact(status)
    const counts = src.slice(src.indexOf('aria-labelledby="health-card-counts"') - 120, src.indexOf('aria-labelledby="health-card-counts"'))
    const reconcile = src.slice(src.indexOf('aria-labelledby="health-card-reconcile"') - 120, src.indexOf('aria-labelledby="health-card-reconcile"'))
    expect(counts).not.toContain('tierClass(')
    expect(reconcile).not.toContain('tierClass(')
    // 依据：这两条数据链都不产出 level。
    expect(compact(charts)).toContain('exportfunctionbuildCountBars')
    expect(compact(health)).toContain('buildCountBars([{key:\'orders\'')
    expect(compact(health)).toContain('constsysHealthReconcileProgress=computed(()=>buildReconcileProgress(')
  })
})
