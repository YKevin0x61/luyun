import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))

function read(rel) {
  return readFileSync(join(here, rel), 'utf8')
}

describe('hygiene staff work app', () => {
  it('单页依次渲染三组（日常 / 专项 / 整改），页内那条五格 tab 条不再回来', () => {
    const home = read('../HygieneHomeView.vue')
    expect(home).toMatch(/useScopedStylesheet\('\/hygiene-admin\.css'\)/)
    expect(home).toMatch(/StandardPhotoCachePanel/)
    expect(home).toMatch(/useStandardPhotoCacheStore/)
    expect(home).toMatch(/class="hygiene-staff hygiene-work"/)
    // 2026-10-05 用户裁定：待办 / 专项 / 整改 三格是**同一类东西**（今天的活），原本却与
    // 「榜」（信息）和「我」（设置）平级摆成一条 tab 条，三种性质混在一起。现在三组依次
    // 排下去、每组一个带条数的小标题；「榜」降到页尾一行入口、「我」整块搬到「今天」页。
    // 这条守着"那条 tab 条别偷偷回来"。
    expect(home).not.toMatch(/class="hy-tabbar"/)
    expect(home).not.toMatch(/HYGIENE_STAFF_TABS/)
    expect(home).not.toMatch(/aria-label="卫生入口"/)
    expect(home).toMatch(/<h2>日常 <span>\{\{ dailyCount \}\}<\/span><\/h2>/)
    expect(home).toMatch(/<h2>专项 <span>\{\{ deepCount \}\}<\/span>/)
    expect(home).toMatch(/<h2>整改 <span>\{\{ fixCount \}\}<\/span><\/h2>/)
    // 顶部那行汇总：今天要做 N 项 + 明细（三组各几件）。
    expect(home).toMatch(/今天要做 \{\{ todoTotal \}\} 项/)
    expect(home).toMatch(/日常 \{\{ dailyCount \}\} · 专项 \{\{ deepCount \}\} · 整改 \{\{ fixCount \}\}/)
    // 「榜」降成页尾一行信息入口（点开在本页展开），不再是平级的一格。
    expect(home).toMatch(/红黑榜 · 卫生教材/)
    expect(home).toMatch(/v-if="boardsOpen"/)
    // 票 10：这一屏原来写的是「今天负责哪个区域、上哪一班？」—— 那是个选择器。自选撤了
    // 之后，同样的位置换成了「今天交不了日常检查」的实话 + 一条去「今天」页的路；
    // 那条写回自选的请求也不许再出现。
    expect(home).toMatch(/今天交不了日常检查/)
    expect(home).toMatch(/今天排班没有排到你的班/)
    expect(home).not.toMatch(/staff\/assignment/)
    expect(home).toMatch(/去看我的班/)
    expect(home).toMatch(/loadZones\(\{ force: true \}\)/)
    expect(home).toMatch(/loadBoardsAndTeaching\(\{ force: true \}\)/)
    // 账号设置（修改个人信息 / 修改密码 / 重新选择区域和班次）整块搬到「今天」页：
    // 它不是"今天要干的卫生活"，混在这一屏只会把待办压下去。这一页里一个字都不该留
    // （判据钉在**标识符与按钮文案**上，不钉在"这三个词有没有出现"——页头注释里会提到
    // 它们搬去哪了，评论里的字不该被当成界面）。
    expect(home).not.toMatch(/startProfileEdit|startPasswordEdit/)
    expect(home).not.toMatch(/profilePhone|profileEditing|passwordSaving|confirmPassword/)
    expect(home).not.toMatch(/>修改个人信息</)
    expect(home).not.toMatch(/>修改密码</)
    expect(home).not.toMatch(/\/api\/hygiene\/staff\/password/)
    const today = read('../../today/TodayView.vue')
    expect(today).toMatch(/修改个人信息/)
    expect(today).toMatch(/修改密码/)
    expect(today).toMatch(/重新选择区域和班次/)
    expect(today).toMatch(/staffRequest\('\/api\/hygiene\/staff\/me', \{/)
    expect(today).toMatch(/method: 'PATCH'/)
    expect(today).toMatch(/\/api\/hygiene\/staff\/password/)
    expect(home).not.toMatch(/staff-phone/)
    expect(home).not.toMatch(/<style scoped>/)
  })

  it('shows remaining work, due clocks, and auto-advances after a shot', () => {
    const home = read('../HygieneHomeView.vue')
    expect(home).toMatch(/from '\.\.\/\.\.\/utils\/hygieneWorkFlow'/)
    expect(home).toMatch(/buildWorkQueue/)
    expect(home).toMatch(/hy-work-row/)
    // 原来是日常那一格开头那句大标题「今天还差什么」。五格合并之后组标题就是「日常 N」，
    // 同一屏上三组各来一句大标题会压过组标题 —— 那句话由顶部汇总（今天要做 N 项）与
    // 下面那排事实胶囊（日常 n/m · 超时 n）接手，两者的口径没变。
    expect(home).toMatch(/今天要做 \{\{ todoTotal \}\} 项/)
    expect(home).toMatch(/hy-work-facts/)
    expect(home).toMatch(/nextShootRow/)
    expect(home).toMatch(/本班/)
    expect(home).toMatch(/staff-preview-close/)
    expect(home).toMatch(/aria-labelledby="hygiene-sheet-title"/)
    expect(home).toMatch(/已通过/)
    expect(home).toMatch(/daily_clocks/)
    expect(home).toMatch(/employee\.name \|\| employee\.phone/)
    expect(home).not.toMatch(/考核分/)
  })

  it('keeps the before image visible while confirming a deep-clean pair', () => {
    const home = read('../HygieneHomeView.vue')
    expect(home).toMatch(/beforePreviewUrl/)
    expect(home).toMatch(/localBeforeWatermark/)
    expect(home).toMatch(/sheet\.mode === 'after-preview'/)
    expect(home).toMatch(/left-label="清理前"/)
    expect(home).toMatch(/right-label="清理后"/)
  })

  it('names the zone when the daily inbox has nothing at all', () => {
    const home = read('../HygieneHomeView.vue')
    const done = home.indexOf('class="hy-staff-done"')
    // 收尾那段空清单的说明：从「今天都交了」那块到**它后面**第一个队列分节。
    // 三个组现在各套一层 `.hy-queue-group`（组标题 + 条数），所以起点之后不能再拿
    // 全文第一个 `hy-queue-group` 当终点 —— 那一个是日常组自己的外壳，位置在起点之前。
    const empty = home.slice(done, home.indexOf('hy-queue-group', done))
    // 日常清单按所选工作区过滤（ADR-0075）。空清单原来只说「还没有带标准图的日常
    // 检查项」，管理员在别的区建完标准图过来核对，读到的就是「图丢了」。这里必须
    // 点出是哪个区没有，并留一个换区出口。
    expect(empty).toMatch(
      /当前区域「\{\{ employee\.zone_name \|\| '未选区域' \}\}」没有带标准图的日常检查项/,
    )
    expect(empty).toMatch(/这一屏只列你选的这个区/)
    // 票 10：那个按钮不再打开「换个区域」的选择器（员工不自己选了），而是切到待办屏
    // 让员工看清今天为什么没有日常可交 —— 文案也跟着改了。
    expect(empty).toMatch(/@click="openDutyNotice">看看今天怎么安排/)
    // 旧的裸文案（不带区名）不许再作为正文出现；注释里提到它不算。
    expect(empty).not.toMatch(/>\s*还没有带标准图的日常检查项。/)
  })

  it('keeps the 日常 group to daily work only', () => {
    const home = read('../HygieneHomeView.vue')
    const queue = home.slice(
      home.indexOf('const workQueue = computed'),
      home.indexOf('const nextWork = computed'),
    )
    // 日常这一组只列日常：专项、整改各自成组（各有各的条数）。混进这条队列会让员工在
    // 这一组里看到不属于日常的活，组标题后面那个条数也会和列表对不上（tabWorkCount 同一口径）。
    expect(queue).toMatch(/inbox: inbox\.value/)
    expect(queue).not.toMatch(/deepInbox/)
    expect(queue).not.toMatch(/fixInbox/)

    const facts = home.slice(
      home.indexOf('hy-work-facts'),
      home.indexOf('hy-staff-lead', home.indexOf('hy-work-facts')),
    )
    expect(facts).toMatch(/日常 \{\{ dailyStats\.passed \}\}/)
    expect(facts).not.toMatch(/专项/)
    expect(facts).not.toMatch(/整改/)
  })

  it('不再有页内那条「‹ 今天」：底栏「我的」就是去「今天」的路', () => {
    const home = read('../HygieneHomeView.vue')
    // 2026-10-05 用户裁定：页内那条 `‹ 今天` 撤掉。它回的是 `/workbench/me/today`，而工作台
    // 底栏「我的」那一格指的**正是**同一个目的地（`WorkbenchTabBar` 按身份渲染
    // `utils/workbenchNav.js` 的 `me` 格）—— 同一屏两个入口通向同一页，页内这条只是重复。
    // 当年它兼职的那件事（给没有返回键的 iOS PWA 留一条回程）现在由底栏接手。
    // 判据钉在标记上（这一页原来只有这一条 router-link，撤了之后一条都不该剩）——
    // 页头注释里写着"原来这里有一条回程"，那条注释里的字不算界面。
    expect(home).not.toMatch(/hy-work-today/)
    expect(home).not.toMatch(/<router-link/)
    // 页头收成一行：页名 + 我今天在哪那颗**只读**胶囊。
    expect(home).toMatch(/<h1 class="hy-brand-title">卫生<\/h1>/)
    expect(home).toMatch(/class="hy-work-shift"/)
    // 那条回程确实在底栏（不是凭空消失）：读那张导航表。
    const nav = read('../../../../src/utils/workbenchNav.js')
    expect(nav).toMatch(/key: 'me', label: '我的', to: '\/workbench\/me\/today'/)
  })
})
