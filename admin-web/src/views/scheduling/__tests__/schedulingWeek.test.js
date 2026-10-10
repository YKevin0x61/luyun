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

  it('点一格从底下弹抽屉，并且给页面留出底部空间', () => {
    // 2026-10-04 起这一下走 `onCellClick`：抽屉折叠着（手里有笔）时它落笔，展开时才是开抽屉。
    expect(view).toMatch(/@click="onCellClick\(employee, day\)"/)
    expect(view).toMatch(/class="gSheet"/)
    expect(view).toMatch(/\.gSheet \{[\s\S]{0,200}?position: fixed/)
    // 门店那套的抽屉压住了下面几行员工（截图里能看到第 4、5 行被压掉一半）——
    // 这里弹出来时给页面补底部内边距，人能滚上来。
    expect(view).toMatch(/\.gB\.sheet-open \{ padding-bottom: 320px; \}/)
    // 只有**展开着**才留 320px；折成笔刷条时只留一条（\.gB\.brush-open）。
    expect(view).toMatch(/'sheet-open': !!sheet && !sheetFolded/)
    // 点同一格 = 收起来：抽屉不会自己走开，得有个关掉的手势。
    expect(view).toMatch(/if \(same\) \{[\s\S]{0,60}?closeSheet\(\)/)
  })

  it('抽屉里那一栏就是「班次 / 周期」，且周期只看不改', () => {
    expect(view).toMatch(/sheetTab === 'shift'/)
    expect(view).toMatch(/sheetTab === 'cycle'/)
    // 写路径复用票 07 的单日覆盖，一个写接口都不新增。
    expect(view).toMatch(/api\.put\(`\/api\/scheduling\/overrides\/\$\{employeeId\}\/\$\{date\}`/)
    expect(view).toMatch(/api\.delete\(`\/api\/scheduling\/overrides\/\$\{target\.employeeId\}\/\$\{target\.date\}`\)/)
    expect(view).toMatch(/is_rest: true/)
    // 工作区不带 = 跟这个班次的固定区（票 03）；要单独改区走月历那页的编辑器。
    expect(view).toMatch(/writeCell\(\{ shift_id: shift\.id, zone_id: null \}\)/)
    // 规则是另一件事：抽屉只把人送到名单页，不在周表上改。
    expect(view).toMatch(/await openPanel\('roster'\)/)
    expect(view).toMatch(/openCycle\(row\)/)
  })

  it('抽屉里能只改这一天的「工作区」（2026-10-04 补）', () => {
    // 原先抽屉里改不了区：周表上发现"今天他不在案板"，得跳去月历那页点这个人才行。
    // 补的是**单日**改区（长期固定区仍然归名单）——写路径没变，还是单日覆盖那条。
    expect(view).toMatch(/const sheetZone = ref\(''\)/)
    expect(view).toMatch(/function pickZone\(value\) \{/)
    // 只改区：班次不动，带着当天那个班次一起提交（后端「只改区」走的就是这条路）。
    expect(view).toMatch(
      /writeCell\(\{ shift_id: cell\.shift_id, zone_id: value === '' \? null : Number\(value\) \}\)/
    )
    // 休 / 请假 / 还没排到的格子没有区：下拉禁掉，并说一句。
    expect(view).toMatch(/const sheetHasShift = computed/)
    expect(view).toMatch(/:disabled="sheetBusy \|\| !sheetUndoable \|\| !sheetHasShift"/)
    expect(view).toContain('休 / 请假的日子没有区')
    // 下拉按 **id** 预填、按服务端回写后的结果重新落位（按名字反查会指到重名的区）。
    expect(view).toMatch(/function syncSheetZone\(\)/)
    expect(view).toMatch(/String\(cell\.zone_id\)/)
    expect(view).toMatch(/syncSheetZone\(\)\n  syncBrushFromCell\(\)\n\}/)
  })

  it('过去的日子改不动，说的是实话而不是给个按不动的按钮', () => {
    expect(view).toMatch(/sheet\.value\.date >= data\.today/)
    expect(view).toMatch(/过去的日子不改写/)
    expect(view).toMatch(/:disabled="sheetBusy \|\| !sheetUndoable/)
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

describe('周表笔刷（2026-10-04：抽屉折叠成一行就是那支笔）', () => {
  it('折叠着点格子 = 落笔，不弹抽屉', () => {
    expect(view).toMatch(/if \(sheetFolded\.value\) \{\n\s+paintCell\(employee, day\)/)
    expect(view).toMatch(/@click="onCellClick\(employee, day\)"/)
    expect(view).toMatch(/import \{ BRUSH_REST, BRUSH_SHIFT, brushPayload, canPaintOn \}/)
  })

  it('长按格子 = 展开看 / 改这一格（pointer 计时，长按那次 click 被吞掉）', () => {
    expect(view).toMatch(/@pointerdown="onCellDown\(employee, day\)"/)
    expect(view).toMatch(/@contextmenu\.prevent/)
    expect(view).toMatch(/LONG_PRESS_MS = 550/)
    expect(view).toMatch(/if \(pressFired\) \{/)
    // 展开时不需要长按（点一下本来就是开抽屉）。
    expect(view).toMatch(/if \(!sheetFolded\.value\) return/)
  })

  it('笔刷条上就是班次、工作区与「休」，控件沿用抽屉那套外观', () => {
    expect(view).toMatch(/aria-label="笔刷班次"/)
    expect(view).toMatch(/aria-label="笔刷工作区"/)
    expect(view).toMatch(/toggleBrushRest\(\)/)
    expect(view).toMatch(/class="gBrush-tag">刷</)
    expect(view).toMatch(/点格子就刷上去 · 长按格子看 \/ 改这一格/)
  })

  it('落笔走抽屉同一条写路径；没选班次、过去的日子都在客户端先拦', () => {
    expect(view).toMatch(/await writeOverride\(employee\.id, day\.business_date, payload\)/)
    expect(view).toMatch(/canPaintOn\(day\.business_date/)
    expect(view).toMatch(/过去的日子不改写：往前翻只能看。/)
    expect(view).toMatch(/先在上面选一个班次（或点「休」），再点格子。/)
  })

  it('打开一格时笔跟着这一格走，但休 / 假 / 还没排的格子不动笔', () => {
    expect(view).toMatch(/syncBrushFromCell\(\)/)
    expect(view).toMatch(/cell\.shift_id !== null && cell\.shift_id !== undefined/)
    expect(view).toMatch(/看了一眼休假日就把笔也换成"休"/)
  })

  it('长按别被手机当成选中文字', () => {
    expect(view).toMatch(/-webkit-touch-callout: none; user-select: none;/)
  })
})

describe('笔袋默认开着（2026-10-05 用户要的）', () => {
  it('一切到周表就开笔袋：不用先点一格再收起', () => {
    expect(view).toMatch(/await loadWeek\(weekStart\.value \|\| mondayOf\(today\.value\)\)\n\s+\/\/ 读完再开笔袋[\s\S]{0,80}?openBrush\(\)/)
    expect(view).toMatch(/function openBrush\(\) \{\n\s+sheet\.value = null\n\s+sheetFolded\.value = true/)
    // 工作区名单也是懒加载的：不先补齐，笔袋里那个下拉就只有「跟固定区」一项。
    expect(view).toMatch(/if \(!zones\.value\.length\) await loadRoster\(\)\n\s+ensureBrushShift\(\)/)
  })

  it('没有"正在看的那一格"也渲染抽屉（只在周表这一栏）', () => {
    expect(view).toMatch(/v-if="\(sheet \|\| sheetFolded\) && panel === 'week'"/)
    // 没有格子时折叠按钮没有意义：笔袋本来就是折着的。
    expect(view).toMatch(/<button\n\s+v-if="sheet"\n\s+class="gSheet-fold"/)
  })

  it('「收起」连笔袋一起收；笔的默认班次等班次名单到货再补', () => {
    expect(view).toMatch(/sheetFolded\.value = false \/\/ 收起 = 连笔袋一起收/)
    expect(view).toMatch(/function ensureBrushShift\(\)/)
    expect(view).toMatch(/watch\(activeShifts, \(\) => \{\n\s+if \(sheetFolded\.value\) ensureBrushShift\(\)/)
  })

  it('笔袋只占一条的高度，最后一行的格子还能滚上来', () => {
    expect(view).toMatch(/\.gB\.brush-open \{ padding-bottom: 88px; \}/)
    expect(view).toMatch(/'brush-open': sheetFolded && panel === 'week'/)
  })
})
