import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))

function read(rel) {
  return readFileSync(join(here, rel), 'utf8')
}

describe('hygiene staff work app', () => {
  it('uses the porcelain work shell with a five-tab bar and no dark staff-phone card', () => {
    const home = read('../HygieneHomeView.vue')
    expect(home).toMatch(/useScopedStylesheet\('\/hygiene-admin\.css'\)/)
    expect(home).toMatch(/StandardPhotoCachePanel/)
    expect(home).toMatch(/useStandardPhotoCacheStore/)
    expect(home).toMatch(/class="hygiene-staff hygiene-work"/)
    expect(home).toMatch(/HYGIENE_STAFF_TABS/)
    expect(home).toMatch(/class="hy-tabbar"/)
    expect(home).toMatch(/aria-label="卫生入口"/)
    // 票 10：这一屏原来写的是「今天负责哪个区域、上哪一班？」—— 那是个选择器。自选撤了
    // 之后，同样的位置换成了「今天交不了日常检查」的实话 + 一条去「今天」页的路；
    // 那条写回自选的请求也不许再出现。
    expect(home).toMatch(/今天交不了日常检查/)
    expect(home).toMatch(/今天排班没有排到你的班/)
    expect(home).not.toMatch(/staff\/assignment/)
    expect(home).toMatch(/去看我的班/)
    expect(home).toMatch(/loadZones\(\{ force: true \}\)/)
    expect(home).toMatch(/loadBoardsAndTeaching\(\{ force: true \}\)/)
    expect(home).toMatch(/修改个人信息/)
    expect(home).toMatch(/staffRequest\('\/api\/hygiene\/staff\/me'/)
    expect(home).toMatch(/method: 'PATCH'/)
    expect(home).toMatch(/修改密码/)
    expect(home).toMatch(/\/api\/hygiene\/staff\/password/)
    expect(home).not.toMatch(/staff-phone/)
    expect(home).not.toMatch(/<style scoped>/)
  })

  it('shows remaining work, due clocks, and auto-advances after a shot', () => {
    const home = read('../HygieneHomeView.vue')
    expect(home).toMatch(/from '\.\.\/\.\.\/utils\/hygieneWorkFlow'/)
    expect(home).toMatch(/buildWorkQueue/)
    expect(home).toMatch(/hy-work-row/)
    expect(home).toMatch(/下一步|下一件|今天还差什么/)
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
    const empty = home.slice(
      home.indexOf('class="hy-staff-done"'),
      home.indexOf('hy-queue-group'),
    )
    // 日常清单按所选责任区过滤（ADR-0075）。空清单原来只说「还没有带标准图的日常
    // 检查项」，管理员在别的区建完标准图过来核对，读到的就是「图丢了」。这里必须
    // 点出是哪个区没有，并留一个换区出口。
    expect(empty).toMatch(
      /当前区域「\{\{ employee\.zone_name \|\| '未选区域' \}\}」没有带标准图的日常检查项/,
    )
    expect(empty).toMatch(/这一屏只列你选的这个区/)
    // 票 10：那个按钮不再打开「换个区域」的选择器（员工不自己选了），而是切到待办屏
    // 让员工看清今天为什么没有日常可交 —— 文案也跟着改了。
    expect(empty).toMatch(/@click="showWhyNoDuty">看看今天怎么安排/)
    // 旧的裸文案（不带区名）不许再作为正文出现；注释里提到它不算。
    expect(empty).not.toMatch(/>\s*还没有带标准图的日常检查项。/)
  })

  it('keeps the 待办 screen to daily work only', () => {
    const home = read('../HygieneHomeView.vue')
    const queue = home.slice(
      home.indexOf('const workQueue = computed'),
      home.indexOf('const nextWork = computed'),
    )
    // 待办页就是日常页：专项、整改各自有 tab 与角标。混进这条队列会让员工在这一屏
    // 看到不属于日常的活，待办角标也会和列表对不上（tabWorkCount 同一口径）。
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

  it('回得到「今天」页：卫生页不是单行道', () => {
    const home = read('../HygieneHomeView.vue')
    // 员工登录后落在 `/today`（票 05），卫生只是它的下半张卡。缺了这条回程，员工点进
    // 卫生就回不到自己的班 —— iPhone 的 PWA 独立窗口没有返回键，那就是真的卡住。
    expect(home).toMatch(/class="hy-work-today"/)
    expect(home).toMatch(/to="\/today"/)
    expect(home).toMatch(/aria-label="回到「今天」页看我的班"/)
    // 头部那条胶囊的样式得在共享样式表里，否则按钮是裸的。
    const css = read('../../../../public/hygiene-admin.css')
    expect(css).toMatch(/\.hygiene-work \.hy-work-today \{/)
  })
})
