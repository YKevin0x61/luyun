import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const view = readFileSync(join(here, '../TodayMonthView.vue'), 'utf8')
const today = readFileSync(join(here, '../TodayView.vue'), 'utf8')
const copy = readFileSync(join(here, '../../../utils/todayShift.js'), 'utf8')
const router = readFileSync(join(here, '../../../router/index.js'), 'utf8')
// 店长月历那份周表：口径 9 说「员工端表头跟店长月历一致」；
// 2026-09-30 两边一起从周日开头改成**周一开头**（店里的口头习惯）。
const manager = readFileSync(
  join(here, '../../scheduling/SchedulingCalendarView.vue'),
  'utf8',
)

describe('员工端「整月」页（票 06）', () => {
  it('reads its own endpoint and nothing else', () => {
    // 跟「今天」页同一扇门：员工 cookie，接口上没有 employee_id 可填 ——
    // 只看得到自己的班（验收 4）。
    expect(view).toMatch(/staffRequest\(`\/api\/scheduling\/me\/month/)
    // 请求里没有 employee_id：读谁由服务端按 cookie 定（注释里可以提这个词，URL 里不行）。
    expect(view).not.toMatch(/staffRequest\([^)]*employee_id/)
    expect(view).not.toMatch(/\/api\/hygiene/)
    expect(view).not.toMatch(/\/api\/scheduling\/(roster|calendar|day|rules)/)
    // 401 回员工登录（票 03 起是 /login 的员工栏），把当前地址整个带过去。
    // 票 12 起落点算在 `utils/loginNext.js` 的 `loginRedirectTarget` 一处（它拒绝在
    // 登录页上再跳一次，O2 的 `?next=` 套娃就是这么来的）。
    expect(view).not.toMatch(/path: '\/workbench\/login'/)
    expect(view).toMatch(/import \{ loginRedirectTarget \} from '\.\.\/\.\.\/utils\/loginNext'/)
    expect(view).toMatch(/loginRedirectTarget\(router\.currentRoute\.value\)/)
  })

  it('lays out a real month grid without horizontal scrolling', () => {
    // 验收 1/5：一个月一天一格、七列，手机上不用横着划。
    // 票 06：共享样式表不再由页面挂（跟着 `<html data-workbench>` 走，路由一处安装）。
    expect(view).not.toMatch(/useScopedStylesheet\('\/hygiene-admin\.css'\)/)
    expect(view).toMatch(/class="month-page hygiene-staff"/)
    expect(view).toMatch(/repeat\(7,\s*1fr\)/)
    for (const cls of ['mNav', 'mHead', 'mGrid', 'mD', 'mLegend', 'mFoot']) {
      expect(view).toContain(cls)
    }
    // 表头七格从 util 来（周一开头，跟服务端 `lead` 同一套）。
    expect(view).toMatch(/v-for="head in MONTH_HEADS"/)
    // 月首那几格空格：服务端给的 `lead`，页面上不自己算星期几。
    expect(view).toMatch(/v-for="n in lead"/)
  })

  it('paints the four states differently and has a legend for them', () => {
    // 验收 1：「休的日子一眼能分辨」—— 休、上班、班次已调整、还没排四种格子。
    for (const tone of ['shift', 'rest', 'moved', 'none']) {
      expect(view).toContain(`.mD.${tone}`)
    }
    for (const word of ['上班', '休', '班次已调整', '还没排']) {
      expect(view).toContain(word)
    }
    // 今天那格另有记号（跟店长月历一个做法）。
    expect(view).toMatch(/\{\s*today:\s*cell\.is_today/)
  })

  it('窗口外的格子淡掉，并说清楚那不是「那天没排」', () => {
    // 票 02 #4 的店长先例（`.gB-d.mute` / `opacity: .35`）：同一个 `window_end` 两边一个读法。
    expect(view).toMatch(/mute: dayBeyondWindow\(cell, windowEnd\)/)
    expect(view).toMatch(/\.mD\.mute\s*\{[^}]*opacity:\s*\.35/)
    // 整月在窗外 vs 只有后半段在窗外：两种说法都要有，不能只说「还没铺到」。
    expect(view).toMatch(/v-if="outside"/)
    expect(view).toMatch(/v-else-if="beyond"/)
  })

  it('把「休 / 还没排 / 已调整」的翻译交给 todayShift，不在这页重写一份', () => {
    expect(view).toMatch(/from '\.\.\/\.\.\/utils\/todayShift'/)
    for (const fn of [
      'MONTH_HEADS',
      'monthLabel',
      'monthCell',
      'stepMonth',
      'dayBeyondWindow',
    ]) {
      expect(view).toContain(fn)
    }
    // 三态的判据读两遍就会漂：页面里不许再出现 scheduled / shift_id。
    expect(view).not.toMatch(/day\.scheduled/)
    expect(view).not.toMatch(/shift_id/)
    // 每格只算一次四态（`:class` 与格子里的字共用同一份）。
    expect(view).toMatch(/days\.value\.map\(\(day\) => \(\{ \.\.\.day, \.\.\.monthCell\(day\) \}\)\)/)
    expect(copy).toMatch(/day\.scheduled/)
    expect(copy).toMatch(/day\.shift_id == null/)
  })

  it('翻月按拿到的月份加减，到窗口末就停，坏月份留一条走得掉的路', () => {
    // 验收 2：能翻上个月和下个月。判据在 util 里（`stepMonth` 的直接断言在
    // utils/__tests__/todayShift.test.js），这里只钉页面确实走它、没有自己算边界。
    expect(view).toMatch(/stepMonth\(month\.value, delta, windowEnd\.value\)/)
    expect(view).toMatch(/@click="step\(-1\)"/)
    expect(view).toMatch(/@click="step\(1\)"/)
    expect(view).toMatch(/aria-label="上个月"/)
    expect(view).toMatch(/aria-label="下个月"/)
    // 往后到展开窗口末：箭头是灰的（不然只会拿到一个空月）。
    expect(view).toMatch(/:disabled="!canNext"/)
    expect(view).toMatch(/const canNext = computed/)
    // 翻出去以后有一条回到本月的路；错误态（手改地址栏那种坏月份）也必须有。
    expect(view.match(/回到本月/g) || []).toHaveLength(2)
    expect(view).toContain('backToThisMonth')
    expect(view).toMatch(/const isThisMonth = computed/)
  })

  it('没有班次名可写死，也没有钟点', () => {
    expect(view).not.toMatch(/['"]白班['"]/)
    expect(view).not.toMatch(/['"]夜班['"]/)
    expect(view).not.toMatch(/\d{1,2}:\d{2}/)
    expect(copy).not.toMatch(/\d{1,2}:\d{2}/)
  })

  it('says what an empty cell is, and what is not built yet', () => {
    // 空格子 = 还没排（新装机时本月前半月就是空的）：页脚直说，别让人以为是「休」。
    expect(view).toContain('空着的格子是那天还没排')
    // 票 08：请假已经接上了（批过的假写「请假」，跟排班给的「休」分开）。
    // 票 06 那条角标也接上了 —— 页脚原来那句「换班的角标等那张票做好再加」是票 09 时代的
    // 占位，留着会让人等一个不存在的东西；现在改成说清右上角那个点是什么。
    expect(view).toContain('批过的假写「请假」')
    expect(view).toContain('右上角的小点')
    expect(view).not.toContain('等那张票做好再加')
    expect(view).not.toContain('请假、换班的角标')
    // 翻到展开窗口之外的那些月：整片空要解释，不是排班丢了。
    expect(view).toMatch(/data\.window_end/)
    expect(view).toContain('还没铺到')
  })

  it('请假那格有自己的颜色与图例，不跟「休」混（票 08）', () => {
    // 语气与文案的判据在 util 里（todayShift.test.js）；这一页只管上色与图例。
    expect(view).toMatch(/\.mD\.leave \.s\s*\{[^}]*var\(--hy-aqua\)/)
    expect(view).toMatch(/\.dot\.leave\s*\{[^}]*var\(--hy-aqua\)/)
    // 图例三条排开：上班 / 休 / 请假（少一条员工就猜「这格子什么意思」）。
    expect(view).toMatch(/class="dot shift"[^<]*\/>上班/)
    expect(view).toMatch(/class="dot rest"[^<]*\/>休/)
    expect(view).toMatch(/class="dot leave"[^<]*\/>请假/)
    expect(copy).toContain("if (tone === 'leave') return { text: '请假', tone }")
  })

  it('半天休那格有自己的颜色与图例，不跟「休」「上班」混（票 07）', () => {
    // 判据在 util 里（`todayShift.test.js` 的四态表）；这一页只管上色与图例。
    // 颜色走语义令牌（`--info`）而不是新增 `--hy-*`：设计护栏的存量棘轮只减不增。
    expect(view).toMatch(/\.mD\.half \.s\s*\{[^}]*var\(--info\)/)
    expect(view).toMatch(/\.dot\.half\s*\{[^}]*var\(--info\)/)
    expect(view).toMatch(/class="dot half"[^<]*\/>休半天/)
    expect(copy).toContain("if (tone === 'half') return { text: '休半天', tone }")
    // 四态各自的名字都在图例上（少一条员工就猜「这格子什么意思」）。
    for (const word of ['上班', '休', '休半天', '还没排']) {
      expect(view).toContain(word)
    }
  })

  it('is registered as a staff page on both sides', () => {
    expect(router).toMatch(/workbenchStaffPage\('\/workbench\/me\/month', 'today-month'/)
    expect(router).toMatch(/views\/today\/TodayMonthView\.vue/)
    // 票 02：员工身份来自页面清单的 audience —— 三页共用的工厂 `workbenchStaffPage`
    // 里由 `pageMeta(path)` 带进来，不再是路由级的 `staffAuth` 布尔。
    expect(router).toMatch(/function workbenchStaffPage\(path, name, loader\)[\s\S]{0,300}?pageMeta\(path\)/)
    expect(router).not.toMatch(/staffAuth/)
    expect(router).toMatch(/function workbenchStaffPage\(path, name, loader\)[\s\S]{0,300}?staffProbe: false/)
    // 后端那一侧（`SPA_PAGE_ROUTES`、`HTML_AUTH_PREFIXES`、尾斜杠）由行为级契约盯着：
    // `tests/test_spa_page_routes.py` 拿 `main.app.routes` 的真实路径集合对表、
    // `tests/test_auth.py::test_staff_phone_pages_accessible_without_admin_session` 走请求。
    // 这里不再抓 `main.py` 的源码文本（票 02 #18：弱断言重说一遍强断言的事，改个写法就假红）。
    // 入口：「今天」页那张卡上的三颗按钮都真的往月历走（票 15 起请假与换班也在这条路上，
    // 各自带一个 `act`；整月不带）。落点是同一个页面组件，另开的断言在上面那条。
    expect(today).toMatch(/key: 'month', label: '整月', to: month/)
    expect(today).toMatch(/router\.push\(entry\.to\)/)
  })

  it('表头跟店长月历是同一份（口径 9：两边都周一开头）', () => {
    // 两边各存一份字面量（仓库里周表数组本来就按作用域各一份），这条把「一起动」钉住：
    // 店长那份改了开头而员工端没跟，这里就红。
    const heads = copy.match(/MONTH_HEADS = (\[[^\]]*\])/)
    const weekdays = manager.match(/const WEEKDAYS = (\[[^\]]*\])/)
    expect(heads && heads[1].replace(/\s/g, '')).toBe(weekdays && weekdays[1].replace(/\s/g, ''))
  })

  it('支持 ?date=&act= 直达：今天页那两颗按钮落在同一天那一屏上（票 15）', () => {
    // 「今天」页的请假 / 换班带着 `?date=<营业日>&act=leave|swap` 跳过来：先读那一天
    // 所在的月份（跨月时不然会落在一格空白上），再落到那一格、直接进那一屏。
    expect(view).toMatch(/import \{ actFromQuery, dateFromQuery, monthOfDate \} from '\.\.\/\.\.\/utils\/staffDayActions'/)
    expect(view).toMatch(/wantedDate\.value = dateFromQuery\(route\.query\)/)
    expect(view).toMatch(/wantedAct\.value = actFromQuery\(route\.query\)/)
    expect(view).toMatch(/await load\(monthOfDate\(wantedDate\.value\) \|\| undefined\)/)
    // 解析完就清掉：留着它会在关掉弹层之后又被打开一次。
    expect(view).toMatch(/wantedDate\.value = ''/)
    expect(view).toMatch(/function openWanted\(\)/)
  })

  it('标出「申请中」的那几天 —— 票 06 那条一直没有数据源的角标', () => {
    // 转出时说「这一票没有数据源」；现在有了：`/me/requests` 里自己那几条**还没落定**的
    // 申请（等对方点头 / 等店长批），按天摊开标在格子上。批了、驳了、撤了都不算 ——
    // 那些是结果，格子写的就是结果本身。
    expect(view).toMatch(/api\/scheduling\/me\/requests/)
    expect(view).toMatch(/eachDayInRange\(/)
    expect(view).toMatch(/pending_peer/)
    expect(view).toMatch(/pending_manager/)
    expect(view).toMatch(/v-if="pendingDates\.has\(cell\.business_date\)"/)
    expect(view).toMatch(/class="dot pending"/)
    // 颜色跟「班次已调整」的琥珀分开（两件事，同色最容易看错）。
    expect(view).toMatch(/\.dot\.pending \{[\s\S]{0,90}?var\(--hy-coral\)/)
    // 页脚不再说「等那张票做好再加」——那张票早做完了，这句话留着会让人等一个不存在的东西。
    expect(view).not.toContain('换班的角标等那张票做好再加')

    expect(view).toContain('右上角的小点')
  })

  it('订阅排班 nudge：店长改了某一天，格子跟着变（票 10 收尾）', () => {
    expect(view).toMatch(/id: 'today-month'/)
    expect(view).toMatch(/load\(month\.value, true\)/)
    // 格子上的两个记号（我的申请 / 有人找我换班）读的是同一条 `/me/requests`：一次请求。
    expect(view).toMatch(/loadRequestMarks\(\)/)
  })

  it('格子上写那天全店几个人在班，点开某天看当天名单（票 13）', () => {
    // 验收 1：人数与「我的班」是两个字段、两处显示 —— 人数是格子里的第三行小字。
    expect(view).toMatch(/staffCountLabel\(cell\.staff_count\)/)
    expect(view).toMatch(/\.mD \.h\s*\{/)
    // 验收 2/3：点格子弹出**那一天那一屏**。名单（票 13）与「那一天能办的两件事」
    // （票 15：请假 / 换班）在同一屏里 —— 名单的判据（`/me/day`、`isMe`、`offLabel`、
    // `myDayText`）跟着它搬进了 `components/scheduling/StaffDaySheet.vue`，那边的测试
    // 有真挂载用例；这一页只负责把「哪一天」交出去。
    expect(view).toMatch(/aria-haspopup="dialog"/)
    expect(view).toMatch(/<StaffDaySheet/)
    expect(view).toMatch(/:day="detailDay"/)
    expect(view).toMatch(/:initial-act="openAct"/)
    // 点的是哪一天存**日期**不存那一格对象：月历重读（nudge / 翻月）之后，那一屏里的
    // 「我这天」要跟着新数据走，而不是停在打开那一刻的快照上。
    expect(view).toMatch(/cells\.value\.find\(\(cell\) => cell\.business_date === detailDate\.value\)/)
    expect(view).not.toMatch(/const detailDay = ref/)
    // 办完（提交 / 撤回 / 回应）让格子上的记号跟着变，并防住「菜单栏那条」的重复：
    expect(view).toMatch(/@changed="loadRequestMarks"/)
    // 窗口外的格子不可点：淡 = 还没铺到，点它只会问出一个注定空白的答案。
    expect(view).toMatch(/:disabled="dayBeyondWindow\(cell, windowEnd\)"/)
    // 页脚那句「只看得到你自己的班」作废（票 13 改的口径）：留着会跟页面自相矛盾。
    expect(view).not.toContain('只看得到你自己的班')
  })
})
