import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { resolveStates, resolveVar, sameValueCollisions } from './themeTokens.js'

const here = dirname(fileURLToPath(import.meta.url))
const view = readFileSync(join(here, '../TodayView.vue'), 'utf8')
const copy = readFileSync(join(here, '../../../utils/todayShift.js'), 'utf8')
const router = readFileSync(join(here, '../../../router/index.js'), 'utf8')
const login = readFileSync(join(here, '../../LoginView.vue'), 'utf8')
const mainPy = readFileSync(join(here, '../../../../../main.py'), 'utf8')

describe('员工端「今天」页（原型 A）', () => {
  it('reads its own endpoint and nothing else', () => {
    // 员工会话读的是排班自己那条门：路径写死 `/me`，不带模板参数 ——
    // 读谁由服务端按 cookie 定，页面上没有 employee_id 可以填。
    expect(view).toMatch(/staffRequest\('\/api\/scheduling\/me'\)/)
    // 路径里唯一带变量的是「撤回自己那条申请」的申请号（票 08）：
    // 归属还是服务端按 cookie 判（别人的申请撤回只会拿到 404）。
    expect(view).not.toMatch(/\/api\/scheduling\/[^'"\s]*employee_id/)
    // 这一页只剩**自己那份资料**那一条卫生门（`/staff/me`：给「我的」那一块与能力卡用）
    // —— 路径里同样没有 employee_id，读谁由 cookie 定。今天的日常与仪容仪表那两条
    // （`/staff/daily-work`、`/staff/attire`）2026-10-14 随那块卡搬去工作台首页
    // （`components/staff/StaffHygieneCard.vue`），这一页不再发；管理端那一侧的卫生接口
    // 一个都不该出现在这里。
    expect(view).toMatch(/staffRequest\('\/api\/hygiene\/staff\/me'\)/)
    expect(view).not.toMatch(/staffRequest\('\/api\/hygiene\/staff\/daily-work'\)/)
    expect(view).not.toMatch(/staffRequest\('\/api\/hygiene\/staff\/attire'\)/)
    expect(view).not.toMatch(/\/api\/hygiene\/[^'"\s]*employee_id/)
    expect(view).not.toMatch(/\/api\/hygiene\/admin/)
    // 401 回员工登录（票 03 起是 /login 的员工栏），把当前地址整个带过去（跟卫生首页一个走法）。
    // 票 12 起落点算在 `utils/loginNext.js` 的 `loginRedirectTarget` 一处：它会拒绝在
    // 登录页上再跳一次（O2 的 `?next=` 套娃），所以页面里不再手拼 `{ path, query }`。
    expect(view).not.toMatch(/path: '\/workbench\/login'/)
    expect(view).toMatch(/import \{ loginRedirectTarget \} from '\.\.\/\.\.\/utils\/loginNext'/)
    expect(view).toMatch(/loginRedirectTarget\(router\.currentRoute\.value\)/)
  })

  it('keeps the chosen prototype A shape', () => {
    // 用户选的员工端首页 = today-variants.html 的 A · 两块：
    // 整屏 → 排班卡 → 往后三天 → 页脚（下半张卫生卡下一张票接）。
    // 票 06：共享样式表不再由页面挂（它跟着 `<html data-workbench>` 走，路由一处安装）——
    // 这一页原先与壳各挂一份，同页两条指向同一文件的 <link>。
    expect(view).not.toMatch(/useScopedStylesheet\('\/hygiene-admin\.css'\)/)
    expect(view).toMatch(/class="today-page hygiene-staff"/)
    for (const cls of ['tA-card sched', 'tA-hd', 'shift', 'acts', 'tA-h', 'next3', 'tA-foot']) {
      expect(view).toContain(cls)
    }
    expect(view).toMatch(/repeat\(3,\s*1fr\)/)
  })

  it('renders however many shifts the API returns', () => {
    // 班次可配置（票 11 会加「早班」之类）：页面上不写死白/夜两个名字，
    // 班次与工作区都从 `/me` 下来的那一行上取。
    expect(view).not.toMatch(/['"]白班['"]/)
    expect(view).not.toMatch(/['"]夜班['"]/)
    expect(copy).not.toMatch(/['"]白班['"]/)
    expect(copy).not.toMatch(/['"]夜班['"]/)
    expect(copy).toMatch(/day\.shift_name/)
    expect(copy).toMatch(/day\.zone_name/)
  })

  it('says the three things an employee can be', () => {
    // 验收 2/3/6：「没你的班」「休」「白班 · 案板」是三句不同的话。
    // 判据（`scheduled` / 空的 shift_id）只在 utils/todayShift.js 里读一次，
    // 真单测在那儿（src/utils/__tests__/todayShift.test.js）；这一页只管摆版式。
    expect(copy).toContain('今天没有你的班')
    expect(copy).toContain('今天休息')
    expect(copy).toMatch(/\$\{day\.zone_name\}/)
    expect(copy).toMatch(/day\.scheduled/)
    expect(copy).toMatch(/day\.shift_id == null/)
    // 明天 / 后天写在卡片上。
    expect(copy).toContain('明天')
    expect(copy).toContain('后天')
  })

  it('把「休 / 还没排」的翻译交给 todayShift，不在这页重写一份', () => {
    expect(view).toMatch(/from '\.\.\/\.\.\/utils\/todayShift'/)
    for (const fn of [
      'dayLabel',
      'shiftText',
      'todayHeadline',
      'todaySubline',
      'todayTone',
      'nextTwoLine',
    ]) {
      expect(view).toContain(fn)
    }
    // 三态的判据读两遍就会漂：页面里不许再出现 scheduled / shift_id。
    expect(view).not.toMatch(/day\.scheduled/)
    expect(view).not.toMatch(/shift_id/)
  })

  it('卡上那行大字：五态色值两两不同（票 07 / C 批返工第二轮）', () => {
    // 两轮返工都栽在同一件事上：撞车撞的是**值**不是名字。第一轮 `.shift.half` 取
    // `--info`，值跟「请假」的 `--hy-aqua` 一样；第二轮改用 `--warn`，又跟「已调整」的
    // `--hy-amber` 一样。只断一对时两轮都是绿的 —— 所以要 work / rest / half / leave /
    // moved 一起解析回令牌层的值，整组两两比（`themeTokens.js` 的 `sameValueCollisions`）。
    const headline = resolveStates(view, [
      ['work', /\.shift\s*\{[^}]*color:\s*var\((--[\w-]+)\)/],
      ['rest', /\.shift\.rest\s*\{[^}]*var\((--[\w-]+)\)/],
      ['half', /\.shift\.half\s*\{[^}]*var\((--[\w-]+)\)/],
      ['leave', /\.shift\.leave\s*\{[^}]*var\((--[\w-]+)\)/],
      ['moved', /\.shift\.moved\s*\{[^}]*var\((--[\w-]+)\)/],
    ])
    expect(headline.half.name).toBe('--accent-alt')
    expect(sameValueCollisions(headline), '两个态指到同值令牌，卡上就会有两句一样颜色的话')
      .toEqual([])
  })

  it('往后三天的格子认识「休半天」：不给它上班色（票 07 / C 批返工）', () => {
    // 三格的类名来自同一个 `shiftTone(day)`（`.next3 .s` 上的 `:class`），所以
    // `half` 这个类**本来就已经带上**了；返工前缺的是这条规则 —— 没有它就落回
    // `.next3 .s` 的兜底色（上班的薄荷），员工看着那天跟平常上班一样。
    expect(view).toMatch(/<span class="s" :class="shiftTone\(day\)"/)
    // 那一行五态一起比，判据与卡上那行大字同一套。
    const row = resolveStates(view, [
      ['work', /\.next3 \.s\s*\{[^}]*color:\s*var\((--[\w-]+)\)/],
      ['rest', /\.next3 \.s\.rest\s*\{[^}]*var\((--[\w-]+)\)/],
      ['half', /\.next3 \.s\.half\s*\{[^}]*var\((--[\w-]+)\)/],
      ['leave', /\.next3 \.s\.leave\s*\{[^}]*var\((--[\w-]+)\)/],
      ['moved', /\.next3 \.s\.moved\s*\{[^}]*var\((--[\w-]+)\)/],
    ])
    expect(sameValueCollisions(row), '三格里两个态同色，那一天就看不出是哪种安排')
      .toEqual([])
    // 点名那条最要紧的区分：它不能落回上班色（薄荷），`half` 规则缺了就会这样。
    expect(row.half.value).not.toBe(row.work.value)
    // 一色一处：跟卡上那行大字同源，改色只改一个地方。
    const headline = resolveVar(view, /\.shift\.half\s*\{[^}]*var\((--[\w-]+)\)/)
    expect(row.half.name).toBe(headline.name)
    // 类名的来源仍是语气那一张表（页面不自己判 half_rest，也不另起一个类名）。
    expect(copy).toMatch(/if \(day\.half_rest\) return 'half'/)
  })

  it('offers the three entries and no clock time anywhere', () => {
    // 验收 4：三个入口（这一票点进去还是空的，页面上直说「还没开放」）。
    for (const label of ['请假', '换班', '整月']) {
      expect(view).toContain(label)
    }
    expect(view).toMatch(/还没开放/)
    // 验收 5：整屏没有钟点 —— 班次没有起止时刻（文案与页面都不许冒出来）。
    expect(view).not.toMatch(/\d{1,2}:\d{2}/)
    expect(copy).not.toMatch(/\d{1,2}:\d{2}/)
  })

  it('is registered as a staff page on both sides', () => {
    // SPA 页面要登记在：vue-router、main.py 的 SPA_PAGE_ROUTES（直连/反代硬导航）。
    // **票 03 起不再登记进免墙表**：员工页搬进工作台（`/workbench/me/*`）之后，页面壳的
    // 放行根据是「任一会话有效」（`main.py` 的 `_is_workbench_page` + `_has_staff_session`），
    // 不是路径 —— 工作台是「进去要登录」的页面区，服务端行为由
    // `tests/test_auth.py` 的两条请求级用例盯着（未登录 302、带员工会话 200）。
    expect(router).toMatch(/workbenchStaffPage\('\/workbench\/me\/today', 'today'/)
    expect(router).toMatch(/views\/today\/TodayView\.vue/)
    // 票 02：「这是员工页」不再由路由级的 `staffAuth` 布尔表达，而是页面清单里的
    // audience —— 三页共用的工厂 `workbenchStaffPage` 里由 `pageMeta(path)` 带进来
    // （守卫与 App.vue 都按它判）。
    expect(router).toMatch(/function workbenchStaffPage\(path, name, loader\)[\s\S]{0,300}?pageMeta\(path\)/)
    expect(router).not.toMatch(/staffAuth/)
    expect(mainPy).toMatch(/SPA_PAGE_ROUTES = \([\s\S]*?"\/workbench\/me\/today"/)
    // 反向：旧的 `/staff` 与 `/staff/` 一条都不在免墙表里（audit 条目 15 的那条裸条目）。
    expect(mainPy).not.toMatch(/HTML_AUTH_EXACT = \{[^}]*"\/staff"/)
    expect(mainPy).not.toMatch(/HTML_AUTH_PREFIXES = \([\s\S]*?"\/staff\/"/)
    // 旧路径连 vue-router 里的别名都不许留（票 04 的取舍）。
    expect(router).not.toMatch(/path: '\/staff/)
    expect(router).not.toMatch(/path: '\/today'/)
    expect(router).not.toMatch(/path: '\/today\/month'/)
    expect(router).not.toMatch(/path: '\/hygiene'/)
    // 票 03 起员工登录页是 `/login` 的员工栏：守卫把原目标带上，不再是 `/hygiene/login`。
    expect(router).not.toMatch(/'\/workbench\/login'/)
    expect(router).toMatch(/path: '\/login', query: \{ next: buildLoginNextFromRoute\(to\) \}/)
  })

  it('does not wait for the session probe before showing the shift', () => {
    // 这一页自己那次请求就分得清 401 与断网（`load()`），守卫那次探针是重复劳动：
    // 弱网下先白等一次超时（最长 4 秒）才轮到排班那条请求。三页都从同一个工厂拿
    // 这个标记（票 03 把卫生待办那页也收齐了）。
    expect(router).toMatch(/function workbenchStaffPage\(path, name, loader\)[\s\S]{0,300}?staffProbe: false/)
    expect(router).toMatch(/if \(to\.meta\.staffProbe === false\) return true/)
  })

  it('drops the employee on the today page after logging in', () => {
    // 验收 1：登录后落到这一页。判据不在登录页里手写（那份比 util 弱，放松了没人拦），
    // 而在 `utils/loginNext.js` 的 `resolveStaffNext` —— 真单测在 loginNext.test.js。
    expect(login).toMatch(/resolveStaffNext\(route\.query\.next\)/)
    expect(login).not.toContain('hygiene|today')
  })
})

describe('「我的成绩」卡（2026-10-05 用户裁定）', () => {
  it('读员工自己的成绩门，路径上没有 employee_id', () => {
    expect(view).toMatch(/staffRequest\(`\/api\/hygiene\/staff\/me\/stats\?days=\$\{SCORE_DAYS\}`\)/)
    expect(view).toMatch(/const SCORE_DAYS = 7/)
    expect(view).not.toMatch(/\/api\/hygiene\/admin/)
  })

  it('排在「我的」之前：这一页读序是"今天上不上班 → 我干得怎么样 → 账号设置"', () => {
    // 位置是那一票的实质决定（用户让我自己判）：成绩属于「我干得怎么样」，而「我的」那张
    // 是设置（不是活），所以成绩在它前面。**2026-10-14 起这一页不再是员工登录落点**
    // （落点搬去工作台首页「今天」，那儿今天要交什么一眼看得见）—— 成绩卡留在这里，
    // 员工从首页的「我的」那一格进来还能看到它；要不要跟着搬是另一件事，见交接说明。
    const score = view.indexOf('class="tA-card score"')
    const me = view.indexOf('class="tA-card me"')
    expect(score).toBeGreaterThan(-1)
    expect(me).toBeGreaterThan(score)
    // 今天要交的那一块整块不在这页了（它随搬走一起走的，不留空壳）。
    expect(view).not.toMatch(/class="tA-card hyg"/)
  })

  it('空态不给数字，更没有 0%（`pass_rate === null` 是"还没开始"，不是"被扣分"）', () => {
    // 服务端用 null 表示"没有分母"。这里钉住那个分支：空态渲染的是 `.score-empty`
    // 那一句话，数字那一段（`.score-rate`）整个不在。
    expect(view).toMatch(/v-else-if="score\.passRate === null"/)
    expect(view).toMatch(/class="score-empty"/)
    expect(view).toContain('还没交过活，交一项就有记录')
    // 百分率整条链子都拆了（t59）：不再有 scorePercent、也不再有那个把 0.996 压成 99 的算法。
    expect(view).not.toMatch(/scorePercent/)
  })

  it('只报三条次数（一次通过 / 被驳回 / 实拍），且没有百分率；驳回原因一行一条带次数', () => {
    // t59 按原型 05 收敛：主数字（百分率）已删，判据改成「三条计数在、百分号不在」。
    expect(view).not.toMatch(/class="score-rate"/)
    // t63（t60 的 G2）：**源码级**断言 —— 只断渲染出来的 class 属性永远抓不到死 CSS。
    // 这条盯着样式表里那两条 `.score-rate{…}` 规则本身，删掉了也要防止被写回来。
    expect(view).not.toMatch(/\.score-rate\s*\{/)
    expect(view).toMatch(/class="score-split"/)
    expect(view).not.toContain('<span>%</span>')
    expect(view).toContain('一次通过')
    expect(view).toContain('被驳回')
    expect(view).toMatch(/<dt>实拍<\/dt>/)
    expect(view).toMatch(/v-for="item in score\.reasons"/)
    expect(view).toMatch(/\{\{ item\.count \}\} 次/)
  })

  it('跟卫生那几块一样自己一个 try：读不出来只影响这张卡，并且能重试', () => {
    expect(view).toMatch(/async function loadScore\(quiet = false\)/)
    expect(view).toMatch(/loadScore\(\)/)
    expect(view).toMatch(/loadScore\(true\)/)
    // 窗口与口径写清是"近 N 天"、跟红黑榜同一份记录。
    expect(view).toContain('近 {{ score.days }} 天')
    expect(view).toContain('次数口径，与红黑榜同一份记录')
  })

  it('验收发生在店长那一侧：跟着卫生 nudge 重读一次', () => {
    expect(view).toMatch(/topics: \['scheduling', 'hygiene'\]/)
    const pull = view.slice(view.indexOf('useNudgePull({'), view.indexOf('</script>'))
    expect(pull).toContain('loadScore(true)')
  })
})

describe('两个弹层的点击账约束（DO:250-251 / DO:909-914 / DO:860-869 · t67 钉住）', () => {
  it('换班：同意 1 击（直接进确认态），拒绝 2 击（保留确认框，DO:250-251）', () => {
    // 同意不弹确认框：按钮直接调 `answerSwap(card, true)`
    expect(view).toMatch(/@click="answerSwap\(card, true\)"/)
    // 拒绝要先落进确认态（`answerTarget`），所以是 2 击
    expect(view).toMatch(/@click="answerTarget = \{ card, agree: false \}"/)
  })

  it('换班：同意按钮写「同意并转店长」（DO:925）', () => {
    expect(view).toContain('>同意并转店长</button>')
    expect(view).toContain('点「同意并转店长」才会轮到店长批')
    expect(view).not.toContain('>同意</button>')
  })

  it('请假与换班：点击账不变，落点搬到月历那一天（票 15）', () => {
    // 一天假：点「请假」→ 月历上那一天的表单已经开着（日期就是这一天）→ 提交，还是两步。
    // 表单与判据整块住在 `components/scheduling/StaffDaySheet.vue`（那边有真挂载用例：
    // 起始日预填、提交上去的 body、事由上限、min-date 全在），这一页只把人送过去 ——
    // 不在这里再摆一套表单，免得同一件事两处判据各写一份。
    expect(view).toMatch(/key: 'leave', label: '请假', to: \{ \.\.\.month, query: \{ date, act: 'leave' \} \}/)
    expect(view).not.toContain('leave-sheet-title')
  })
})

describe('员工端请假（票 08）', () => {
  it('今天页只留「看状态」与「撤回」两条，发起那条搬去了月历那一天', () => {
    // 看/撤：`GET /api/scheduling/me/requests`、`DELETE .../<申请号>`。
    // 都只认员工 cookie，页面上没有 employee_id 可填。**提一条不再从这一页发**（票 15）：
    // `POST` 跟着表单搬进 `StaffDaySheet`（那边的挂载用例断言了提交上去的 body）。
    expect(view).toMatch(/staffRequest\('\/api\/scheduling\/me\/requests'\)/)
    expect(view).toMatch(/staffRequest\(`\/api\/scheduling\/me\/requests\/\$\{request\.id\}`/)
    expect(view).toMatch(/method: 'DELETE'/)
    expect(view).not.toContain('/api/scheduling/me/colleagues')
    // 谁的身份由 cookie 定：管理端那几条 `/api/scheduling/inbox*` 不许出现在员工页上；
    // 请求路径里也不许出现 employee_id（只有注释里那句「没有 employee_id 可填」）。
    expect(view).not.toContain('/api/scheduling/inbox')
    // 判的是「有没有在填这个字段」，不是「有没有提到这个词」——头注释里就写着
    // 「接口上没有 `employee_id` 可填」这句口径；`peer_employee_id`（票 09 的换班）
    // 前面多一个下划线，落不进这条正则。
    expect(view).not.toMatch(/(^|[^_a-zA-Z])employee_id\s*[:=]/)
  })

  it('那两颗按钮带着日期与动作跳到月历那一天（票 15）', () => {
    // 带过去的日期取**服务端的营业日**（`days[0].business_date`），不取手机上的今天：
    // 手机时区可能不在东八区，而「过去的日子请不了假」是服务端按营业日判的。
    expect(view).toMatch(/const date = \(today\.value && today\.value\.business_date\) \|\| ''/)
    expect(view).toMatch(/key: 'swap', label: '换班', to: \{ \.\.\.month, query: \{ date, act: 'swap' \} \}/)
    expect(view).toMatch(/router\.push\(entry\.to\)/)
    // 表单那一套不在这页：日期控件、事由上限、提交按钮全在 StaffDaySheet 里。
    expect(view).not.toContain('day-leave-start')
  })

  it('撤回之后重读一次列表，店长那边先动过也不会留一条假记录', () => {
    expect(view).toMatch(/note\.value = '撤回了'/)
    expect(view).toMatch(/await loadRequests\(true\)/)
    // 进页面那一次读不出来不吭声（管理员还没应用 0008 时，首页不该顶一行红字）。
    expect(view).toMatch(/if \(!quiet\) requestsError\.value = err\.message/)
  })

  it('撤回/回应的失败提示留到重读之后再说，不被重读抹掉', () => {
    // `loadRequests` 成功时会把 `requestsError` 清空（那是给「读不出来」用的），
    // 所以「没撤成 / 没回成」那句话必须等重读完了再写：先写后读等于当场抹掉 ——
    // 员工点了撤回看到界面什么都没变，会以为撤回了（真出过这个 bug）。
    for (const [start, end] of [
      ['async function cancelLeave', 'async function answerSwap'],
      ['async function answerSwap', 'onMounted('],
    ]) {
      const body = view.slice(view.indexOf(start), view.indexOf(end))
      const reread = body.indexOf('await loadRequests(true)')
      const message = body.indexOf('requestsError.value = failure')
      expect(reread, start).toBeGreaterThan(-1)
      expect(message, start).toBeGreaterThan(-1)
      expect(reread, start).toBeLessThan(message)
    }
  })

  it('撤回与拒绝先过确认框，手机上一误触不会直接生效', () => {
    // 这两个动作服务端只往前走（撤回后要重提、拒绝后这件事就结束），点错没有回头路。
    // 同仓库对不可逆动作一律弹确认框（店长驳回、删班次、卫生端那几处），员工端这两个
    // 是全仓少见的裸动作。「同意并转店长」不弹：后面还有店长那道闸，跟店长端「批准不弹、
    // 驳回弹」同一个口径 —— 三个都弹就成了每次都拦一道。
    expect(view).toMatch(/<ConfirmDialog/)
    expect(view).toMatch(/@click="cancelTarget = request"/)
    expect(view).toMatch(/@click="answerTarget = \{ card, agree: false \}"/)
    expect(view).toMatch(/@click="answerSwap\(card, true\)"/)
    expect(view).toMatch(/@confirm="confirmCancel"/)
    expect(view).toMatch(/@confirm="confirmAnswer"/)
    // 框里说清后果，不是一句「确定吗」。
    expect(view).toContain('要重新提一次')
    expect(view).toContain('对方会看到')
  })

  it('申请列表没读出来时不留一个静默的空页', () => {
    // 换班那张卡是「有才显示」的：读失败时它整块不在，员工只会以为没人找他换班，
    // 而页面上唯一能重来的动作是整页刷新。给一句能点的话。
    expect(view).toMatch(/requestsUnread/)
    expect(view).toMatch(/v-if="requestsUnread"/)
    expect(view).toMatch(/@click="loadRequests\(\)"/)
    expect(view).toContain('没读出来')
  })

  it('撤回 / 同意 / 拒绝那一排放大到点得中', () => {
    // 32px 在手机上太小 —— 挨着的是不可逆动作（有确认框兜着，但手指先得点得中）。
    // 「过去的日子在控件上先挡住」那条（`:min-date` 取服务端营业日）跟着日期控件
    // 搬进了 `StaffDaySheet`，那边的断言在 `components/scheduling/__tests__/`。
    expect(view).toMatch(/\.tL-cancel \{[\s\S]{0,260}?min-height: 40px/)
  })

  it('自己那条申请在页面上看得见、能撤回，批了才写成请假', () => {
    // 验收 4：员工看得见每条申请到哪一步（等店长批 / 批了 / 驳回了 / 已撤回）。
    expect(view).toMatch(/requestLine\(request\)/)
    expect(view).toMatch(/v-if="canCancel\(request\)"/)
    // 按钮先开确认框（`cancelTarget`），真发那条 DELETE 在 `confirmCancel` 里。
    expect(view).toMatch(/@click="cancelTarget = request"/)
    expect(view).toContain('请假批了那天写')
    expect(view).toContain('换班要对方先同意、店长再批')
    // 出错了要说出来（不吞）：这一页现在只有 `requestsError` 一处（表单那两处
    // `leaveError` / `swapError` 跟着表单搬去了 StaffDaySheet）。
    expect(view).toMatch(/v-if="requestsError"/)
    expect(view).not.toContain('leaveError')
  })

  it('三颗按钮都往月历走，请假与换班各带一个 act（票 15）', () => {
    // 票 06 时三个入口点进去都是「还没开放」；票 08 接上请假、票 09 接上换班、票 15 把
    // 这两件事收进月历那一天。`open()` 里最后那句兜底仍在（将来再加一颗没配 `to` 的按钮时，
    // 它说的是真话，而不是静默什么都不做）。
    expect(view).toMatch(/key: 'month', label: '整月', to: month/)
    expect(view).toMatch(/const ENTRIES = computed/)
    expect(view).toMatch(/note\.value = `「\$\{entry\.label\}」还没开放`/)
    expect(copy).toContain('今天请假')
    expect(copy).toContain("if (day.leave) return '请假'")
  })
})

describe('员工端换班（票 09）', () => {
  it('今天页只剩「替对方同意 / 拒绝」；提一条搬去了月历那一天', () => {
    expect(view).toMatch(/staffRequest\(`\/api\/scheduling\/me\/swaps\/\$\{card\.id\}\/\$\{agree \? 'accept' : 'reject'\}`/)
    expect(view).toMatch(/method: 'POST'/)
    // 身份由 cookie 定：路径上只有申请号，没有 employee_id。
    // `peer_employee_id`（票 09 的换班）不算：那是同事的 id，不是「我的 id」。
    expect(view).not.toMatch(/(^|[^_a-zA-Z])employee_id\s*[:=]/)
    // 发起那两条（同事名单 + 提一条）搬进了 StaffDaySheet：它的挂载用例断言了
    // 提交上去的 `peer_employee_id` 是那天名单里点中的那个人。
    expect(view).not.toContain('/api/scheduling/me/colleagues')
    expect(view).not.toContain('peer_employee_id')
  })

  it('别人问我换班的那几条看得见、点得动，店长那边在此之前看不见', () => {
    expect(view).toMatch(/v-if="incoming\.length"/)
    expect(view).toMatch(/incomingLine\(card\)/)
    expect(view).toMatch(/@click="answerSwap\(card, true\)"/)
    // 拒绝先开确认框（同意不弹，见确认框那条用例）。
    expect(view).toMatch(/@click="answerTarget = \{ card, agree: false \}"/)
    expect(view).toContain('点「同意并转店长」才会轮到店长批')
    // 同意/拒绝之后两边都要重读（对方可能自己撤了），也各有一句回执。
    expect(view).toMatch(/note\.value = agree \? '你同意了，接下来等店长批' : '你拒绝了，这件事到此为止'/)
    // 那次重读要钉在 `answerSwap` 里：`cancelLeave` 里也有一句 `await loadRequests(true)`，
    // 全文匹配会被它顶替（删掉这里的重读，断言照样绿）。
    const answer = view.slice(view.indexOf('async function answerSwap'), view.indexOf('onMounted('))
    expect(answer).toContain('await loadRequests(true)')
  })

  it('换班那条与我提的那些分开：暖色卡 + 自己的 tag', () => {
    expect(view).toContain('tA-card swap')
    expect(view).toMatch(/\.tA-card\.swap \{[\s\S]{0,80}--hy-amber/)
    expect(view).toMatch(/\.tag\.swap \{/)
  })

  it('订阅排班与卫生 nudge：这块几件事一起重读（票 10 收尾）', () => {
    // 换班那条尤其要紧：对方不开页面就永远不知道有人找他换 —— 这也是这几个页面里
    // 唯一「别人动作直接影响我」的地方。今天要交的卫生那一块搬去工作台首页之后
    // 有了自己的订阅（`StaffHygieneCard`），这一页的 pull 里只剩自己那三件。
    expect(view).toMatch(/useNudgePull\(\{/)
    expect(view).toMatch(/topics: \['scheduling', 'hygiene'\]/)
    expect(view).toMatch(/load\(true\)/)
    expect(view).toMatch(/loadRequests\(true\)/)
    expect(view).toMatch(/loadHygiene\(\)/)
    expect(view).not.toMatch(/loadHygiene\(true\)/)
  })
})

describe('仪容仪表（票 12）不再是这一页的事', () => {
  it('整块随卫生卡搬去了工作台首页，这一页一条都不留', () => {
    // 断言整组随块搬到 `components/staff/__tests__/staffHygieneCard.test.js`：
    // 「读员工自己那一条」「排到班次才显示」「live=true 进队列」「ADR 0050 标准图那一屏
    // 没有取景框」「已通过不给重拍」「这一行自己的字号与色调」六条在那份测试里逐字保留。
    expect(view).not.toMatch(/attire/)
    expect(view).not.toMatch(/HygieneLiveCamera/)
    expect(view).not.toMatch(/HygieneStandardOverlay/)
  })
})

describe('账号设置（2026-10-05 从卫生页的「我」整格搬来）', () => {
  it('三行入口都在这一页尾部，卫生页里一个都不留', () => {
    const home = readFileSync(join(here, '../../hygiene/HygieneHomeView.vue'), 'utf8')
    for (const label of ['修改个人信息', '修改密码', '重新选择区域和班次']) {
      expect(view).toContain(label)
      expect(home).not.toContain(`>${label}<`)
    }
    // 资料那一段（原来「我」面板里的那张表）也一起搬来了。
    expect(view).toMatch(/class="me-meta"/)
    // 票 01 起档位标签**不在这里**了：`permissionLine` 那一行撤掉，能力（开了哪几项、
    // 每项干什么、**还缺哪一项**）由页首那张「你能做的事」卡承载 —— 同一件事不说两遍
    // （真机走查 S10 的病根：标签写着「管理员」而一项开关都没给时，这里原来只显示
    // 「管理员」，人会以为自己有权限却什么都做不了）。
    // 卡自己的行为（逐条渲染 / 没开的写「店长还没开给你」/ 没权限整块不渲染）在
    // `staffCapabilityCard.test.js` 里**真挂一遍页**压，这里只钉"它搬走了"。
    // 这一条不读模板串断那张卡的 CSS 类名：spec 的 Testing Decisions 明写只断言外部行为，
    // 卡的形态归它自己那两处真挂载的用例管。
    expect(view).not.toMatch(/<dt>卫生权限<\/dt>/)
    expect(view).not.toMatch(/\{\{ permissionLine \}\}/)
  })

  it('两条写请求走员工自己的门，读的还是 /staff/me 那一份（不额外发请求）', () => {
    // 改资料 / 改密码：`PATCH /api/hygiene/staff/me`、`PATCH /api/hygiene/staff/password`。
    expect(view).toMatch(/staffRequest\('\/api\/hygiene\/staff\/me', \{[\s\S]{0,80}?method: 'PATCH'/)
    expect(view).toMatch(/staffRequest\('\/api\/hygiene\/staff\/password'/)
    // 资料那份 employee 在 `loadHygiene` 那一条 `/staff/me` 里顺手存下，路径上没有 id。
    expect(view).toMatch(/staffMe\.value = me\.employee \|\| \{\}/)
    expect(view).not.toMatch(/\/api\/hygiene\/admin/)
  })

  it('改手机号要先确认：它是登录账号，打错一位下次就登不进来', () => {
    // 校验与确认框都是从卫生页照搬的：正则、`phoneChanged`、`confirm-label="确认改号"`。
    expect(view).toMatch(/const PHONE_PATTERN = \/\^1\[3-9\]\\d\{9\}\$\//)
    expect(view).toMatch(/function phoneChanged\(\)/)
    expect(view).toMatch(/if \(phoneChanged\(\) && !profilePhoneConfirmOpen\.value\)/)
    expect(view).toMatch(/title="确认改手机号"/)
    expect(view).toMatch(/confirm-label="确认改号"/)
    // 两次新密码不一致就地拦住，不发请求。
    expect(view).toMatch(/if \(newPassword\.value !== confirmPassword\.value\)/)
  })

  it('「重新选择区域和班次」不自选：说清由排班决定，并把人送回顶上那张排班卡', () => {
    // 票 10 撤掉了员工自选班次/工作区那条路，搬过来也不许偷偷装回来。
    expect(view).toMatch(/function openShiftNotice\(\)/)
    // 那句话随票 15 更新（请假 / 换班的入口搬到了月历那一天），口径不变：
    // 区域与班次不在这里自选，要改是排班页（超级管理员）的事。
    expect(view).toMatch(/今天上哪个班、在哪个区由排班决定/)
    expect(view).toMatch(/class="tA-sub sched-note"/)
    expect(view).not.toMatch(/api\/hygiene\/staff\/assignment/)
    expect(view).not.toMatch(/HYGIENE_SHIFTS/)
  })
})
