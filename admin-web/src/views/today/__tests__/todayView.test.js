import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const view = readFileSync(join(here, '../TodayView.vue'), 'utf8')
const copy = readFileSync(join(here, '../../../utils/todayShift.js'), 'utf8')
const router = readFileSync(join(here, '../../../router/index.js'), 'utf8')
const login = readFileSync(join(here, '../../hygiene/HygieneLoginView.vue'), 'utf8')
const mainPy = readFileSync(join(here, '../../../../../main.py'), 'utf8')

describe('员工端「今天」页（原型 A）', () => {
  it('reads its own endpoint and nothing else', () => {
    // 员工会话读的是排班自己那条门：路径写死 `/me`，不带模板参数 ——
    // 读谁由服务端按 cookie 定，页面上没有 employee_id 可以填。
    expect(view).toMatch(/staffRequest\('\/api\/scheduling\/me'\)/)
    // 路径里唯一带变量的是「撤回自己那条申请」的申请号（票 08）：
    // 归属还是服务端按 cookie 判（别人的申请撤回只会拿到 404）。
    expect(view).not.toMatch(/\/api\/scheduling\/[^'"\s]*employee_id/)
    expect(view).not.toMatch(/\/api\/hygiene/)
    // 401 回员工登录（同一个登录页），把当前地址整个带过去（跟卫生首页一个走法）。
    expect(view).toMatch(/path: '\/hygiene\/login'/)
    expect(view).toMatch(/next: router\.currentRoute\.value\.fullPath/)
  })

  it('keeps the chosen prototype A shape', () => {
    // 用户选的员工端首页 = today-variants.html 的 A · 两块：
    // 整屏 → 排班卡 → 往后三天 → 页脚（下半张卫生卡下一张票接）。
    expect(view).toMatch(/useScopedStylesheet\('\/hygiene-admin\.css'\)/)
    expect(view).toMatch(/class="today-page hygiene-staff"/)
    for (const cls of ['tA-card sched', 'tA-hd', 'shift', 'acts', 'tA-h', 'next3', 'tA-foot']) {
      expect(view).toContain(cls)
    }
    expect(view).toMatch(/repeat\(3,\s*1fr\)/)
  })

  it('renders however many shifts the API returns', () => {
    // 班次可配置（票 11 会加「早班」之类）：页面上不写死白/夜两个名字，
    // 班次与责任区都从 `/me` 下来的那一行上取。
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
    // SPA 页面要登记在：vue-router、main.py 的 SPA_PAGE_ROUTES（直连/反代硬导航）、
    // 以及服务端的 HTML 鉴权豁免表（手机上没有管理端会话，拦在服务端就进不去）。
    // 带尾斜杠那条走 HTML_AUTH_PREFIXES（见 tests/test_auth.py 的员工端用例）。
    expect(router).toMatch(/path: '\/today', name: 'today'/)
    expect(router).toMatch(/views\/today\/TodayView\.vue/)
    expect(router).toMatch(/path: '\/today'[\s\S]{0,200}?staffAuth: true/)
    expect(mainPy).toMatch(/SPA_PAGE_ROUTES = \([\s\S]*?"\/today"/)
    expect(mainPy).toMatch(/HTML_AUTH_EXACT = \{[^}]*"\/today"/)
    expect(mainPy).toMatch(/HTML_AUTH_PREFIXES = \([\s\S]*?"\/today\/"/)
  })

  it('does not wait for the session probe before showing the shift', () => {
    // 这一页自己那次请求就分得清 401 与断网（`load()`），守卫那次探针是重复劳动：
    // 弱网下先白等一次超时（最长 4 秒）才轮到排班那条请求。
    expect(router).toMatch(/path: '\/today'[\s\S]{0,200}?staffProbe: false/)
    expect(router).toMatch(/if \(to\.meta\.staffProbe === false\) return true/)
  })

  it('drops the employee on the today page after logging in', () => {
    // 验收 1：登录后落到这一页。判据不在登录页里手写（那份比 util 弱，放松了没人拦），
    // 而在 `utils/loginNext.js` 的 `resolveStaffNext` —— 真单测在 loginNext.test.js。
    expect(login).toMatch(/resolveStaffNext\(route\.query\.next\)/)
    expect(login).not.toContain('hygiene|today')
  })
})

describe('员工端请假（票 08）', () => {
  it('三条请求都走员工门，路径上只有自己的申请号', () => {
    // 提/看/撤：`GET|POST /api/scheduling/me/requests`、`DELETE .../<申请号>`。
    // 都只认员工 cookie，页面上没有 employee_id 可填。
    expect(view).toMatch(/staffRequest\('\/api\/scheduling\/me\/requests'\)/)
    expect(view).toMatch(/staffRequest\(`\/api\/scheduling\/me\/requests\/\$\{request\.id\}`/)
    expect(view).toMatch(/method: 'POST'/)
    expect(view).toMatch(/method: 'DELETE'/)
    // 谁的身份由 cookie 定：管理端那几条 `/api/scheduling/inbox*` 不许出现在员工页上；
    // 请求路径里也不许出现 employee_id（只有注释里那句「没有 employee_id 可填」）。
    expect(view).not.toContain('/api/scheduling/inbox')
    // 判的是「有没有在填这个字段」，不是「有没有提到这个词」——头注释里就写着
    // 「接口上没有 `employee_id` 可填」这句口径；`peer_employee_id`（票 09 的换班）
    // 前面多一个下划线，落不进这条正则。
    expect(view).not.toMatch(/(^|[^_a-zA-Z])employee_id\s*[:=]/)
  })

  it('表单里那两天默认是服务端给的营业日，不是手机上的今天', () => {
    // 手机时区可能不在东八区，而「过去的日子请不了假」是服务端按营业日判的。
    expect(view).toMatch(/leaveStart\.value = \(today\.value && today\.value\.business_date\) \|\| ''/)
    // 只请一天时后一格空着就不发那个字段（服务端把 None 与空串都当单日）。
    expect(view).toMatch(/end_date: leaveEnd\.value \|\| null/)
    // 事由最多 50 字：跟前端的 maxlength 与服务端的 MAX_REQUEST_NOTE 是同一个数。
    expect(view).toMatch(/maxlength="50"/)
    expect(view).toMatch(/note: leaveNote\.value \|\| null/)
  })

  it('提完/撤回都重读一次列表，店长那边先动过也不会留一条假记录', () => {
    expect(view).toMatch(/note\.value = '请假提上去了，等店长批'/)
    expect(view).toMatch(/await loadRequests\(\)/)
    expect(view).toMatch(/await loadRequests\(true\)/)
    // 进页面那一次读不出来不吭声（管理员还没应用 0008 时，首页不该顶一行红字）。
    expect(view).toMatch(/if \(!quiet\) requestsError\.value = err\.message/)
  })

  it('自己那条申请在页面上看得见、能撤回，批了才写成请假', () => {
    // 验收 4：员工看得见每条申请到哪一步（等店长批 / 批了 / 驳回了 / 已撤回）。
    expect(view).toMatch(/requestLine\(request\)/)
    expect(view).toMatch(/v-if="canCancel\(request\)"/)
    expect(view).toMatch(/@click="cancelLeave\(request\)"/)
    expect(view).toContain('请假批了那天写')
    expect(view).toContain('换班要对方先同意、店长再批')
    // 出错了要说出来（不吞）：钉住那一处 —— 只写 `role="alert"` 的话，将来别处再加一个
    // 无障碍标记，这里就退化成恒真了。
    expect(view).toMatch(/v-if="leaveError"[\s\S]{0,40}role="alert"/)
  })

  it('「换班」也不是那句「还没开放」了，请假与换班各走各的表单', () => {
    // 票 06 时三个入口点进去都是「还没开放」；票 08 把请假接上了、票 09 把换班接上了 ——
    // 这三句得分开走，别把哪个入口一块写成开放（`open()` 里最后那句仍是兜底）。
    expect(view).toMatch(/if \(entry\.key === 'leave'\) \{\s*openLeave\(\)/)
    expect(view).toMatch(/if \(entry\.key === 'swap'\) \{\s*openSwap\(\)/)
    expect(view).toMatch(/note\.value = `「\$\{entry\.label\}」还没开放`/)
    expect(copy).toContain('今天请假')
    expect(copy).toContain("if (day.leave) return '请假'")
  })
})

describe('员工端换班（票 09）', () => {
  it('四条请求都走员工门：名单、提一条、替对方同意 / 拒绝', () => {
    expect(view).toMatch(/staffRequest\('\/api\/scheduling\/me\/colleagues'\)/)
    expect(view).toMatch(/staffRequest\('\/api\/scheduling\/me\/swaps'/)
    expect(view).toMatch(/staffRequest\(`\/api\/scheduling\/me\/swaps\/\$\{card\.id\}\/\$\{agree \? 'accept' : 'reject'\}`/)
    // 身份由 cookie 定：路径上只有申请号，没有 employee_id。
    // `peer_employee_id`（票 09 的换班）不算：那是同事的 id，不是「我的 id」。
    expect(view).not.toMatch(/(^|[^_a-zA-Z])employee_id\s*[:=]/)
    // 表单里发的是「跟谁换」的同事 id，不是姓名（姓名可以重）。
    expect(view).toMatch(/peer_employee_id: Number\(swapPeer\.value\) \|\| null/)
  })

  it('别人问我换班的那几条看得见、点得动，店长那边在此之前看不见', () => {
    expect(view).toMatch(/v-if="incoming\.length"/)
    expect(view).toMatch(/incomingLine\(card\)/)
    expect(view).toMatch(/@click="answerSwap\(card, true\)"/)
    expect(view).toMatch(/@click="answerSwap\(card, false\)"/)
    expect(view).toContain('你点了同意才会轮到店长批')
    // 同意/拒绝之后两边都要重读（对方可能自己撤了），也各有一句回执。
    expect(view).toMatch(/note\.value = agree \? '你同意了，接下来等店长批' : '你拒绝了，这件事到此为止'/)
    // 那次重读要钉在 `answerSwap` 里：`cancelLeave` 里也有一句 `await loadRequests(true)`，
    // 全文匹配会被它顶替（删掉这里的重读，断言照样绿）。
    const answer = view.slice(view.indexOf('async function answerSwap'), view.indexOf('onMounted('))
    expect(answer).toContain('await loadRequests(true)')
  })

  it('换班表单：同事下拉 + 哪一天 + 事由，日期默认服务端营业日', () => {
    expect(view).toMatch(/swapDay\.value = \(today\.value && today\.value\.business_date\) \|\| ''/)
    expect(view).toMatch(/v-for="person in colleagues"/)
    expect(view).toMatch(/:value="person\.id"/)
    // 事由的长度上限钉在换班那个输入框上：`maxlength="50"` 请假表单里也有一个。
    const noteBox = view.slice(view.indexOf('id="swap-note"'))
    expect(noteBox.slice(0, 200)).toMatch(/maxlength="50"/)
    // 三样缺一样就不让提交（同事、日期）。
    expect(view).toMatch(/:disabled="swapBusy \|\| !swapPeer \|\| !swapDay"/)
    expect(view).toMatch(/body: \{\s*peer_employee_id: Number\(swapPeer\.value\) \|\| null,\s*business_date: swapDay\.value/)
    // 出错了要说出来，跟请假那一处一样的钉法。
    expect(view).toMatch(/v-if="swapError"[\s\S]{0,40}role="alert"/)
  })

  it('换班那条与我提的那些分开：暖色卡 + 自己的 tag', () => {
    expect(view).toContain('tA-card swap')
    expect(view).toMatch(/\.tA-card\.swap \{[\s\S]{0,80}--hy-amber/)
    expect(view).toMatch(/\.tag\.swap \{/)
  })
})
