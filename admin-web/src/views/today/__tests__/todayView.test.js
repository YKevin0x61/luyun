import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const view = readFileSync(join(here, '../TodayView.vue'), 'utf8')
const copy = readFileSync(join(here, '../../../utils/todayShift.js'), 'utf8')
const router = readFileSync(join(here, '../../../router/index.js'), 'utf8')
const login = readFileSync(join(here, '../../LoginView.vue'), 'utf8')
const mainPy = readFileSync(join(here, '../../../../../main.py'), 'utf8')

const month = readFileSync(join(here, '../TodayMonthView.vue'), 'utf8')
const pageRoutes = readFileSync(join(here, '../../../router/pageRoutes.json'), 'utf8')

/**
 * 员工端「我的」页（信息架构重排第二层 · 2026-10-19）。
 *
 * 这一页从「我的今天」收窄成「我的」：工作台首页的员工档回答「我今天在哪」（我的班 +
 * 请假/换班入口），所以「我的班」整块、请假/换班/整月三颗按钮都从这里删掉，只留页头
 * 右手「整月 ›」。下面这些断言跟着结构走：
 *   · 旧「原型 A 骨架 / 五态大字 / 往后三天 / 三个入口」那几条**整体下线** —— 它们断的
 *     是不再存在于本页的块（班次与未来三天归工作台首页/整月）；判据本身的真单测在
 *     `utils/__tests__/todayShift.test.js`，没有丢。
 *   · 新断言盯：标题与路由标题、页头「整月 ›」、四段顺序、`?act` 转发仍在、接口面没扩。
 */
