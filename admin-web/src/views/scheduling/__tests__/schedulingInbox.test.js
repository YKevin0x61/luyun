import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const view = readFileSync(join(here, '../SchedulingInboxView.vue'), 'utf8')
const calendar = readFileSync(join(here, '../SchedulingCalendarView.vue'), 'utf8')
const copy = readFileSync(join(here, '../../../utils/leaveRequest.js'), 'utf8')
const tokens = readFileSync(join(here, '../../../../public/hygiene-admin.css'), 'utf8')
const router = readFileSync(join(here, '../../../router/index.js'), 'utf8')
const navBar = readFileSync(join(here, '../../../components/NavBar.vue'), 'utf8')
const mainPy = readFileSync(join(here, '../../../../../main.py'), 'utf8')

describe('店长端排班待办（票 08、09）', () => {
  it('借共享样式表的令牌，不进卫生模块', () => {
    // 跟月历同一套深青墨配色：令牌住在 public/hygiene-admin.css，但那是共享样式表，
    // 不是卫生模块（不 import 它的 Python、不挂它的菜单）。
    expect(view).toMatch(/useScopedStylesheet\('\/hygiene-admin\.css'\)/)
    expect(view).toMatch(/class="hygiene-admin inbox-page"/)
  })

  it('只读排班自己那三条待办路由', () => {
    // 店长门：待办列表 + 批准/驳回。身份由管理端 cookie 定，路径上只有申请号。
    expect(view).toMatch(/api\.get\('\/api\/scheduling\/inbox'\)/)
    expect(view).toMatch(/api\.post\(`\/api\/scheduling\/inbox\/\$\{request\.id\}\/\$\{action\}`, \{\}\)/)
    // 动作名只有服务端有路由的那两个。
    expect(view).toMatch(/decide\(request, 'approve'\)/)
    expect(view).toMatch(/decide\(target, 'reject'\)/)
    // 页面上真正的请求就这两条，都在店长那扇门里（员工门那几条只出现在头注释的说明里）。
    const calls = [...view.matchAll(/api\.(?:get|post|put|delete)\(\s*[`']([^`']*)/g)].map((m) => m[1])
    expect(calls.sort()).toEqual([
      '/api/scheduling/inbox',
      '/api/scheduling/inbox/${request.id}/${action}',
    ])
    expect(view).not.toMatch(/\/api\/hygiene/)
  })

  it('先把结果摊开再让人按（验收 2）', () => {
    // 批了之后那天每个班次还剩几个人：一行一天，句子的判据在 util 里
    // （leaveRequest.test.js 钉着「过去 / 没排 / 正常」三种说法）。
    expect(view).toMatch(/v-for="day in request\.days"/)
    expect(view).toMatch(/:class="\{ past: day\.past, none: !day\.scheduled \}"/)
    // 请假与换班一句话不同：请假说「批了之后剩几个人」，换班说「两个人的班怎么对调」。
    expect(view).toMatch(/previewLine\(day\)/)
    expect(view).toMatch(/swapPreviewLine\(day, request\.employee_name, request\.peer_name\)/)
    // 事由与区间也一起摊开（店长不该点开才知道是谁请哪天）。
    expect(view).toMatch(/noteText\(request\.note\)/)
    expect(view).toMatch(/requestRangeText\(request\.start_date, request\.end_date\)/)
    // 回执把「批了」和「改了」分开说，不写一句笼统的「已批准」；两种申请各一句。
    expect(view).toMatch(/approveReceipt\(data\)/)
    expect(view).toMatch(/swapApproveReceipt\(data\)/)
    expect(copy).toContain('已经过去，排班没动')
    // 人手够不够只提示不拦：页面代码里没有「最少几个人」的闸（那句话只在头注释里
    // 说明口径，所以把模板切出来查）。先确认真的切到了模板 —— `indexOf` 返回 -1 时
    // `slice(-1)` 只剩最后一个字符，后面那条断言会恒真。
    expect(view.indexOf('<template>')).toBeGreaterThan(-1)
    const tpl = view.slice(view.indexOf('<template>'))
    expect(tpl).not.toMatch(/最少/)
  })

  it('换班卡跟请假卡同一张，只多一行「跟谁换」（票 09 验收 3/4）', () => {
    // 验收 3：对方没点同意之前这里根本不会有这张卡 —— 服务端只把 `pending_manager`
    // 的换班放进 `/inbox`，页面上不必再过滤一次，但卡片得说清是谁跟谁换。
    expect(view).toMatch(/<span v-if="request\.kind === 'swap'" class="iPeer">⇄ \{\{ request\.peer_name/)
    expect(view).toMatch(/kindText\(request\.kind\)/)
    // 两种申请共用一个计数与一处「等你批」，别为换班再抄一份列表。
    expect((view.match(/v-for="request in requests"/g) || []).length).toBe(1)
    expect(view).toContain('申请等着批')
    expect(view).toContain('换班要对方先点同意，才会轮到这儿')
    expect(view).toMatch(/\.iPeer \{[\s\S]{0,60}--hy-amber/)
  })

  it('驳回走确认框，框里点名是谁、驳的是哪一种', () => {
    expect(view).toMatch(/<ConfirmDialog/)
    // 标题与正文按种类说（请假的驳了是「继续按规则排」，换班的驳了是「那两个人不动」）。
    expect(view).toMatch(/:title="`驳回这次\$\{kindText\(rejectTarget\.kind\)\}`"/)
    expect(view).toMatch(/:message="`驳回「\$\{rejectTarget\.employee_name \|\| '这位员工'\}」的\$\{kindText\(rejectTarget\.kind\)\}后/)
    expect(view).toMatch(/rejectTarget\.employee_name/)
    expect(view).toMatch(/confirm-label="驳回"/)
    expect(view).toMatch(/danger/)
    // 批准不弹框（可逆：批错了再改那天）；两条动作都防连点。
    expect((view.match(/:disabled="busyId === request\.id"/g) || []).length).toBe(2)
  })

  it('点了以后重读待办：别人先批了/员工自己撤了都不留假记录', () => {
    // 成功与失败各重读一次（失败可能是 `request_not_pending`）；进页面那次是
    // `onMounted` 的 `load()`，不必 await。后端那句原话直接转出来。
    expect((view.match(/await load\(\)/g) || []).length).toBe(2)
    expect(view).toMatch(/errorText\.value = err\.message \|\| '没处理成'/)
    // 写进 ref 还不够：不渲染出来，批假失败就是静默的（页面上什么都不会变）。
    expect(view).toMatch(/v-if="errorText"[\s\S]{0,40}role="alert"/)
  })

  it('谁还没配规则分成两拨：在职的提醒，停用/没批准的不提醒', () => {
    // 口径与月历底下那根条一致（`needsRule`）：在职 = 批准过、没停用。
    expect(view).toMatch(/splitRuleless\(withoutRule\.value\)/)
    expect(view).toMatch(/v-for="person in ruleless\.active"/)
    expect(view).toMatch(/ruleless\.muted\.length/)
    expect(copy).toMatch(/person\.approved && !person\.disabled/)
  })

  it('用的每个令牌都在共享样式表里有定义', () => {
    const used = new Set([...view.matchAll(/var\((--[a-z0-9-]+)\)/g)].map((m) => m[1]))
    expect(used.size).toBeGreaterThan(10)
    const missing = [...used].filter((name) => !tokens.includes(`${name}:`))
    expect(missing).toEqual([])
  })

  it('在管理端有一扇自己的门（前端与后端都登记）', () => {
    expect(router).toMatch(/path: '\/scheduling\/inbox', name: 'scheduling-inbox'/)
    expect(router).toMatch(/views\/scheduling\/SchedulingInboxView\.vue/)
    // 管理端的门：不带 `HYGIENE_STAFF_META` 那套员工 meta（没有 staffAuth）。
    expect(router).not.toMatch(/scheduling-inbox[\s\S]{0,200}?staffAuth/)
    // 直连/反代硬导航那条路要认得这个地址（服务端 SPA 白名单）。
    expect(mainPy).toMatch(/SPA_PAGE_ROUTES = \([\s\S]*?"\/scheduling\/inbox"/)
    // 顶栏那条「排班」按前缀亮：进了待办页，导航上还在排班这一档。
    expect(navBar).toMatch(/prefix: '\/scheduling'/)
  })

  it('月历页上有一条路走得到这一页（不然没人知道有假要批）', () => {
    expect(calendar).toMatch(/class="gPend gTodo"/)
    expect(calendar).toMatch(/router\.push\('\/scheduling\/inbox'\)/)
    expect(calendar).toContain('请假待办')
    expect(view).toMatch(/router\.push\('\/scheduling'\)/)
  })
})
