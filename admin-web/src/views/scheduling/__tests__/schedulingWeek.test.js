import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const view = readFileSync(join(here, '../SchedulingCalendarView.vue'), 'utf8')

// 周表（员工 × 周）：2026-09-30 按门店在用的那套排班页的形式加进店长端。
// 它回答的是第四句话 ——「这个人这一周怎么上」：月历答「哪天缺人」、当天卡答「这天是谁」、
// 名单答「谁的规则配错了」。
describe('店长端周表（员工 × 周）', () => {
  it('只读排班自己的接口，周表是第三个视角而不是第二页', () => {
    expect(view).toMatch(/api\.get\('\/api\/scheduling\/week'/)
    expect(view).not.toMatch(/\/api\/hygiene/)
    // 三个视角在一页里切：不新增 SPA 路由，前端清单契约（tests/test_spa_page_routes.py）不用动。
    for (const word of ['月历', '周表', '名单']) {
      expect(view).toContain(word)
    }
    expect(view).toMatch(/openPanel\('week'\)/)
  })

  it('周起点是周一，且标题写日期区间而不是「10月第一周」', () => {
    // 周一 = 0：`getDay()` 是周日 0，先 +6 再取模 —— 跟服务端 `date.weekday()` 同一套。
    expect(view).toMatch(/\(base\.getDay\(\) \+ 6\) % 7/)
    expect(view).toMatch(/const WEEK_HEADS = \['一', '二', '三', '四', '五', '六', '日'\]/)
    // 跨月周（9/28–10/4）只写「10 月第一周」是有歧义的，直接给区间。
    expect(view).toMatch(/shortDate\(data\.start\)/)
    expect(view).toMatch(/shortDate\(data\.end\)/)
    // 「上周 / 本周 / 下周」按 7 天翻。
    expect(view).toMatch(/shiftWeek\(weekStart, -1\)/)
    expect(view).toMatch(/shiftWeek\(weekStart, 1\)/)
  })

  it('三种「空」分得开：没铺到 / 整列没数据 / 这个人没排到', () => {
    // 服务端给了两个判据：`day.in_window`（超出展开窗口）与 `day.row_count`（那天一行都没有）。
    // 少了它们，「那天全员休」和「那天还没有数据」在界面上长得一模一样。
    expect(view).toMatch(/!day\.in_window/)
    expect(view).toMatch(/!day\.row_count/)
    expect(view).toMatch(/mute: blank && \(!day\.in_window \|\| !day\.row_count\)/)
    expect(view).toMatch(/none: blank && day\.in_window && !!day\.row_count/)
    for (const cls of ['.gW-cell.mute', '.gW-cell.none', '.gW-cell.off', '.gW-cell.leave']) {
      expect(view).toContain(cls)
    }
    // 那句「不是那天全员休」得说出来，不能只靠一个淡格子。
    expect(view).toMatch(/不是「那天全员休」/)
  })

  it('假与休分开画（票 08 的口径不能退回去）', () => {
    // 结果行里两者同形（都没有班次），只有覆盖记录的 `kind` 分得开；周表也不能把
    // 「他请假了」和「他今天本来就休」画成一个颜色。
    expect(view).toMatch(/cell\.leave \? '假' : '休'/)
    expect(view).toMatch(/leave: off && !!cell\.leave/)
    expect(view).toMatch(/\.gW-cell\.leave \{ color: var\(--hy-aqua\)/)
  })

  it('格子里一定留字，而且缩写要能彼此分开', () => {
    // 颜色只有 6 个 tone，班次是数据、可以配到第 6 个 —— 所以「靠颜色认班次」不成立，
    // 格子里必须有字。而且不能死取第一个字：「主管A两头班」与「主管B两头班」都是「主」。
    expect(view).toMatch(/const weekShortNames = computed/)
    expect(view).toMatch(/weekShortNames\.value\[cell\.shift_id\]/)
    expect(view).toMatch(/const SHIFT_TONES = \['mint', 'aqua', 'amber', 'seal', 'violet', 'rose'\]/)
    // 五个班次至少要有五个不重样的 tone（门店那种 A/B/C + 两个主管班）。
    for (const tone of ['mint', 'aqua', 'amber', 'seal', 'violet']) {
      expect(view).toContain(`.gW-cell.tone-${tone} {`)
    }
  })

  it('半天休在格子上写一个「半」，只读（票 07 / ADR 0114）', () => {
    // 那天仍然挂着班次（格子里照旧是班次缩写），只是旁边多一个「半」——
    // 员工与店长都不会以为要上整天。**只读**的一位：这里没有设置它的控件，
    // 设置它的地方只有考勤表页（考勤表那条 PUT 的 `half_rest`）。
    expect(view).toMatch(/v-if="weekCellHalf\(employee, day\)"/)
    expect(view).toMatch(/function weekCellHalf\(employee, day\)/)
    expect(view).toMatch(/class="gW-half"/)
    expect(view).toMatch(/cell\.half_rest \? '（休半天）' : ''/)
    // 图例里解释那个「半」（只在真有这样的格子时才出现）。
    expect(view).toMatch(/const weekHalfCount = computed/)
    expect(view).toMatch(/class="gB-half"><i><\/i>半 = 休半天/)
    // 周表上**没有**写它的路：那一行只有「刷」/「清」/ 班次 / 休。
    expect(view).not.toMatch(/half_rest:\s*true/)
    expect(view).not.toMatch(/half_rest:\s*!/)
  })

  it('点一格＝立刻落笔；笔栏常驻并给页面留出底部空间', () => {
    // 票 14 修订（2026-10-13 用户真机裁定）：「还是之前点击方格就改写班次那种比较好」——
    // 格子从「选区手柄」改回**落笔点**：点一格 = 用笔栏上那支笔立刻写这一格
    // （用户①：原来点完格子还要再点一次班次才生效，多一步）。
    expect(view).toMatch(/@click="paintCell\(employee, day\)"/)
    // 选区还在，但手柄只剩两个：日期列头 = 整列、人名 = 整行（只为批量存在）。
    expect(view).toMatch(/@click="onColPick\(day\)"/)
    expect(view).toMatch(/@click="onRowPick\(employee\)"/)
    expect(view).toMatch(/nextPick\(colHandle\(day\.business_date\)\)/)
    expect(view).toMatch(/pickAt\(current, handle\)/)
    // 笔栏**常驻**在周表这一栏的底部（不是选中才升起）：页面得一直让出它的高度，
    // 不留的话它压住的正是下面几行员工（门店那套就是这个毛病，截图里能看到第 4、5 行被压掉一半）。
    expect(view).toMatch(/v-if="panel === 'week'"/)
    expect(view).toMatch(/class="gPick"/)
    expect(view).toMatch(/\.gPick \{[\s\S]{0,240}?position: fixed/)
    expect(view).toMatch(/\.gB\.pen-open \{ padding-bottom: 76px; \}/)
    expect(view).toMatch(/'pen-open': panel === 'week'/)
    // 点回锚点 / 点表格外的空白 = 取消（ADR 0107 决策 3）：展开与取消的规则在纯逻辑模块里
    // （那边有单测），这一页负责把手柄接上去、把「点空白」翻成一次取消。
    expect(view).toMatch(/pickAt,/)
    expect(view).toMatch(/function onPageClick\(event\)/)
  })

  it('笔栏上是笔（班次按钮组 / 工作区 / 休 / ↺），批量动作只在选区那一行', () => {
    // 点色卡 = **换笔**（不是立刻写）：班次选一次就一直用，这就是用户要的「一支笔」的感觉
    // （用户④：每次都要重新挑班次和工作区）。默认仍是第一个在用的班次。
    expect(view).toMatch(/@click="pickPenShift\(shift\)"/)
    expect(view).toMatch(/@click="pickPenRest\(\)"/)
    expect(view).toMatch(/const penShift = computed/)
    // 工作区默认收起、点按钮才多展开一行横滑的区名（用户②：动作条太高）。
    expect(view).toMatch(/v-if="penZoneOpen" class="gPick-zones"/)
    // 那颗按钮是**开关**，不是选项：展开时它写「收起」（窄，别把「休」挤出视野），
    // 也不带选中态 —— 否则展开后两颗「跟固定区」一起亮（2026-10-13 用户截图）。
    expect(view).toMatch(/penZoneOpen \? '收起' : penZoneName/)
    expect(view).not.toMatch(/:class="\{ on: penZoneOpen \}"/)
    // 批量只走选区那一行的「刷」/「清」；不选区时那一行整条不渲染。
    expect(view).toMatch(/@click="applyRegion\(\)"/)
    expect(view).toMatch(/@click="applyRegionClear\(\)"/)
    // 写路径是**批量端点**（单格也只是 items 里的一条）：一次校验、一次写锁、一次提交。
    expect(view).toMatch(/api\.post\('\/api\/scheduling\/overrides\/batch'/)
    expect(view).toMatch(/batchPayload\(action, options, cells\)/)
    // 一笔写完只重拉一次周表（不是每格一次）。
    expect(view).toMatch(/await loadWeek\(week\.value \? week\.value\.start : weekStart\.value\)/)
    // 规则仍是另一件事：在「名单」里改（上一版那条「去名单改规则」是单格摘要的一部分，
    // 单格不再有摘要 —— 点一格就是落笔）。
    expect(view).toMatch(/openPanel\('roster'\)/)
    expect(view).toMatch(/openCycle\(employee\)/)
    // 笔栏里**没有原生 `<select>`**：那个弹层由系统画，在这个贴底的位置会飘到页面中间
    // （2026-10-13 用户截图），而那层我们一个像素都改不了。班次与工作区都是按钮组。
    const pen = view.slice(view.indexOf('class="gPick"'), view.indexOf('<!-- 选区里有过去的日子'))
    expect(pen).not.toMatch(/<select/)
  })

  it('选区那一行：摘要 + 影响格数 + 「刷」「清」，只在有选区时出现；↺ 没得撤就禁用', () => {
    // 用户不用批量（③），所以这一行不选区时**完全不出现** —— 它只在选了整列 / 整行后长出来。
    expect(view).toMatch(/class="gPick-sum"/)
    expect(view).toMatch(/v-if="pickActive" class="gPick-region"/)
    expect(view).toMatch(/全店 \$\{view\.rows\.length\} 人 · \$\{view\.count\} 格/)
    expect(view).toMatch(/这一周 · \$\{view\.count\} 格/)
    // ↺ 撤销：只留最近一批；没有可撤销的那一批时禁用（口径 1）。
    expect(view).toMatch(/@click="undoLast\(\)"/)
    expect(view).toMatch(/:disabled="pickBusy \|\| !pickUndo"/)
    // 撤销是内存级的：这台手机、刷新之前（ADR 0107 决策 7）。这句说明跟着回执那一行走。
    expect(view).toMatch(/撤销只留最近一批、也只在这台手机上/)
    expect(view).toMatch(/undoPlan\(snapshot, result\.skipped\)/)
  })

  it('过去的日子：点一下就弹一次确认；超 30 天与已批的假先拦下来点名', () => {
    // ADR 0108：过去 30 天内可补记，弹**一次**确认点名是哪几天（不是每格弹一次）——
    // 单格也一样，点一格就是一笔。
    expect(view).toMatch(/const dates = pastDatesIn\(cells, \{ today: week\.value/)
    expect(view).toMatch(/pastConfirm\.value = \{ action, options, cells, dates, clear: action === PICK_CLEAR \}/)
    expect(view).toMatch(/<ConfirmDialog/)
    expect(view).toMatch(/:message="pastConfirmText"/)
    expect(view).toMatch(/@confirm="runPaint\(pastConfirm\.action, pastConfirm\.options, pastConfirm\.cells\)"/)
    // 会被跳过的格子**不发请求**：超 30 天、已批的假（词表与回执同一套 `PICK_SKIP_TEXT`）。
    expect(view).toMatch(/const \[skip\] = predictSkips\(cells, skipContext\(action\)\)/)
    expect(view).toMatch(/跳过：\$\{skipText\(\[skip\]\)\}/)
    // 整片刷的时候谁被跳过由服务端回执点名（它才是权威）。
    expect(view).toMatch(/skipText\(result\.skipped\)/)
    expect(view).toMatch(/PICK_SKIP_TEXT/)
  })

  it('回执与待批提醒收进一行提示，逐格点名的进「看全部」，而且会自己消失', () => {
    expect(view).toMatch(/已刷 \$\{result\.applied\} 格/)
    expect(view).toMatch(/跳过 \$\{result\.skipped\.length\} 格/)
    expect(view).toMatch(/可撤销（只留最近一批、也只在这台手机上）/)
    // 待批提醒看得出是「照刷」：先信服务端回执里那一份，它没给就退回既有的待办那一份。
    expect(view).toMatch(/api\.get\('\/api\/scheduling\/inbox'\)/)
    expect(view).toMatch(/pickPending\(cells, pendingCells\.value\)/)
    expect(view).toMatch(/批了会盖掉你刚刷的班/)
    // **一行** aria-live，几秒后自己消失（下一次操作替换它）——不再同时挂 4~6 行把表格压掉半屏。
    expect(view).toMatch(/const PICK_NOTICE_MS = 6000/)
    expect(view).toMatch(/noticeTimer = setTimeout/)
    expect(view).toMatch(/aria-live="polite"/)
    // 跳过 / 待批那几行**不丢**：收进 `details`，点「看全部」展开。
    expect(view).toMatch(/v-if="pickNotice\.details\.length"/)
    expect(view).toContain('看全部')
  })

  it('没配规则的人在行内标出来（行内，不是表下注释）', () => {
    // 一个新人整行都是空的，看着像这店不给他排班 —— 这句必须在那一行上。
    expect(view).toMatch(/v-if="!employee\.has_rule" class="gW-norule"/)
    expect(view).toMatch(/\.gW-norule \{/)
    expect(view).toMatch(/has_rule/)
  })

  it('能按姓名筛人，并说出筛掉了几个', () => {
    // 门店那张截图里网格的可视高度只有 655px（4.56 行），而名单至少 11 人 —— 靠翻页找人太慢。
    // 服务端一次给全量，筛是前端的事：不新增接口参数。
    expect(view).toMatch(/const weekRows = computed/)
    expect(view).toMatch(/v-model="weekFind"/)
    expect(view).toMatch(/v-for="employee in weekRows"/)
    // 少了几行必须说出来：不然「筛掉了」看着像「那几个人没了」。
    expect(view).toMatch(/筛掉 \{\{ weekFilteredOut \}\} 人/)
  })

  it('表头粘住、姓名列定宽，手机上不横向滚', () => {
    // 手机上一屏只放得下四行多（门店那张截图 655px 可视高度 / 143.5px 行高 =
    // 4.56 行，名单至少 11 人），没有粘住的表头就不知道哪一列是哪天。
    expect(view).toMatch(/\.gW thead th \{[\s\S]{0,120}?position: sticky/)
    expect(view).toMatch(/table-layout: fixed/)
    expect(view).toMatch(/\.gW-cname \{ width: 4\.6em; \}/)
  })

  it('今天那列有记号，改过的那格有青点（跟月历同一个记号）', () => {
    expect(view).toMatch(/today: !!day\.is_today/)
    expect(view).toMatch(/\.gW thead th\.today/)
    expect(view).toMatch(/over: !!cell && cell\.overridden/)
    expect(view).toMatch(/\.gW-cell\.over::after/)
    // 待批的琥珀点挂在**日期表头**上（一天一条事实）：挂在每一格里会变成 11 个一样的点。
    expect(view).toMatch(/v-if="pendingMarks\[day\.business_date\]"/)
    expect(view).toMatch(/\.gW thead th \.pend \{/)
    expect(view).not.toMatch(/<i v-if="pendingMarks\[day\.business_date\]" class="pend"><\/i>/)
  })

  it('格子里带出工作区（2026-10-08 用户要的）：班次下面一行小字', () => {
    // 周表答的是「这个人这周怎么上」—— 光有班次不知道人在哪个区，所以每格第二行写区名。
    expect(view).toMatch(/v-if="weekCellZone\(employee, day\)" class="gW-zone"/)
    expect(view).toMatch(/function weekCellZone\(employee, day\)/)
    // 只有「这天真有班次」才谈得上区：休 / 请假 / 还没排到不显示，与 `sheetHasShift` 同判据。
    expect(view).toMatch(/if \(cell\.shift_id === null \|\| cell\.shift_id === undefined\) return ''/)
    expect(view).toMatch(/\.gW-cell \.gW-zone \{/)
    // 三个字的区名（明档1）在 ~39px 宽的格子里单行放得下；再长的截断，不撑破格子。
    expect(view).toMatch(/text-overflow: ellipsis/)
    // 没配区的格子**不写占位**（那一行留给真有区的人），但无障碍那一路照旧说清楚。
    expect(view).toMatch(/未配工作区/)
    expect(view).not.toMatch(/class="gW-zone">未配/)
  })

  it('窗口外那几格点不动，也不冒充「休」', () => {
    expect(view).toMatch(/:disabled="!day\.in_window"/)
    expect(view).toMatch(/还没铺到（只铺到/)
  })

  it('只用共享样式表里有的令牌', () => {
    const tokens = [
  // 令牌自 ③-1a（t44）起分布在**两份**：兼容层与布局留在 public/hygiene-admin.css，
  // 语义层 + `--hy-*` 本体搬进了 src/styles/theme.workbench.css 的 html[data-theme="workbench"]。
  // 守卫必须读**两份合并**，否则「令牌搬家」会被误报成「令牌未定义」（t46）。
  readFileSync(join(here, '../../../../public/hygiene-admin.css'), 'utf8'),
  readFileSync(join(here, '../../../../src/styles/theme.workbench.css'), 'utf8'),
].join('\n')
    const used = new Set([...view.matchAll(/var\((--[a-z0-9-]+)\)/g)].map((m) => m[1]))
    const missing = [...used].filter((name) => !tokens.includes(`${name}:`))
    expect(missing).toEqual([])
  })
})

// 票 14（ADR 0107）把那支常驻的笔整套换掉了：底部常驻笔刷条、展开的编辑抽屉、长按
// （550ms）看 / 改这一格 —— 三样一起退役，单格的「看 / 改」由「点一格 → 动作条」接走。
// 这一组断的就是「旧三样不在」＋「新的选区手柄与动作条在」。
describe('旧三样退役：笔刷条 / 编辑抽屉 / 长按都不在了（票 14）', () => {
  it('底部常驻的笔刷条没有了（连它的控件与提示语一起）', () => {
    expect(view).not.toMatch(/gBrush-tag/)
    expect(view).not.toMatch(/gBrush-hint/)
    expect(view).not.toContain('点格子就刷上去')
    expect(view).not.toMatch(/aria-label="笔刷班次"/)
    expect(view).not.toMatch(/aria-label="笔刷工作区"/)
    // 一进周表就开笔袋那套也没了：动作条只在**选中**那一刻出现（ADR 0107 决策 2）。
    expect(view).not.toMatch(/openBrush\(\)/)
    expect(view).not.toMatch(/sheetFolded/)
    expect(view).not.toMatch(/\.gB\.brush-open/)
  })

  it('展开的编辑抽屉没有了：写一格不再走单日覆盖那条路', () => {
    expect(view).not.toMatch(/class="gSheet"/)
    expect(view).not.toMatch(/sheetTab/)
    expect(view).not.toMatch(/sheetZone/)
    expect(view).not.toMatch(/writeOverride/)
    // 单日覆盖那条接口仍然有主（月历那页的编辑器），但周表不再用它写格子。
    expect(view).not.toMatch(/api\.put\(`\/api\/scheduling\/overrides\/\$\{target\.employeeId\}/)
  })

  it('长按（550ms）语义没有了：格子只有「点一下 = 落笔」这一种手势', () => {
    expect(view).not.toMatch(/LONG_PRESS_MS/)
    expect(view).not.toMatch(/onCellDown/)
    expect(view).not.toMatch(/pressFired/)
    expect(view).not.toMatch(/@pointerdown/)
    expect(view).not.toMatch(/-webkit-touch-callout/)
    expect(view).not.toMatch(/@contextmenu\.prevent/)
  })
})

// 选区手柄与笔栏：这一组是**最小量**的「新东西在」（组件级渲染测试不在这条接缝上，
// 展开规则 / 影响格数 / 跳过预判 / 请求体 / 撤销计划全在 `utils/schedulingWeekPick` 的单测里）。
describe('选区手柄与笔栏（票 14 / ADR 0107，2026-10-13 方向修订）', () => {
  it('日期列头与人名列可点、可聚焦、有正确的角色与名称', () => {
    // 它们原来是完全不可交互的 `<th>`：现在里面是一个真按钮（Tab 到得了、读屏有名字）。
    expect(view).toMatch(/class="gW-th"/)
    expect(view).toMatch(/:aria-label="`\$\{formatDayLabel\(day\.business_date\)\}：选中这一列全店`"/)
    expect(view).toMatch(/:aria-label="`\$\{employee\.name\}：选中这一整周`"/)
    expect(view).toMatch(/:aria-pressed="pickedCols\.includes\(day\.business_date\) \? 'true' : 'false'"/)
    // 窗口外的列照旧不可选（未来窗口的上限没变）。
    expect(view).toMatch(/:disabled="!day\.in_window"/)
    // 待批琥珀角标在按钮**外面**：点它不触发选列。
    expect(view).toMatch(/<\/button>\n\s+<!-- 待批的天数[\s\S]{0,300}?class="pend"/)
  })

  it('选中高亮复用既有的选中外环，没有新增颜色引用', () => {
    expect(view).toMatch(/\.gW-cell\.on, \.gW-th\.on \{ box-shadow: 0 0 0 2px var\(--hy-mint\); \}/)
    expect(view).toMatch(/on: pickKeys\.value\.has\(`\$\{employee\.id\}\|\$\{day\.business_date\}`\)/)
    expect(view).toMatch(/const pickedRows = computed/)
  })

  it('笔栏常驻并叠在底部导航之上：那套「底栏让位」整条退役', () => {
    // 上一版是「选中那一刻动作条升起、**替换**底栏」（`body.sched-picking` + 文件末尾那条
    // 无作用域规则）；用户裁定回到常驻笔栏、底栏照旧留在原位（那是他熟悉的旧形态）。
    expect(view).not.toMatch(/sched-picking/)
    expect(view).not.toMatch(/wb-tabbar/)
    expect(view).toMatch(/v-if="panel === 'week'"/)
    // 笔栏是 `fixed` + `--z-queue`（200，壳底栏是 `--z-shell` 20），所以它压在底栏上面。
    // 这条成立的前提是页面根**不自建层叠上下文** —— 给了 z-index 就会把 fixed 的笔栏锁在
    // 底栏下面（票 12 的笔刷条正是这么坏的：ADR 0107 记着「真机截图里那句提示语被压掉一半」）。
    // 票 05 起这里断**语义名**（数字不准再出现在源码里）：要动的只有档位，见 designGuard 的清单。
    expect(view).toMatch(/\.gPick \{[\s\S]{0,240}?z-index: var\(--z-queue\)/)
    const page = view.match(/\.sched-page \{[\s\S]*?\n\}/)[0]
    expect(page).not.toMatch(/\n\s*z-index:/)
    // 「6 行压掉半屏」不回来：笔栏本体是一行 —— 没有换行、也没有 64vh 那种滚动高度。
    const pen = view.slice(view.indexOf('class="gPick"'), view.indexOf('<!-- 选区里有过去的日子'))
    expect(pen).not.toMatch(/max-height/)
    expect(pen).not.toMatch(/flex-wrap/)
  })

  it('提示区域在笔栏里，一行、能读出来（aria-live），并且会自己消失', () => {
    expect(view).toMatch(/v-if="pickNotice"/)
    expect(view).toMatch(/noticeOpen/)
    expect(view).toMatch(/aria-live="polite"/)
    expect(view).toMatch(/@click="noticeOpen = !noticeOpen"/)
  })
})