describe('员工端「我的」页（IA 第二层）', () => {
  it('标题就是「我的」：页头、document.title、页面清单三处同一个词', () => {
    // 用户裁定：这一页不再承担「今天」——与工作台首页的员工档（也叫「今天」）撞名。
    expect(view).toMatch(/class="kicker">我的</)
    expect(view).toMatch(/class="tDay">我的</)
    expect(view).toContain("workbenchDocumentTitle('我的')")
    // 页面清单里这一行的 title 也跟着改（底栏「我的」那一格点进来时页头/标签一致）。
    expect(pageRoutes).toMatch(/"path": "\/workbench\/staff\/today",\s*"title": "我的"/)
    // 旧的「我的今天」不许再从这一页或清单里冒出来。
    expect(view).not.toContain('我的今天')
    expect(pageRoutes).not.toContain('"title": "我的今天"')
  })

  it('「我的班」块整块下线；「整月」只留页头右手一个入口', () => {
    // 班次大字 / 副行 / 请假·换班·整月三颗按钮都归工作台首页的员工档（上一批加的入口）。
    for (const gone of [
      /class="tA-card sched"/,
      /class="strip"/,
      /class="strip-name"/,
      /const ENTRIES = computed/,
      /function open\(entry\)/,
      /还没开放/,
    ]) {
      expect(view, String(gone)).not.toMatch(gone)
    }
    // 「整月」没有丢：页头右手那颗链接（不是旧的三颗按钮之一）。
    expect(view).toMatch(/class="tTop-link" to="\/workbench\/staff\/month">整月 ›</)
    // 请假 / 换班不再由这一页发起（只有首页那两个直达入口 + 月历操作单）。
    expect(view).not.toMatch(/@click="open\(entry\)"/)
  })

  it('内容按「我的」重排：待你回应 → 成绩 → 我的资料 → 你能做的事', () => {
    const order = [
      'class="group-h">待你回应<',
      'class="tA-card score"',
      'class="tA-card me"',
      'class="tA-card caps"',
    ].map((needle) => {
      const at = view.indexOf(needle)
      expect(at, needle).toBeGreaterThan(-1)
      return at
    })
    expect(order, '四段顺序变了').toEqual([...order].sort((a, b) => a - b))
  })

  it('首页「请假 / 换班」直达的转发逻辑留在这一页（?act=leave|swap&date=）', () => {
    // 首页员工档那两个入口落在 `/workbench/staff/today?act=...&date=<营业日>`；这一页
    // 没有表单，只负责把它转进月历并打开那一天 —— 这条链不能因为本页改版而断。
    expect(view).toMatch(/function forwardDayAction\(\)/)
    expect(view).toMatch(/actFromQuery\(route\.query\)/)
    expect(view).toMatch(/dateFromQuery\(route\.query\)/)
    expect(view).toMatch(/path: '\/workbench\/staff\/month'/)
    expect(view).toMatch(/router\.replace\(\{/)
    expect(view).toMatch(/if \(forwardDayAction\(\)\) return/)
  })

  it('不再拉「我的班」那条排班请求：接口面只剩自己的申请 / 资料 / 成绩', () => {
    // 这一页不再回答「今天上不上班」，`/api/scheduling/me` 那条不该再发。
    expect(view).not.toMatch(/staffRequest\('\/api\/scheduling\/me'\)/)
    // 自己那三扇门（申请 / 资料 / 成绩）都在，且只认员工自己的 cookie。
    expect(view).toMatch(/staffRequest\('\/api\/scheduling\/me\/requests'\)/)
    expect(view).toMatch(/staffRequest\('\/api\/hygiene\/staff\/me'\)/)
    expect(view).toMatch(/staffRequest\(`\/api\/hygiene\/staff\/me\/stats\?days=\$\{SCORE_DAYS\}`\)/)
    expect(view).not.toMatch(/\/api\/hygiene\/admin/)
    expect(view).not.toMatch(/\/api\/scheduling\/[^'"\s]*employee_id/)
    // 401 仍回员工登录（票 12 收在 `loginRedirectTarget` 一处）。
    expect(view).toMatch(/import \{ loginRedirectTarget \} from '\.\.\/\.\.\/utils\/loginNext'/)
    expect(view).toMatch(/loginRedirectTarget\(router\.currentRoute\.value\)/)
  })

  it('卫生块不再挂在这一页（它归工作台首页），两条卫生接口这里一条都不发', () => {
    expect(view).not.toMatch(/StaffHygieneCard/)
    expect(view).not.toMatch(/staffRequest\('\/api\/hygiene\/staff\/daily-work'\)/)
    expect(view).not.toMatch(/staffRequest\('\/api\/hygiene\/staff\/attire'\)/)
  })

  it('is registered as a staff page on both sides', () => {
    // SPA 页面要登记在：vue-router、main.py 的 SPA_PAGE_ROUTES（直连/反代硬导航）。
    expect(router).toMatch(/workbenchStaffPage\('\/workbench\/staff\/today', 'today'/)
    expect(router).toMatch(/views\/today\/TodayView\.vue/)
    expect(router).toMatch(/function workbenchStaffPage\(path, name, loader\)[\s\S]{0,300}?pageMeta\(path\)/)
    expect(router).not.toMatch(/staffAuth/)
    expect(mainPy).toMatch(/SPA_PAGE_ROUTES = \([\s\S]*?"\/workbench\/staff\/today"/)
    expect(mainPy).not.toMatch(/HTML_AUTH_EXACT = \{[^}]*"\/staff"/)
    expect(mainPy).not.toMatch(/HTML_AUTH_PREFIXES = \([\s\S]*?"\/staff\/"/)
    expect(router).not.toMatch(/path: '\/staff/)
    expect(router).not.toMatch(/path: '\/today'/)
    expect(router).not.toMatch(/path: '\/today\/month'/)
    expect(router).not.toMatch(/path: '\/hygiene'/)
    expect(router).not.toMatch(/'\/workbench\/login'/)
    expect(router).toMatch(/path: '\/login', query: \{ next: buildLoginNextFromRoute\(to\) \}/)
    // 整月页的返回按钮跟着改叫「我的」（这一格底栏标签也是「我的」）。
    expect(month).toMatch(/@click="router\.push\('\/workbench\/staff\/today'\)">‹ 我的</)
  })

  it('does not wait for the session probe before showing the page', () => {
    // 三页都从同一个工厂拿 `staffProbe: false`：弱网下不先白等一次探针超时。
    expect(router).toMatch(/function workbenchStaffPage\(path, name, loader\)[\s\S]{0,300}?staffProbe: false/)
    expect(router).toMatch(/if \(to\.meta\.staffProbe === false\) return true/)
  })

  it('drops the employee on the staff landing page after logging in', () => {
    // 验收 1：登录后落到员工首页。判据不在登录页里手写（那份比 util 弱，放松了没人拦），
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
    // IA 第二层后这一页没有发起按钮（`ENTRIES` 已删）：表单只有月历那一处这条契约
    // 由首页「请假」入口 → 本页 `?act` 转发 → 月历操作单这条链保证（断言见上面那组）。
    expect(view).not.toMatch(/const ENTRIES = computed/)
    expect(view).toMatch(/function forwardDayAction\(\)/)
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

  it('这一页不再发起请假 / 换班：只留「我的申请」的看状态与撤回（票 15 / IA 第二层）', () => {
    // 请假与换班的发起只有月历那一天的操作单（`StaffDaySheet`）。首页的员工档给了两个
    // 直达入口，落到本页的 `?act=` 由 `forwardDayAction` 转进月历 —— 这一页自己不摆表单、
    // 也不再有三颗入口按钮（`ENTRIES` 已随「我的班」块下线）。
    expect(view).not.toMatch(/const ENTRIES = computed/)
    expect(view).not.toMatch(/key: 'swap', label: '换班'/)
    // 表单那一套不在这页：日期控件、事由上限、提交按钮全在 StaffDaySheet 里。
    expect(view).not.toContain('day-leave-start')
    // 撤回那条仍在这里（`DELETE .../<申请号>`）。
    expect(view).toMatch(/staffRequest\(`\/api\/scheduling\/me\/requests\/\$\{request\.id\}`/)
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
    // 纯视觉断言维护（2026-10-17 B 案「钢面 · 仪」）：触控下限从 40px 提到 44px，
    // 与员工端其它行对齐（行 ≥44px）；判据仍是「这一排点得中」，只是数变了。
    expect(view).toMatch(/\.tL-cancel \{[\s\S]{0,260}?min-height: 44px/)
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

  it('页头右手的「整月 ›」是这一页唯一去月历的入口（IA 第二层）', () => {
    // 「我的班」块删掉后，整月入口只剩页头这一颗；请假/换班的发起仍只有月历那一天，
    // 首页那两个直达入口靠本页的 `?act` 转发（断言在上面 IA 第二层那组里）。
    expect(view).toMatch(/class="tTop-link" to="\/workbench\/staff\/month">整月 ›</)
    expect(view).not.toMatch(/key: 'month', label: '整月', to: month/)
    // 五态翻译仍由 utils 负责（页面不再摆班次，但这张表还是全仓唯一一份）。
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
    // 纯视觉断言维护（2026-10-17 B 案「钢面 · 仪」）：暖色从兼容层 --hy-amber 换成
    // 语义层的 --warn（待批一档），判据仍是「暖色卡 + 与自己提的那张分开」。
    expect(view).toMatch(/\.tA-card\.swap \{[\s\S]{0,80}var\(--warn\)/)
    expect(view).toMatch(/\.tag\.swap \{/)
  })

  it('订阅排班与卫生 nudge：申请 / 资料 / 成绩三块一起重读（票 10 收尾）', () => {
    // 换班那条尤其要紧：对方不开页面就永远不知道有人找他换。这一页不再读「我的班」，
    // 所以 pull 里没有那条排班请求，只剩自己的三份数据。
    expect(view).toMatch(/useNudgePull\(\{/)
    expect(view).toMatch(/topics: \['scheduling', 'hygiene'\]/)
    expect(view).toMatch(/loadRequests\(true\)/)
    expect(view).toMatch(/loadHygiene\(\)/)
    expect(view).toMatch(/loadScore\(true\)/)
    expect(view).not.toMatch(/load\(true\)/)
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

  it('「重新选择区域和班次」不自选：说清由排班决定，并把人送到整月', () => {
    // 票 10 撤掉了员工自选班次/工作区那条路，搬过来也不许偷偷装回来。
    // IA 第二层后「我的班」块已删：这一行改成送到整月（那一天点开就是名单与工作区），
    // 口径不变 —— 区域与班次不在这里自选，要改是排班页（超级管理员）的事。
    expect(view).toMatch(/function openShiftNotice\(\)/)
    expect(view).toMatch(/router\.push\('\/workbench\/staff\/month'\)/)
    expect(view).toMatch(/今天上哪个班、在哪个区由排班决定/)
    expect(view).toMatch(/class="tA-sub sched-note"/)
    expect(view).not.toMatch(/api\/hygiene\/staff\/assignment/)
    expect(view).not.toMatch(/HYGIENE_SHIFTS/)
  })
})
