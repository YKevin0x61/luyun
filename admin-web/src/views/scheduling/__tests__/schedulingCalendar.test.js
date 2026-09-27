import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const view = readFileSync(join(here, '../SchedulingCalendarView.vue'), 'utf8')
const tokens = readFileSync(join(here, '../../../../public/hygiene-admin.css'), 'utf8')
const router = readFileSync(join(here, '../../../router/index.js'), 'utf8')
const navBar = readFileSync(join(here, '../../../components/NavBar.vue'), 'utf8')

describe('店长端排班月历（原型 B）', () => {
  it('borrows the shared deep-teal tokens without joining the hygiene module', () => {
    // 令牌住在 public/hygiene-admin.css 里，但那是**共享样式表**：排班不 import
    // 卫生的 Python、不挂卫生菜单，两边各走各的门。
    expect(view).toMatch(/useScopedStylesheet\('\/hygiene-admin\.css'\)/)
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
    // 改成休走 `is_rest`，不是「班次留空」；不给责任区 = 跟这个班次的固定区。
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

  it('has its own door in the admin shell', () => {
    expect(router).toMatch(/path: '\/scheduling'/)
    expect(router).toMatch(/views\/scheduling\/SchedulingCalendarView\.vue/)
    expect(navBar).toMatch(/to="\/scheduling"/)
    expect(navBar).toMatch(/prefix: '\/scheduling'/)
  })

  it('底部那条待处理里接着「请假待办」（票 08）', () => {
    // 底下原来只有「N 个人还没配规则」那一条。请假是员工在手机上提的、店长在待办页批，
    // 月历上得有条路走过去 —— 否则没人知道有假等着批（页面上也不许弹窗提醒）。
    expect(view).toMatch(/class="gPend gTodo"/)
    expect(view).toMatch(/router\.push\('\/scheduling\/inbox'\)/)
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
    // 单日覆盖那条路也校验）：停用班次出现在周期的解析表或固定责任区的选择里，
    // 店长选完只会吃一句 400。页面照同一口径把它们挡在选择之外。
    expect(view).toMatch(/const activeShifts = computed/)
    expect((view.match(/v-for="shift in activeShifts"/g) || []).length).toBe(2)
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
