import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { pageMeta } from '../../../router/pageRoutes.js'

const here = dirname(fileURLToPath(import.meta.url))
const view = readFileSync(join(here, '../SchedulingCalendarView.vue'), 'utf8')
const shell = readFileSync(join(here, '../SchedulingLayout.vue'), 'utf8')
const tokens = readFileSync(join(here, '../../../../public/hygiene-admin.css'), 'utf8')
const router = readFileSync(join(here, '../../../router/index.js'), 'utf8')
const navBar = readFileSync(join(here, '../../../components/NavBar.vue'), 'utf8')

describe('店长端排班月历（原型 B）', () => {
  it('borrows the shared deep-teal tokens without joining the hygiene module', () => {
    // 令牌住在 public/hygiene-admin.css 里，但那是**共享样式表**：排班不 import
    // 卫生的 Python、不挂卫生菜单，两边各走各的门。
    // 共享样式表由**壳**加载一份（`SchedulingLayout.vue`）：三个子页各加载一份会挂出
    // 重复的 <link>；壳一层管住，跟卫生管理端一个做法（那边也是 layout 加载、子页不管）。
    expect(shell).toMatch(/useScopedStylesheet\('\/hygiene-admin\.css'\)/)
    expect(view).not.toMatch(/useScopedStylesheet\(/)
    expect(view).toMatch(/class="hygiene-admin sched-page"/)
  })

  it('only talks to the scheduling API', () => {
    expect(view).toMatch(/\/api\/scheduling\/calendar/)
    expect(view).toMatch(/\/api\/scheduling\/day/)
    expect(view).toMatch(/\/api\/scheduling\/roster/)
    expect(view).toMatch(/api\.put\(`\/api\/scheduling\/rules\/\$\{employee\.id\}`/)
    expect(view).toMatch(/api\.delete\(`\/api\/scheduling\/rules\/\$\{employee\.id\}`\)/)
    expect(view).not.toMatch(/\/api\/hygiene/)
  })

  it('keeps the chosen prototype B shape', () => {
    // 用户选的店长端首页 = `.scratch/scheduling/prototype/admin-variants.html`
    // 的 B · 月历（以天为中心）：一月网格 → 选中那天的人 → 底部待处理条。
    for (const cls of ['gB-grid', 'gB-d', 'gB-legend', 'gB-card', 'gB-names', 'gPend']) {
      expect(view).toContain(cls)
    }
    expect(view).toMatch(/repeat\(7,\s*1fr\)/)
    expect(view).toMatch(/calendar\.lead/) // 月初空格由后端算，前端不重算星期
    expect(view).toMatch(/calendar\.window_end/) // 窗口尽头之后的格子要说「还没铺到」
  })

  it('renders however many shifts the API returns', () => {
    // 班次可配置（票 11 会加「早班」之类）：页面按 N 个班次渲染，不写死白/夜两个。
    expect(view).toMatch(/v-for="shift in shifts"/)
    expect(view).toMatch(/v-for="group in dayDetail\.groups"/)
    expect(view).not.toMatch(/['"]白班['"]/)
    expect(view).not.toMatch(/['"]夜班['"]/)
  })

  it('pins a fixed zone per person and per shift (票 03)', () => {
    // 区名单只有一份（卫生建的那张表），排班经公共层读出来 → 页面按 N 个区渲染，
    // 一个区名字都不写死。
    expect(view).toMatch(/\/api\/scheduling\/zone-defaults\/\$\{employee\.id\}/)
    expect(view).toMatch(/data\.zones/)
    expect(view).toMatch(/v-for="zone in zones"/)
    expect(view).toMatch(/v-for="person in group\.people"/)
    expect(view).toMatch(/person\.zone/)
    expect(view).toMatch(/未配区/)
  })

  it('edits a rotation cycle made of shift names and 休 (票 04)', () => {
    // 周期编辑：一格一天，写班次名或「休」，起点默认今天。天数的上限来自服务端
    // （`max_cycle_days`），示例按当前班次名拼 —— 都不许在页面里写死第二份。
    expect(view).toContain('gC-in')
    expect(view).toMatch(/:placeholder="cyclePlaceholder"/)
    expect(view).toMatch(/const cyclePlaceholder = computed/)
    expect(view).toMatch(/parseCycle/)
    expect(view).toMatch(/REST_WORDS/)
    expect(view).toMatch(/data\.max_cycle_days/)
    expect(view).toMatch(/anchor_date: cycleAnchor\.value \|\| null/)
    // 「固定某个班」和「编周期」走同一个 PUT /rules，不是两套逻辑。
    expect(view.match(/api\.put\(`\/api\/scheduling\/rules\//g)).toHaveLength(2)
    // 空周期的提示也按**当前班次名**拼（票 11 能改名、能加第三个）：写死
    // 「白班 夜班 休」会让改了名的店对着一句不存在的话猜。
    expect(view).toMatch(/周期不能空着：至少写一天，例如「\$\{names \|\| '班次'\} 休」/)
    expect(view).not.toContain('例如「白班')
  })

  it('changes one day without touching the rule (票 07)', () => {
    // 单日覆盖：点当天卡里的一个人 → 就地改这一天。写的是 `/overrides/<人>/<哪天>`，
    // 跟规则那条路分开 —— 「固定一个班 / 编周期」改的是规则，「改这一天」改的是那天的快照。
    expect(view).toMatch(/api\.put\(`\/api\/scheduling\/overrides\/\$\{person\.id\}\/\$\{day\}`/)
    expect(view).toMatch(/api\.delete\(`\/api\/scheduling\/overrides\/\$\{person\.id\}\/\$\{day\}`\)/)
    // 改成休走 `is_rest`，不是「班次留空」；不给工作区 = 跟这个班次的固定区。
    expect(view).toMatch(/is_rest: true/)
    expect(view).toMatch(/zone_id: editZone\.value === '' \? null : Number\(editZone\.value\)/)
    // 名字是一颗可以按的棋子；点开的是那一天的那个人。
    expect(view).toMatch(/class="gB-name"/)
    expect(view).toMatch(/@click="openDayEdit\(person, group\.shift\.id\)"/)
    expect(view).toMatch(/person\.overridden/)
    // 名单不在手边就先补：反过来（先预填再补名单）这个会话里第一次打开编辑器必然落回
    // 「跟固定区」，保存时 `zone_id: null` 把那天自己挑过的区悄悄退回。
    expect(view).toMatch(/if \(!zones\.value\.length\) await loadRoster\(\)/)
    // 预填按 id：`hygiene_zones` 的区名字没有唯一约束，按名字反查会指到别人身上。
    expect(view).toMatch(/person\.zone_id === null \|\| person\.zone_id === undefined/)
    // 哪一天冻在打开那一刻，且换一天就把编辑器收起来 —— 否则开着编辑器点月历另一天，
    // 保存会把上一个人的改动写到新那一天。
    expect(view).toMatch(/day: selectedDate\.value/)
    expect(view).toMatch(/watch\(selectedDate,/)
    expect(view).toMatch(/const day = person && person\.day/)
    expect(view).toMatch(/formatDayLabel\(editing\.day\)/)
  })

  it('marks the days that were changed by hand (票 07)', () => {
    // 格子右上角一个点 + 图例里一句解释：「改成休」在人数里根本看不出来，
    // 只有这个跟人数无关的标记记得住。图例那句只在当月真有过改动时出现。
    expect(view).toMatch(/over: day\.overridden > 0/)
    expect(view).toMatch(/\.gB-d\.over::after/)
    expect(view).toMatch(/<span v-if="overriddenDays" class="gB-ov"><i><\/i>这天有改动<\/span>/)
    // 休的人不是一行计数：规则铺出来的休、被改成休的人，都得点得开（不然改错了没处撤）。
    expect(view).toMatch(/v-for="person in restPeople"/)
    expect(view).toMatch(/@click="openDayEdit\(person, 'rest'\)"/)
    // 「撤销」只在本来就改过、且还撤得动的日子给；过去的日子给的是一句实话，不是
    // 一个点了也不变的按钮。后端说得出为什么改不了，原话转给店长。
    expect(view).toMatch(/v-if="editing\.overridden && editing\.undoable"/)
    expect(view).toMatch(/撤销覆盖，回到规则/)
    expect(view).toMatch(/v-if="editing\.overridden && !editing\.undoable"/)
    expect(view).toMatch(/这天的改动已经是历史了/)
    expect(view).toMatch(/editError\.value = err\.message/)
  })

  it('uses only tokens the shared stylesheet defines', () => {
    // 少一个 var() 就是一处静默失效的样式（无色/无圆角），而 scoped 样式块
    // 不会因为引用了不存在的自定义属性而报错。
    const used = new Set([...view.matchAll(/var\((--[a-z0-9-]+)\)/g)].map((m) => m[1]))
    expect(used.size).toBeGreaterThan(10)
    const missing = [...used].filter((name) => !tokens.includes(`${name}:`))
    expect(missing).toEqual([])
  })

  it('排班从后台壳里独立出来：自带窄栏，不再压后台那条导航（2026-10-04）', () => {
    // 后台导航在手机上要占 86px（`theme.css` 的 ≤720px 那套两行布局），而排班是"当场干活
    // 的界面"——跟卫生管理端、员工端一个路子：标 standalone，由 SchedulingLayout 提供
    // 自己的头（回后台的入口、页面名、实时状态，原先这三样都挂在后台导航上）。
    // 票 01：这个标记不再写死在 router 里，而是从页面清单派生（`meta: pageMeta('/workbench')`）；
    // 注册出来的路由确实等于清单，由 `router/__tests__/pageRoutes.test.js` 对着真实路由表钉。
    // 票 06：子应用根改成了「今天」首页（`audience: both`）—— 人事这一组这几页仍各自带
    // `audience: 'admin'` 的独立外壳标记（由 `workbenchHrPage` 那条工厂从清单派生）。
    expect(router).toMatch(/meta: pageMeta\(path\)/)
    expect(router).toMatch(/meta: pageMeta\('\/workbench'\)/)
    expect(pageMeta('/workbench/hr/calendar')).toEqual({
      standalone: true, public: false, audience: 'admin',
    })
    expect(router).toMatch(/views\/scheduling\/SchedulingLayout\.vue/)
    expect(shell).toMatch(/router\.push\('\/'\)/)
    expect(shell).toMatch(/inject\('wsConnected'/)
    // 票 05：三条排班页按组落在 `/workbench/hr/*`（月历 / 待办 / 班次表各一行，
    // 走人事壳那条工厂）。后端 `SPA_PAGE_ROUTES` 与 `tests/test_spa_page_routes.py`
    // 都从页面清单 / 路由源码里读路径（票 01 起解析面也认无插值模板），子路由因此写绝对路径。
    expect(router).toMatch(/workbenchHrPage\('\/workbench\/hr\/calendar', 'workbench-hr-calendar'/)
    expect(router).toMatch(/workbenchHrPage\('\/workbench\/hr\/inbox', 'workbench-hr-inbox'/)
    expect(router).toMatch(/workbenchHrPage\('\/workbench\/hr\/shifts', 'workbench-hr-shifts'/)
    // 平铺那批旧地址一条都不留（票 05 的收口）。
    expect(router).not.toMatch(/'\/workbench\/inbox'/)
    expect(router).not.toMatch(/'\/workbench\/shifts'/)
  })

  it('导航上排班与卫生并成一格「工作台」（2026-10-04 合并）', () => {
    // 原来是并排两格（「排班」+「卫生」）。合并成一个子系统之后只留一格，高亮覆盖两组。
    expect(navBar).toMatch(/WORKBENCH_TITLE/)
        expect(navBar).toMatch(/route\.path\.startsWith\('\/workbench'\)/)
    // B1 的小步：进「现场」那一组不再靠壳里那扇单门（它是工作台导航的真子集，
    // 一次只到得了一页，还带着一个看着像下拉的 `›`），由壳渲染的**整排工作台级导航**
    // 接手 —— 同一颗组件（`components/workbench/WorkbenchNav.vue`）三个壳共用，
    // 表在 `utils/workbenchNav.js`，落点常量在 `utils/workbenchCopy.js`。
    expect(shell).toMatch(/components\/workbench\/WorkbenchNav\.vue/)
    expect(shell).toMatch(/class="sched-navbar"/)
    expect(shell).toMatch(/workbenchGroup\('hr'\)/)
    // 牌子写「工作台」就指工作台首页（B2：原来指本组首页，于是这条栏上没有回首页的路）。
    expect(shell).toMatch(/WORKBENCH_HOME/)
    // 子系统名字只写一次。
    expect(shell).toMatch(/WORKBENCH_TITLE/)
  })

  it('has its own door in the admin shell', () => {
    expect(router).toMatch(/path: '\/workbench'/)
    expect(router).toMatch(/views\/scheduling\/SchedulingCalendarView\.vue/)
    expect(navBar).toMatch(/to="\/workbench"/)
    expect(navBar).toMatch(/prefix: '\/workbench'/)
  })

  it('底部那条待处理里接着「请假待办」（票 08）', () => {
    // 底下原来只有「N 个人还没配规则」那一条。请假是员工在手机上提的、店长在待办页批，
    // 月历上得有条路走过去 —— 否则没人知道有假等着批（页面上也不许弹窗提醒）。
    expect(view).toMatch(/class="gPend gTodo"/)
    expect(view).toMatch(/router\.push\('\/workbench\/hr\/inbox'\)/)
    expect(view).toContain('请假待办')
    // 那一条是按钮不是链接：跟旁边那条同一个形状，点哪儿都算。
    expect(view).toMatch(/<button class="gPend gTodo" type="button"/)
    expect(view).toMatch(/\.gTodo \{[\s\S]{0,120}?var\(--hy-aqua\)/)
  })

  it('批过的请假自成一组，不跟「休」混（票 08）', () => {
    // 服务端在 day_detail 里已经分好了（`person.leave` 来自覆盖记录的 kind）：请假与「本来就休」
    // 在结果表里同形（都没有班次），页面上再不分，店长就分不清「他请假了」和「他今天本来就休」，
    // 更分不清它跟票 07 手动改成的休 —— 后者的青点标记是同一个。
    expect(view).toMatch(
      /const leavePeople = computed\(\(\) => offPeople\.value\.filter\(\(person\) => person\.leave\)\)/
    )
    expect(view).toMatch(/v-for="person in leavePeople"/)
    expect(view).toMatch(/<b>请假<\/b><i>\{\{ leavePeople\.length \}\}<\/i>/)
    expect(view).toMatch(/<em class="gB-zone leave">请假<\/em>/)
    expect(view).toMatch(/\.gB-grp-t\.leave \{ color: var\(--hy-aqua\); \}/)
    expect(view).toMatch(/\.gB-zone\.leave \{ color: var\(--hy-aqua\); \}/)
  })

  it('班次列以月历那份为准，名单那份不把它挤掉（停用班次照旧显示）', () => {
    // `/roster` 的 shifts 只有启用的（服务层 `list_shifts()`），`/calendar` 那份是
    // 「启用的全要 + 这个月真有行的停用班次」（`_shifts_for_display`）。以前数「还没配
    // 规则的人」那一次顺手拿名单那份覆盖了班次列，于是停用班次从这个月的格子与图例上
    // 消失，点开那天却还列着他 —— 格子数字与卡片合计互相打脸，票 11 的验收②
    //（停用之后历史排班照旧显示）在界面上就不成立了。
    expect(view).toMatch(/shifts\.value = data\.shifts \|\| \[\]/)
    expect(view).toMatch(/mergeShiftList\(shifts\.value, data\.shifts\)/)
    // 数人那一次只碰计数，不碰班次列。
    expect(view).toMatch(
      /async function loadPendingCount\(\) \{[\s\S]{0,400}?pendingCount\.value = countPending/
    )
    expect(view).not.toMatch(/async function loadPendingCount\(\) \{[\s\S]{0,400}?shifts\.value =/)
  })

  it('配规则只挑还在用的班次；当天那个下拉能显示停用的，但不给选', () => {
    // 「只能用还在用的班次」是服务端的口径（`set_rule` 的 `_require_shifts_usable`，
    // 单日覆盖那条路也校验）：停用班次出现在周期的解析表或固定工作区的选择里，
    // 店长选完只会吃一句 400。页面照同一口径把它们挡在选择之外。
    expect(view).toMatch(/const activeShifts = computed/)
    // 四处按「还在用的班次」渲染：名单行的「固定X班」按钮、每人每班次的固定工作区下拉、
    // 周表的图例、周表底部抽屉里的班次色卡（后两处是 2026-09-30 加的）。
    // 多一处少一处都是有意的改动，所以钉住数量。
    // 五处：月历图例、抽屉里的班次按钮、笔刷条的班次下拉（2026-10-04 加）、
    // 名单里的固定班次按钮、月历当天卡里的班次选择。
    expect((view.match(/v-for="shift in activeShifts"/g) || []).length).toBe(5)
    // 当天卡那个下拉列的是**显示用**那份（他那天可能就是停用的那个班）——
    // 停用的那条标出来并禁掉。
    expect(view).toMatch(/v-for="shift in shifts"[\s\S]{0,260}?已停用/)
    expect(view).toMatch(/:disabled="!shift\.is_active"/)
  })

  it('月历上标出「哪天有等着批的」—— 数据来自 /inbox，按天摊开（spec US 11）', () => {
    // 这条验收在票 02 与票 06 之间被转手两次、一直没人接：结果是店长只能滑到底、
    // 点进待办页才知道有几条在等他批。数据本来就够（`/inbox` 每条申请带
    // `start_date`/`end_date`），按天摊开数一遍就行，不用后端再出一个接口。
    expect(view).toMatch(/api\.get\('\/api\/scheduling\/inbox'\)/)
    expect(view).toMatch(/eachDayInRange\(/)
    expect(view).toMatch(/v-if="pendingMarks\[day\.business_date\]"/)
    expect(view).toMatch(/class="pend"/)
    // 图例里那条记号只在真有角标时出现。
    expect(view).toMatch(/hasPendingMarks/)
    // 琥珀给「待批」、青点给「这天被改过」—— 票 07 特意分开的两套记号，不能混。
    expect(view).toMatch(/\.gB-d \.pend \{/)
    expect(view).toMatch(/\.gB-legend \.gB-pend i \{ background: var\(--hy-amber\); \}/)
    expect(view).toMatch(/\.gB-legend \.gB-ov i \{ background: var\(--hy-aqua\); \}/)
  })

  it('名单读不出来时不写成「全员都配好了」', () => {
    // 原来 catch 里写 `pendingCount = 0`，页面就显示「0 个人还没配规则 · 全员都配好了」
    // —— 一句假话，店长会照着它放心。
    expect(view).toMatch(/pendingCount\.value = null/)
    expect(view).not.toMatch(/pendingCount\.value = 0\b/)
    expect(view).toMatch(/名单没读出来：点开重试一次/)
  })

  it('换一天先清掉上一天的人；月份连点两次也算数', () => {
    // 换天失败时表头已经是新那天、名单却还列着旧那天的人 —— 店长会照着错的名单改班。
    expect(view).toMatch(/async function loadDay[\s\S]{0,500}?dayDetail\.value = null/)
    // 慢网连点时，先到的旧响应不该覆盖后点的那个月；月份要立刻更新，否则第二次点击
    // 算出来的目标还是同一个月，看起来像没反应。
    expect(view).toMatch(/calendarSeq/)
    expect(view).toMatch(/if \(seq !== calendarSeq\) return/)
    // 首屏失败时 `monthValue` 是空串：`shiftMonth` 不能拼出一个真值的 `'NaN-NaN'`。
    expect(view).toMatch(/\(value \|\| currentMonthValue\(\)\)\.split\('-'\)/)
  })

  it('规则读不出来时说清，并且算进「要处理的人」', () => {
    // 后端收尾时给坏规则换了个形状：`{cycle: null, anchor_date: null, invalid: true}`
    //（原来它会让 `/roster` 整页 400，而名单面板是唯一能重配规则的入口 —— 等于自锁死）。
    // 页面得把「没配过」与「配了但读不出来」分开说，否则店长照着配一遍也修不好
    // （那条坏行还在），也看不出真正的原因。
    expect(view).toMatch(/if \(rule\.invalid\) return '规则坏了，重配一条'/)

    expect(view).toMatch(/\(!employee\.rule \|\| employee\.rule\.invalid\)/)
  })

  it('订阅排班 nudge：别人提了申请/改了排班，这一页自己重读（票 10 收尾）', () => {
    // 没有实时的话，店长只能靠反复刷新手动发现「有人提了假」「另一个页面改了排班」。
    // nudge 不带数据，所以 pull 里重读；静默（`silent`）—— 每来一条就闪一下 loading 很吵。
    expect(view).toMatch(/useNudgePull\(\{/)
    expect(view).toMatch(/id: 'scheduling-calendar'/)
    expect(view).toMatch(/topics: \['scheduling'\]/)
    expect(view).toMatch(/loadCalendar\(monthValue\.value, true\)/)
  })
})

// C 方向（2026-10-05 用户裁定）：工作台级导航（今天 / 人事 / 现场 / 后勤 / 我的）在手机档
// 从顶栏下到底部的拇指区，顶栏因此只剩「‹后台 + 本组四页 + 身份 + 退出」。
// 手机档的排布是**布局**：jsdom 不做布局、也不解析媒体查询，所以这一组按源码断 ——
// 同一手法见 `views/workbench/__tests__/shellMobileChrome.test.js` 的 `mobileBlocks`
// （那边断的是工作台外壳自己的那份，这里断人事壳的）。
describe('C 方向：人事壳把工作台级导航让到底栏', () => {
  /** 把文件里 `@media (max-width: 720px)` 那几段规则拼起来。按大括号配对取块，
   *  不靠正则猜边界（文件末尾还有 ≤560px 与桌面档的规则，滑过去就会断错对象）。 */
  function mobileRules(source) {
    const marker = '@media (max-width: 720px)'
    const blocks = []
    let from = 0
    for (;;) {
      const start = source.indexOf(marker, from)
      if (start === -1) break
      const open = source.indexOf('{', start)
      let depth = 0
      let end = -1
      for (let i = open; i < source.length; i += 1) {
        if (source[i] === '{') depth += 1
        else if (source[i] === '}') {
          depth -= 1
          if (depth === 0) { end = i; break }
        }
      }
      if (end === -1) throw new Error('大括号不配对')
      blocks.push(source.slice(open, end + 1))
      from = end + 1
    }
    expect(blocks.length, `${marker} 一段都没有`).toBeGreaterThan(0)
    return blocks.join('\n')
  }

  it('底栏挂上了，而顶栏那一条在手机档整个收起来（桌面档还要它）', () => {
    expect(shell).toMatch(/components\/workbench\/WorkbenchTabBar\.vue/)
    expect(shell).toMatch(/<WorkbenchTabBar class="sched-tabbar" \/>/)
    // 组件与它所在的带子都还在：桌面档（>720px）那条带子里仍是工作台级导航 + 本组四页。
    expect(shell).toMatch(/components\/workbench\/WorkbenchNav\.vue/)
    expect(shell).toMatch(/class="sched-navbar"/)
    // 手机档：**整条** `.sched-top` 收起来（2026-10-08 方案 C）—— 组内四页与退出 / 回后台
    // 都进 `WorkbenchMobileHead`，不再是"只收起工作台级那一排、其余留在带子上"。
    // 删组件（而不是 `display:none`）会把桌面档那一条一起删掉。
    expect(mobileRules(shell)).toMatch(/\.sched-top\s*\{\s*display:\s*none/)
    expect(shell).toMatch(/components\/workbench\/WorkbenchMobileHead\.vue/)
  })

  it('底栏钉在视口底、内容给它让出高度（两条都是壳自己的账）', () => {
    // 组件里那条 `position: fixed` 会被共享样式表的
    // `.hygiene-admin > *:not(.modal-overlay) { position: relative }` 打回 `relative`
    // （同特异度，而那张表更晚进 head —— 平局按文档顺序判）：底栏脱不出文档流，内容一长
    // 就跟着排到页面末尾，手机上等于没有。`.sched-shell` 这个父级是这条规则赢的条件，
    // 别当成冗余删掉。
    const rules = mobileRules(shell)
    expect(rules).toMatch(/\.sched-shell \.sched-tabbar\s*\{[^}]*position:\s*fixed/)
    // 它是 fixed（不占流），内容末尾要让出那条栏的高度，否则最后一屏压在栏下滚不到底。
    expect(rules).toMatch(/\.sched-shell\s*\{[^}]*padding-bottom:\s*calc\(57px \+ env\(safe-area-inset-bottom/)
  })
})
