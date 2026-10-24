import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))

function read(rel) {
  return readFileSync(join(here, rel), 'utf8')
}

const view = read('../HygieneDataView.vue')

describe('hygiene data & photo admin (ADR-0087)', () => {
  it('导航里有第 7 项，并且挂在自己的路由上', () => {
    const copy = read('../../../utils/hygieneCopy.js')
    expect(copy).toMatch(/path: '\/workbench\/floor\/data'/)
    expect(copy).toMatch(/title: '数据与照片'/)
    const router = read('../../../router/index.js')
    expect(router).toMatch(
      /hygieneAdminPage\('\/workbench\/floor\/data', 'workbench-floor-data', \(\) => import\('\.\.\/views\/hygiene\/HygieneDataView\.vue'\)\)/,
    )
  })

  it('列表按营业日区间、类型与工作区查询，并带分页', () => {
    expect(view).toMatch(/api\.get\('\/api\/hygiene\/admin\/data\/records'/)
    // 默认走两个日期框；勾「近一年」时给明确下界 = 今天-365 天（后端传 None 只回最近 30 天；
    // 而传 2000-01-01 会撞上 MAX_RANGE_DAYS = 366 的 400 range_too_wide）
    expect(view).toMatch(/const date_from = allTime\.value \? daysAgoIso\(ALL_TIME_DAYS\) : \(dateFrom\.value \|\| undefined\)/)
    expect(view).toMatch(/const ALL_TIME_DAYS = 365/)
    expect(view).toMatch(/const date_to = allTime\.value \? daysAgoIso\(0\) : \(dateTo\.value \|\| undefined\)/)
    expect(view).toMatch(/kinds: selectedKinds\.value\.join\(','\)/)
    expect(view).toMatch(/page_size: PAGE_SIZE/)
  })

  it('默认查最近一周，不让首屏扫全表', () => {
    expect(view).toMatch(/const PAGE_SIZE = 24/)
    expect(view).toMatch(/to\.getTime\(\) - 6 \* 86400000/)
  })

  it('取图走按 capture_id 的管理端接口，缩略图用 thumb 变体', () => {
    expect(view).toMatch(
      /\/api\/hygiene\/admin\/data\/photo\/\$\{encodeURIComponent\(captureId\)\}\?variant=\$\{variant\}/,
    )
    expect(view).toMatch(/photoUrl\(photo\.capture_id\)/)
    expect(view).toMatch(/HygieneImageLightbox/)
  })

  it('删单条记录走 DELETE，并且要二次确认', () => {
    expect(view).toMatch(
      /api\.delete\(\s*`\/api\/hygiene\/admin\/data\/records\/\$\{target\.kind\}\/\$\{target\.record_id\}`/,
    )
    expect(view).toMatch(/title="删除这条记录"/)
    expect(view).toMatch(/danger/)
  })

  it('按区间清理不含整改原图：整单删除仍是整改页的能力', () => {
    expect(view).toMatch(/KINDS\.filter\(\(kind\) => kind\.value !== 'fix'\)/)
    expect(view).toMatch(/api\.post\('\/api\/hygiene\/admin\/data\/purge'/)
    expect(view).toMatch(/kinds: purgeKinds\.value/)
    expect(view).toMatch(/title="按区间清理"/)
  })

  it('导出是异步任务：起任务 → 轮询进度 → 下载 zip', () => {
    expect(view).toMatch(/api\.post\(\s*`\/api\/hygiene\/admin\/data\/export\/jobs\?/)
    expect(view).toMatch(/api\.get\(`\/api\/hygiene\/admin\/data\/export\/jobs\/\$\{jobId\}`\)/)
    expect(view).toMatch(
      /api\.download\(\s*`\/api\/hygiene\/admin\/data\/export\/jobs\/\$\{jobId\}\/download`/,
    )
    expect(view).toMatch(/正在打包/)
    // 轮询必须在组件卸载时停掉，否则离开页面还在打接口。
    expect(view).toMatch(/onBeforeUnmount/)
    expect(view).toMatch(/clearTimeout\(pollTimer\)/)
  })

  it('存储概况显示各类型张数与占用', () => {
    expect(view).toMatch(/api\.get\('\/api\/hygiene\/admin\/data\/storage'\)/)
    expect(view).toMatch(/storage\.total\.count/)
    expect(view).toMatch(/formatBytes\(bucket\.bytes\)/)
  })

  it('删除与清理都会提示不可恢复', () => {
    expect(view).toMatch(/硬删除/)
    expect(view).toMatch(/红黑榜的历史计数不回滚/)
  })
})

describe('一键保存今天的照片（票 12 收尾）', () => {
  it('按钮在数据页上，起的是 scope=today 的任务', () => {
    expect(view).toContain('一键保存今天的照片')
    expect(view).toMatch(
      /api\.post\('\/api\/hygiene\/admin\/data\/export\/jobs\?scope=today'\)/,
    )
  })

  it('不自己算「今天」：营业日 06:00 切日，只有服务端那个口径跟照片上的日期一致', () => {
    const fn = view.slice(
      view.indexOf('async function saveTodayPhotos'),
      view.indexOf('function gotoPage'),
    )
    expect(fn).toMatch(/scope=today/)
    expect(fn).not.toMatch(/date_from/)
    expect(fn).not.toMatch(/toISOString\(\)/)
    expect(fn).not.toMatch(/chinaNow/)
  })

  it('类型里有仪容仪表 —— 一键不带 kinds，所以日常与仪容都在包里', () => {
    expect(view).toMatch(/\{ value: 'attire', label: '仪容仪表' \}/)
  })

  it('下载名听后端给的（今天的包不叫「卫生数据.zip」）', () => {
    expect(view).toMatch(/job\.filename \|\| filename/)
  })

  it('打包中不让重复点', () => {
    expect(view).toMatch(/exportToday \|\| \(exportJob && exportJob\.state === 'running'\)/)
  })
})

describe('数据与照片 · 2026-10-19 重排（三块结构）', () => {
  it('筛选条默认收起：折叠态是一行摘要 + 查询，展开才是日期/工作区/类型', () => {
    expect(view).toMatch(/const filtersOpen = ref\(false\)/)
    expect(view).toMatch(/class="filter-summary"/)
    expect(view).toMatch(/:aria-expanded="filtersOpen \? 'true' : 'false'"/)
    expect(view).toMatch(/filtersOpen = !filtersOpen/)
    // 查询成功后自动收起
    expect(view).toMatch(/await loadRecords\(\)\n  filtersOpen\.value = false/)
    expect(view).toMatch(/filtersDirty/)
  })

  it('存储概况：7 类可点 chip，点一个＝设为唯一类型并滚到列表、再点取消', () => {
    expect(view).toMatch(/class="kind-chip"/)
    expect(view).toMatch(/toggleKindFilter\(kind\.value\)/)
    const fn = view.slice(view.indexOf('async function toggleKindFilter'), view.indexOf('async function clearFilters'))
    expect(fn).toMatch(/if \(isKindActive\(kind\)\) \{/)
    expect(fn).toMatch(/selectedKinds\.value = KINDS\.map/)
    expect(fn).toMatch(/else \{[\s\S]*?selectedKinds\.value = \[kind\][\s\S]*?allTime\.value = true/)
    // 点 chip = 看该类近一年：否则默认 7 天会把 9 月的老记录筛没（用户报的 0 条就这原因）
    expect(fn).toMatch(/allTime\.value = true/)
    expect(fn).toMatch(/scrollIntoView/)
    // chip 上保留每类张数与占用提示
    expect(view).toMatch(/kindCount\(kind\.value\)/)
    expect(view).toMatch(/kindBytes\(kind\.value\)/)
  })

  it('清理走页头「清理…」弹窗，不再有常驻的 danger-card 面板', () => {
    expect(view).toMatch(/function openPurge\(\)/)
    expect(view).toMatch(/@click="openPurge"/)
    expect(view).toMatch(/title="按区间清理"/)
    // 二次确认：必须输入「清理」两个字（ConfirmDialog 的 prompt.equals）
    expect(view).toMatch(/equals: '清理'/)
    // 原常驻面板与它的 class 已经删掉
    expect(view).not.toMatch(/danger-card/)
    expect(view).not.toMatch(/danger-body/)
    // 原校验保留
    expect(view).toMatch(/const canPurge = computed\(\(\) => purgeKinds\.value\.length > 0 && !busy\.value\)/)
  })

  it('空态给「清除筛选」；加载失败给「重试」', () => {
    expect(view).toMatch(/async function clearFilters\(\)/)
    expect(view).toMatch(/>清除筛选</)
    expect(view).toMatch(/reset[\s\S]*?loadRecords\(\)/)
    const load = view.slice(view.indexOf('async function loadRecords'), view.indexOf('async function applyFilters'))
    expect(load).toMatch(/loadError\.value = err\.message/)
    expect(view).toMatch(/>重试</)
  })

  it('列表行：主页头动作进「导出 ▾」菜单；行尾「⋯」菜单 + 全部照片浮层 + 分页文案', () => {
    // 导出台账 / 一键保存今天的照片不再平铺在筛选里，进页头菜单
    const menu = view.slice(view.indexOf('head-actions-desktop'), view.indexOf('</header>'))
    expect(menu).toMatch(/导出 ▾/)
    expect(menu).toMatch(/startExport/)
    expect(menu).toMatch(/saveTodayPhotos/)
    expect(menu).toMatch(/一键保存今天的照片/)
    // 手机档三样收进 ⋯ 菜单
    expect(view).toMatch(/class="menu-trigger" aria-label="更多操作"/)
    // 行尾菜单 / 全部照片浮层
    expect(view).toMatch(/class="row-menu-trigger"/)
    expect(view).toMatch(/查看全部照片/)
    expect(view).toMatch(/删除这条/)
    expect(view).toMatch(/modal-overlay photo-sheet/)
    expect(view).toMatch(/record\.photos\.slice\(0, 3\)/)
    // 分页：第 1–24 条 / 共 N 条
    expect(view).toMatch(/<span class="pager-text">第 \{\{ pageStart \}\}–\{\{ pageEnd \}\} 条 \/ 共 \{\{ total \}\} 条<\/span>/)
  })
})

describe('照片文件不在了（库里有记录、磁盘上没有）', () => {
  it('取图 404 时显示「无文件」占位，而不是破图', () => {
    // 2026-10-19 用户报：列表里两个缩略图 404。后端 404 是对的（文件确实没了，
    // 存储概况的 missing_files 就是它们），前端不能留空框/破图。
    expect(view).toMatch(/@error="markPhotoMissing\(photo\.capture_id\)"/)
    expect(view).toMatch(/function markPhotoMissing\(captureId\)/)
    expect(view).toMatch(/class="data-thumb-missing"/)
    expect(view).toMatch(/无文件/)
    // 「全部照片」浮层里的那张也要同样降级（不能只修行内）
    expect((view.match(/@error="markPhotoMissing\(photo\.capture_id\)"/g) || []).length).toBeGreaterThanOrEqual(2)
  })
})

describe('数据与照片 · 近一年与 0 条空态（2026-10-19 用户报「点整改原图 0 条」）', () => {
  it('筛选面板有「近一年」，勾上时日期控件禁用；摘要显示近一年', () => {
    expect(view).toMatch(/const allTime = ref\(false\)/)
    expect(view).toMatch(/<label class="all-time-check">/)
    expect(view).toMatch(/v-model="allTime"/)
    expect(view).toMatch(/:disabled="allTime"/)
    expect(view).toMatch(/if \(allTime\.value\) return '近一年'/)
  })

  it('点类型 chip = 看该类近一年（再点取消时保持当前时间范围）', () => {
    const fn = view.slice(view.indexOf('async function toggleKindFilter'), view.indexOf('async function showAllTime'))
    expect(fn).toMatch(/selectedKinds\.value = \[kind\]\s*\n\s*allTime\.value = true/)
    // 取消（回到全部类型）那一路不碰时间范围
    const cancel = fn.slice(0, fn.indexOf('else {'))
    expect(cancel).not.toMatch(/allTime\.value = false/)
    // 空态里的「看近一年」也走同一个概念
    expect(view).toMatch(/async function showAllTime\(\)/)
    expect(view).toMatch(/allTime\.value = true[\s\S]{0,120}await loadRecords\(\)/)
  })

  it('0 条分两种空态；加载失败仍是独立「重试」', () => {
    expect(view).toMatch(/这个日期范围里没有记录。/)
    expect(view).toMatch(/上方类型 chip 上的数字是近一年的；这类记录可能在这个区间之外。/)
    expect(view).toMatch(/>看近一年</)
    expect(view).toMatch(/一次记录都没有：员工还没交过照片，或者已经被清理。/)
    expect(view).toMatch(/>清除筛选</)
    // 加载失败不与空态混
    expect(view).toMatch(/记录没读出来 · \{\{ loadError \}\}/)
    expect(view).toMatch(/>重试</)
  })
})
