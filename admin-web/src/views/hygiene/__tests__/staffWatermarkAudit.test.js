import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

/**
 * ③-3b 员工端「卫生」页的防回归断言（t51）。
 *
 * 守住三件事，都是**读源码式**断言（与 liveCameraNoAlbum.test.js 同一路数）：它们在
 * 页面上各有一次「看起来对、其实错了」的历史，靠人看很难发现。
 */
const here = dirname(fileURLToPath(import.meta.url))
const HOME = join(here, '../HygieneHomeView.vue')
const OVERLAY = join(here, '../../../components/hygiene/HygieneWatermarkOverlay.vue')
const read = (path) => readFileSync(path, 'utf8')

describe('③-3b 水印三项：时间 · 卫生工作区 · 姓名（ADR 0049）', () => {
  it('三档拍照都构造 zone 水印；专项那一档不再只给项目名', () => {
    const src = read(HOME)
    // 三处水印构造：整改（fix）/ 专项（deep）/ 日常（兜底那条）。三处都必须是 time+zone+photographer。
    expect((src.match(/zone:/g) || []).length).toBeGreaterThanOrEqual(3)
    expect((src.match(/photographer:/g) || []).length).toBeGreaterThanOrEqual(3)
    // 专项那一档原来写的是 `item_name: row && row.item_name` —— 第二行会被项目名占掉，区名就没了
    expect(src).not.toMatch(/item_name: row && row\.item_name/)
    // 专项的 zone：有区名用区名，没有（ADR 0054：专项全店一条）才退回项目名
    expect(src).toMatch(/const zoneName = \(row && row\.zone_name\) \|\| \(row && row\.item_name\)/)
    expect(src).toMatch(/zone: zoneName/)
  })

  it('水印组件第二行按 `item_name || zone` 取 —— 所以水印对象里只能给 zone', () => {
    // 这条是上面那条的**理由**：给 item_name 会把 zone 顶掉（即使两个都给了）。
    const overlay = read(OVERLAY)
    expect(overlay).toMatch(/mark\.item_name \|\| mark\.zone/)
    expect(overlay).toMatch(/formatWatermarkTime\(mark\.time\)/)
    expect(overlay).toMatch(/mark\.photographer/)
  })
})

describe('③-3b 单页三组结构 + 别人的活才进「待我验收」', () => {
  it('页内没有五格 tab 条（2026-10-05 用户裁定：不许回来）', () => {
    const src = read(HOME)
    expect(src).not.toMatch(/class="hy-tabbar"/)
    expect(src).not.toMatch(/class="hy-tab\b/)
  })

  it('自己交的那条不给自己判：决定权按提交人比对', () => {
    const src = read(HOME)
    expect(src).toMatch(/employee\.value\.id !== review\.submitter_id/)
  })
})

describe('③-3b 专项 / 整改两组：视图层稳定排序（逾期 → 将到期 → 待拍 → 等验收）', () => {
  it('口径表在视图层，且只看载荷里已有的 deadline + status（不碰接口）', () => {
    const src = read(HOME)
    expect(src).toMatch(/const URGENCY_RANK = \{ overdue: 0, soon: 1, todo: 2, waiting: 3 \}/)
    expect(src).toMatch(/if \(isPendingReview\(row\)\) return URGENCY_RANK\.waiting/)
    expect(src).toMatch(
      /URGENCY_RANK\[deadlineUrgency\(row && row\.deadline, nowTick\.value\)\] \?\? URGENCY_RANK\.todo/,
    )
    // 稳定排序：先拷一份再排（不就地改 ref / props 里的数组）
    expect(src).toMatch(/return \[\.\.\.rows\]\.sort\(/)
  })

  it('两个显示点用排好序的列表；`openDeep` 与 `fixInbox` 本身留给逻辑判据', () => {
    const src = read(HOME)
    expect(src).toMatch(/const openDeepQueue = computed\(\(\) => urgentFirst\(openDeep\.value\)\)/)
    expect(src).toMatch(/const fixQueue = computed\(\(\) => urgentFirst\(fixInbox\.value\)\)/)
    expect(src).toMatch(/v-for="row in openDeepQueue"/)
    expect(src).toMatch(/v-for="row in fixQueue"/)
    // 「下一件」的判据仍读原列表 —— 排序只改显示顺序，没改语义
    expect(src).toMatch(/nextFixWorkRow\(fixInbox\.value, /)
  })

  it('日常那一组不重复排序：它由共享 util 的 buildWorkQueue 排好', () => {
    const src = read(HOME)
    expect(src).not.toMatch(/urgentFirst\(workQueue/)
    expect(src).toMatch(/buildWorkQueue\(/)
  })
})

describe('③-3b 「已交」不另立分组：口径写在页内（F3）', () => {
  it('注释里写明「有意为之、不是漏做」，并点名守住它的实质判据', () => {
    const src = read(HOME)
    expect(src).toMatch(/有意为之、不是漏做/)
    expect(src).toMatch(/ADR 0067/)
    expect(src).toMatch(/function canDecide\(review, cap\)/)
    // 页面上确实没有独立的「已交」分组（有的只是行状态那几个字）
    expect(src).not.toMatch(/class="[^"]*"[^>]*>\s*已交\s*</)
  })
})
