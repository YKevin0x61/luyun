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
    expect(view).toMatch(/path: '\/login'/)
    expect(view).not.toMatch(/path: '\/workbench\/login'/)
    expect(view).toMatch(/next: router\.currentRoute\.value\.fullPath/)
  })

  it('lays out a real month grid without horizontal scrolling', () => {
    // 验收 1/5：一个月一天一格、七列，手机上不用横着划。
    expect(view).toMatch(/useScopedStylesheet\('\/hygiene-admin\.css'\)/)
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

  it('is registered as a staff page on both sides', () => {
    expect(router).toMatch(/path: '\/staff\/month', name: 'today-month'/)
    expect(router).toMatch(/views\/today\/TodayMonthView\.vue/)
    // 票 02：员工身份来自页面清单的 audience（`pageMeta('/staff/month')`），不再是
    // 路由级的 `staffAuth` 布尔。
    expect(router).toMatch(/path: '\/staff\/month'[\s\S]{0,200}?pageMeta\('\/staff\/month'\)/)
    expect(router).not.toMatch(/staffAuth/)
    expect(router).toMatch(/path: '\/staff\/month'[\s\S]{0,200}?staffProbe: false/)
    // 后端那一侧（`SPA_PAGE_ROUTES`、`HTML_AUTH_PREFIXES`、尾斜杠）由行为级契约盯着：
    // `tests/test_spa_page_routes.py` 拿 `main.app.routes` 的真实路径集合对表、
    // `tests/test_auth.py::test_staff_phone_pages_accessible_without_admin_session` 走请求。
    // 这里不再抓 `main.py` 的源码文本（票 02 #18：弱断言重说一遍强断言的事，改个写法就假红）。
    // 入口：「今天」页那张卡上的「整月」不是一句「还没开放」，而是真的走过去。
    expect(today).toMatch(/\{\s*key: 'month',\s*label: '整月',\s*to: '\/staff\/month'\s*\}/)
    expect(today).toMatch(/router\.push\(entry\.to\)/)
  })

  it('表头跟店长月历是同一份（口径 9：两边都周一开头）', () => {
    // 两边各存一份字面量（仓库里周表数组本来就按作用域各一份），这条把「一起动」钉住：
    // 店长那份改了开头而员工端没跟，这里就红。
    const heads = copy.match(/MONTH_HEADS = (\[[^\]]*\])/)
    const weekdays = manager.match(/const WEEKDAYS = (\[[^\]]*\])/)
    expect(heads && heads[1].replace(/\s/g, '')).toBe(weekdays && weekdays[1].replace(/\s/g, ''))
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
    expect(view).toMatch(/loadPendingMarks\(\)/)
  })
})
