import { describe, expect, it } from 'vitest'
import {
  buildLoginNextFromRoute,
  isRecipeReaderPath,
  resolveLoginNext,
  resolveLoginTab,
  resolveStaffNext,
  shouldSkipLoginRedirect,
} from '../loginNext.js'

describe('isRecipeReaderPath', () => {
  // 票 07：配方阅读面搬进工作台的「后勤」组，四条路径跟着换地址。
  it('岗位列表、详情、打印、二维码是阅读面（新地址）', () => {
    expect(isRecipeReaderPath('/workbench/kitchen/recipe')).toBe(true)
    expect(isRecipeReaderPath('/workbench/kitchen/recipe/detail')).toBe(true)
    expect(isRecipeReaderPath('/workbench/kitchen/recipe/print')).toBe(true)
    expect(isRecipeReaderPath('/workbench/kitchen/recipe/qr')).toBe(true)
  })

  it('配方管理不是阅读面（它只给管理端）', () => {
    expect(isRecipeReaderPath('/workbench/kitchen/recipe/manage')).toBe(false)
    expect(isRecipeReaderPath('/admin')).toBe(false)
  })

  it('旧的 /recipe* 不是阅读面了：搬走之后一条别名都不留', () => {
    for (const stale of ['/recipe', '/recipe/detail', '/recipe/print', '/recipe/qr']) {
      expect(isRecipeReaderPath(stale)).toBe(false)
    }
  })
})

describe('shouldSkipLoginRedirect', () => {
  it('登录页、配置页和配方阅读面不因 401 整页跳登录', () => {
    expect(shouldSkipLoginRedirect('/login')).toBe(true)
    expect(shouldSkipLoginRedirect('/settings')).toBe(true)
    expect(shouldSkipLoginRedirect('/workbench/kitchen/recipe/detail')).toBe(true)
  })

  it('旧配置页地址 /setup 改名后不再有豁免（不做别名）', () => {
    expect(shouldSkipLoginRedirect('/setup')).toBe(false)
  })

  it('旧配方地址不再有豁免（搬进工作台之后不留别名）', () => {
    expect(shouldSkipLoginRedirect('/recipe/detail')).toBe(false)
    expect(shouldSkipLoginRedirect('/recipe')).toBe(false)
  })

  it('员工手机端的页不因 401 跳后台登录（票 03 起在 /workbench/me/*）', () => {
    expect(shouldSkipLoginRedirect('/workbench/me/today')).toBe(true)
    expect(shouldSkipLoginRedirect('/workbench/me/today/')).toBe(true)
    expect(shouldSkipLoginRedirect('/workbench/me/month')).toBe(true)
    expect(shouldSkipLoginRedirect('/workbench/me/clean')).toBe(true)
    // 注册页搬到顶层 /register：它没有管理端会话，401 也不该整页跳 /login。
    expect(shouldSkipLoginRedirect('/register')).toBe(true)
  })

  it('旧员工路径已删除：401 不再给它们任何豁免（不做别名）', () => {
    expect(shouldSkipLoginRedirect('/today')).toBe(false)
    expect(shouldSkipLoginRedirect('/today/month')).toBe(false)
    expect(shouldSkipLoginRedirect('/hygiene')).toBe(false)
  })

  it('管理面和运营页仍跳登录', () => {
    expect(shouldSkipLoginRedirect('/workbench/kitchen/recipe/manage')).toBe(false)
    expect(shouldSkipLoginRedirect('/admin')).toBe(false)
    expect(shouldSkipLoginRedirect('/workbench/hr/roster')).toBe(false)
    expect(shouldSkipLoginRedirect('/workbench/floor/zones')).toBe(false)
    expect(shouldSkipLoginRedirect('/workbench/floor/daily')).toBe(false)
    expect(shouldSkipLoginRedirect('/workbench/floor/deep-clean')).toBe(false)
    expect(shouldSkipLoginRedirect('/workbench/floor/fix')).toBe(false)
    expect(shouldSkipLoginRedirect('/workbench/floor/boards')).toBe(false)
  })
})

describe('buildLoginNextFromRoute', () => {
  it('用已解码的 query 拼 next，slug 只编码一次', () => {
    const next = buildLoginNextFromRoute({
      path: '/workbench/kitchen/recipe/detail',
      query: { slug: '肠粉档' },
    })
    expect(next).toBe('/workbench/kitchen/recipe/detail?slug=%E8%82%A0%E7%B2%89%E6%A1%A3')
    expect(next).not.toContain('%25')
  })

  it('没有 query 时只返回 path', () => {
    expect(buildLoginNextFromRoute({ path: '/workbench/kitchen/recipe/manage', query: {} }))
      .toBe('/workbench/kitchen/recipe/manage')
  })
})

describe('resolveLoginNext', () => {
  it('解开误伤的双重编码 slug', () => {
    const raw = '/workbench/kitchen/recipe/detail?slug=%25E8%2582%25A0%25E7%25B2%2589%25E6%25A1%25A3'
    expect(resolveLoginNext(raw)).toBe('/workbench/kitchen/recipe/detail?slug=%E8%82%A0%E7%B2%89%E6%A1%A3')
  })

  it('拒绝开放重定向', () => {
    expect(resolveLoginNext('https://evil.example/phish')).toBe('/')
    expect(resolveLoginNext('//evil.example')).toBe('/')
    expect(resolveLoginNext('/login?next=/admin')).toBe('/')
  })

  it('空值回退', () => {
    expect(resolveLoginNext(null, '/workbench/kitchen/recipe')).toBe('/workbench/kitchen/recipe')
    expect(resolveLoginNext('', '/workbench/kitchen/recipe')).toBe('/workbench/kitchen/recipe')
  })

  it('管理员身份不认员工端落点（身份互斥）', () => {
    // 员工页那扇门只认员工 cookie：管理员被送进去也只会被客户端守卫弹回 /login。
    expect(resolveLoginNext('/workbench/me/today')).toBe('/')
    expect(resolveLoginNext('/workbench/me/today?day=2')).toBe('/')
    expect(resolveLoginNext('/workbench/me/month')).toBe('/')
    expect(resolveLoginNext('/workbench/me/clean')).toBe('/')
    expect(resolveLoginNext('/workbench/me/today', '/admin')).toBe('/admin')
  })

  it('管理员身份照旧放行任意站内管理路径（含 query 与配方阅读面）', () => {
    expect(resolveLoginNext('/workbench/hr/roster')).toBe('/workbench/hr/roster')
    expect(resolveLoginNext('/admin?tab=orders')).toBe('/admin?tab=orders')
    // 票 07：配方阅读面是 `both`，管理端登录后回那条配方也要放行（扫码的人里也有店长）。
    expect(resolveLoginNext('/workbench/kitchen/recipe/detail?slug=congee'))
      .toBe('/workbench/kitchen/recipe/detail?slug=congee')
    expect(resolveLoginNext('/workbench/kitchen/recipe')).toBe('/workbench/kitchen/recipe')
  })
})

describe('resolveStaffNext', () => {
  it('员工那三页原样放行（含子页面与 query）', () => {
    expect(resolveStaffNext('/workbench/me/today')).toBe('/workbench/me/today')
    expect(resolveStaffNext('/workbench/me/clean')).toBe('/workbench/me/clean')
    expect(resolveStaffNext('/workbench/me/month')).toBe('/workbench/me/month')
    expect(resolveStaffNext('/workbench/me/month?m=2026-09')).toBe('/workbench/me/month?m=2026-09')
    expect(resolveStaffNext('/workbench/me/today/')).toBe('/workbench/me/today/')
  })

  it('站外地址与协议相对地址一律回落到员工默认落点', () => {
    expect(resolveStaffNext('https://evil.example/phish')).toBe('/workbench/me/today')
    expect(resolveStaffNext('//evil.example')).toBe('/workbench/me/today')
    expect(resolveStaffNext('/\\evil.example')).toBe('/workbench/me/today')
    expect(resolveStaffNext('javascript:alert(1)')).toBe('/workbench/me/today')
  })

  it('管理端页面不算员工落点（连名字像的也不算）', () => {
    expect(resolveStaffNext('/admin')).toBe('/workbench/me/today')
    expect(resolveStaffNext('/workbench/hr/roster')).toBe('/workbench/me/today')
    expect(resolveStaffNext('/workbench/floor/zones')).toBe('/workbench/me/today')
    expect(resolveStaffNext('/workbench/mex')).toBe('/workbench/me/today')
    expect(resolveStaffNext('/login?next=/admin')).toBe('/workbench/me/today')
  })

  it('票 07：配方阅读面也是员工落点 —— 扫码的厨师登录后回到那条配方', () => {
    // 这是本票最容易漏的一处：只认「员工端落点」（`/workbench/me/*`）的话，厨师在员工栏
    // 登录、`?next=` 是配方阅读路径时会**静默落到 `/workbench/me/today`** ——
    // 「登录后回到那条配方」当场失效，而且没有任何报错。
    expect(resolveStaffNext('/workbench/kitchen/recipe'))
      .toBe('/workbench/kitchen/recipe')
    expect(resolveStaffNext('/workbench/kitchen/recipe/detail?slug=changfen'))
      .toBe('/workbench/kitchen/recipe/detail?slug=changfen')
    expect(resolveStaffNext('/workbench/kitchen/recipe/print?slug=changfen'))
      .toBe('/workbench/kitchen/recipe/print?slug=changfen')
    expect(resolveStaffNext('/workbench/kitchen/recipe/qr'))
      .toBe('/workbench/kitchen/recipe/qr')
    // 管理页不是员工落点（配方管理只给管理端），照旧回落到员工默认落点。
    expect(resolveStaffNext('/workbench/kitchen/recipe/manage'))
      .toBe('/workbench/me/today')
  })

  it('票 07：老地址 ?next=/recipe* 换成新地址之后才算员工落点', () => {
    expect(resolveStaffNext('/recipe/detail?slug=changfen'))
      .toBe('/workbench/kitchen/recipe/detail?slug=changfen')
    expect(resolveStaffNext('/recipe')).toBe('/workbench/kitchen/recipe')
  })

  it('旧员工路径不再是落点：搬走之后没留别名', () => {
    for (const stale of ['/today', '/today/month', '/hygiene']) {
      expect(resolveStaffNext(stale)).toBe('/workbench/me/today')
    }
  })

  it('注册页是员工侧界面，但不是登录后落点：?next=/register 回落到默认落点', () => {
    // 注册成功还在等超级管理员批准、会话也不存在 —— 把 /register 当落点就是死路。
    expect(resolveStaffNext('/register')).toBe('/workbench/me/today')
    expect(resolveStaffNext('/register?from=login')).toBe('/workbench/me/today')
  })

  it('默认落在今天页；也认调用方给的兜底', () => {
    expect(resolveStaffNext(null)).toBe('/workbench/me/today')
    expect(resolveStaffNext('')).toBe('/workbench/me/today')
    expect(resolveStaffNext(undefined)).toBe('/workbench/me/today')
    expect(resolveStaffNext(undefined, '/workbench/me/clean')).toBe('/workbench/me/clean')
    expect(resolveStaffNext('/admin', '/workbench/me/clean')).toBe('/workbench/me/clean')
  })

  it('数组取第一个（vue-router 的 query 可能是数组）', () => {
    expect(resolveStaffNext(['/workbench/me/today', '/admin'])).toBe('/workbench/me/today')
    expect(resolveStaffNext(['/admin', '/workbench/me/today'])).toBe('/workbench/me/today')
  })

  it('误伤的双重编码照旧解开', () => {
    expect(resolveStaffNext('/workbench/me/today?next=%252Fworkbench%252Fme%252Ftoday')).toBe('/workbench/me/today?next=%2Fworkbench%2Fme%2Ftoday')
  })
})

describe('resolveLoginTab（面板默认开在哪一栏）', () => {
  it('记住值优先；没有记住值时落在员工栏（员工手机一打开就是员工栏）', () => {
    expect(resolveLoginTab(null, 'admin')).toBe('admin')
    expect(resolveLoginTab(null, 'staff')).toBe('staff')
    expect(resolveLoginTab(null, null)).toBe('staff')
    expect(resolveLoginTab(null, '')).toBe('staff')
    expect(resolveLoginTab(null, 'boss')).toBe('staff')
  })

  it('?next 落在员工端前缀内时强制员工栏，优先于记住值', () => {
    expect(resolveLoginTab('/workbench/me/today', 'admin')).toBe('staff')
    expect(resolveLoginTab('/workbench/me/month?m=2026-09', 'admin')).toBe('staff')
    expect(resolveLoginTab('/workbench/me/clean', 'admin')).toBe('staff')
    // 误伤的双重编码照旧认得出（`%25` 在路径后面时解一层）。
    expect(resolveLoginTab('/workbench/me/today?next=%252Fworkbench%252Fme%252Ftoday', 'admin')).toBe('staff')
  })

  it('票 07：?next 是配方阅读路径时也强制员工栏（扫码的绝大多数是厨师）', () => {
    // 关键交互那条：「扫码 → 未登录 → 登录页**默认开员工栏**」。判据不是「路径前缀像员工端」
    // 而是「这条目标路径是一页配方阅读面」——前台/后厨共用的扫码入口就这么一个。
    expect(resolveLoginTab('/workbench/kitchen/recipe/detail?slug=changfen', 'admin')).toBe('staff')
    expect(resolveLoginTab('/workbench/kitchen/recipe', 'admin')).toBe('staff')
    expect(resolveLoginTab('/workbench/kitchen/recipe/qr', 'admin')).toBe('staff')
    // 老地址在入口先换成新地址，换完仍然开员工栏（扫码的人手里是旧二维码）。
    expect(resolveLoginTab('/recipe/detail?slug=changfen', 'admin')).toBe('staff')
  })

  it('配方管理不是阅读面：它照旧跟着记住值走', () => {
    expect(resolveLoginTab('/workbench/kitchen/recipe/manage', 'admin')).toBe('admin')
  })

  it('管理端路径、站外地址、登录页自身都不强制员工栏', () => {
    expect(resolveLoginTab('/admin', 'admin')).toBe('admin')
    expect(resolveLoginTab('/workbench/hr/roster', 'admin')).toBe('admin')
    expect(resolveLoginTab('/workbench/floor/daily', 'admin')).toBe('admin')
    expect(resolveLoginTab('//evil.example', 'admin')).toBe('admin')
    expect(resolveLoginTab('//evil.example/workbench/me/today', 'admin')).toBe('admin')
    expect(resolveLoginTab('https://evil.example/workbench/me/today', 'admin')).toBe('admin')
    expect(resolveLoginTab('/login?next=/workbench/me/today', 'admin')).toBe('admin')
    // 旧路径也不是员工端了（搬走 + 删除，不留别名）。
    expect(resolveLoginTab('/today', 'admin')).toBe('admin')
    expect(resolveLoginTab(undefined, undefined)).toBe('staff')
  })

  it('数组取第一个', () => {
    expect(resolveLoginTab(['/admin', '/workbench/me/today'], 'staff')).toBe('staff')
    expect(resolveLoginTab(['/workbench/me/today', '/admin'], 'admin')).toBe('staff')
  })
})

describe('老地址的 ?next= 迁移（票 03/04/05/07/08）', () => {
  // 老路由已经删干净了，`?next=` 里可能还存着搬家前的老地址（旧书签、上一次被挡下来
  // 写进 URL 的那条）——在入口处换成新地址，原样放行就是跳进白屏。
  it('票 08：管理后台的 /prep-plan 换成后勤组的新地址，query 与 hash 原样带过去', () => {
    expect(resolveLoginNext('/prep-plan')).toBe('/workbench/kitchen/prep-plan')
    expect(resolveLoginNext('/prep-plan?day=2026-10-04'))
      .toBe('/workbench/kitchen/prep-plan?day=2026-10-04')
    expect(resolveLoginNext('/prep-plan#board')).toBe('/workbench/kitchen/prep-plan#board')
    expect(resolveLoginNext('/prep-plan/')).toBe('/workbench/kitchen/prep-plan/')
    // 前缀相同不等于老地址：只按整段（含尾斜杠那一种）匹配。
    expect(resolveLoginNext('/prep-plans')).toBe('/prep-plans')
    expect(resolveLoginNext('/prep-plan-x')).toBe('/prep-plan-x')
    // 员工栏不把它当落点（那是工作台里给管理端与员工共用的页，不是员工端前缀）：
    // 换了地址之后仍在 `/workbench/` 底下、但不是员工端，落回员工首页。
    expect(resolveStaffNext('/prep-plan')).toBe('/workbench/me/today')
  })

  it('票 07：独立域的 /recipe* 换成工作台「后勤」组的新地址，query 与 hash 原样带过去', () => {
    // 扫码看岗位配方这条路最值钱：岗位码指向新地址，但**已经贴出去/存下来的**老地址
    // 还在别人手机里（`?next=` 里也可能是上一次被挡下来时写的那条）。
    expect(resolveLoginNext('/recipe')).toBe('/workbench/kitchen/recipe')
    expect(resolveLoginNext('/recipe/detail?slug=changfen'))
      .toBe('/workbench/kitchen/recipe/detail?slug=changfen')
    expect(resolveLoginNext('/recipe/print?slug=changfen'))
      .toBe('/workbench/kitchen/recipe/print?slug=changfen')
    expect(resolveLoginNext('/recipe/qr')).toBe('/workbench/kitchen/recipe/qr')
    expect(resolveLoginNext('/recipe/manage')).toBe('/workbench/kitchen/recipe/manage')
    expect(resolveLoginNext('/recipe/detail#top')).toBe('/workbench/kitchen/recipe/detail#top')
    expect(resolveLoginNext('/recipe/detail/')).toBe('/workbench/kitchen/recipe/detail/')
    // 前缀相同不等于老地址：只按整段（含尾斜杠那一种）匹配。
    expect(resolveLoginNext('/recipex')).toBe('/recipex')
    expect(resolveLoginNext('/recipe-x')).toBe('/recipe-x')
    // 员工栏同样认得（扫码的厨师走的就是这一栏）。
    expect(resolveStaffNext('/recipe/detail?slug=changfen'))
      .toBe('/workbench/kitchen/recipe/detail?slug=changfen')
  })

  it('票 05：工作台平铺的地址换成「人事 / 现场」两组的新地址', () => {
    // 票 04 那版平铺的 `/workbench/*` 是**正式地址**，这一票刚把它们按组重排 ——
    // 手机里存着的、上一次登录写进 URL 的都可能是这一批。
    expect(resolveLoginNext('/workbench/roster')).toBe('/workbench/hr/roster')
    expect(resolveLoginNext('/workbench/inbox')).toBe('/workbench/hr/inbox')
    expect(resolveLoginNext('/workbench/shifts?tab=add')).toBe('/workbench/hr/shifts?tab=add')
    expect(resolveLoginNext('/workbench/zones')).toBe('/workbench/floor/zones')
    expect(resolveLoginNext('/workbench/daily')).toBe('/workbench/floor/daily')
    expect(resolveLoginNext('/workbench/attire')).toBe('/workbench/floor/attire')
    expect(resolveLoginNext('/workbench/deep-clean')).toBe('/workbench/floor/deep-clean')
    expect(resolveLoginNext('/workbench/fix')).toBe('/workbench/floor/fix')
    expect(resolveLoginNext('/workbench/boards')).toBe('/workbench/floor/boards')
    expect(resolveLoginNext('/workbench/data')).toBe('/workbench/floor/data')
    // 带尾斜杠的写法也换（有人在地址栏里留过它）。
    expect(resolveLoginNext('/workbench/daily/')).toBe('/workbench/floor/daily/')
    expect(resolveLoginNext('/workbench/daily#top')).toBe('/workbench/floor/daily#top')
  })

  it('子应用根 /workbench 不是老地址：票 06 之前它仍是排班月历，原样留着', () => {
    expect(resolveLoginNext('/workbench')).toBe('/workbench')
    expect(resolveLoginNext('/workbench?tab=x')).toBe('/workbench?tab=x')
    // 前缀相同不等于老地址：只按整段（含尾斜杠那一种）匹配。
    expect(resolveLoginNext('/workbench/daily-x')).toBe('/workbench/daily-x')
  })

  it('更老的前缀（/hygiene/*、/scheduling*）直接换成新分组，query 与 hash 原样带过去', () => {
    expect(resolveLoginNext('/hygiene/daily')).toBe('/workbench/floor/daily')
    expect(resolveLoginNext('/hygiene/deep-clean')).toBe('/workbench/floor/deep-clean')
    // 花名册是人事页（票 05 起），老地址也照新分组换。
    expect(resolveLoginNext('/hygiene/roster')).toBe('/workbench/hr/roster')
    expect(resolveLoginNext('/scheduling')).toBe('/workbench/hr/calendar')
    expect(resolveLoginNext('/scheduling/inbox')).toBe('/workbench/hr/inbox')
    expect(resolveLoginNext('/scheduling/shifts?tab=add')).toBe('/workbench/hr/shifts?tab=add')
    expect(resolveLoginNext('/hygiene/daily#top')).toBe('/workbench/floor/daily#top')
  })

  it('编码过的形态到这里是"挡掉"而不是"认下来"（解码在 route.query 那层）', () => {
    // `?next=%2Fhygiene%2Fdaily` 进来时已被 vue-router 解成 `/hygiene/daily` —— 上一条覆盖的
    // 就是这个形态。裸的 `%2F…` 不是这条函数的输入，落到这里就按"不是站内路径"安全回落。
    expect(resolveLoginNext('%2Fhygiene%2Fdaily')).toBe('/')
    expect(resolveLoginNext('%2Fhygiene%2Fdaily', '/admin')).toBe('/admin')
  })

  it('新地址、别的后台页、站外地址都不受影响', () => {
    expect(resolveLoginNext('/workbench/floor/daily')).toBe('/workbench/floor/daily')
    expect(resolveLoginNext('/workbench/hr/calendar')).toBe('/workbench/hr/calendar')
    expect(resolveLoginNext('/logs')).toBe('/logs')
    // 新地址（票 08 起备货计划住在工作台里）原样留着。
    expect(resolveLoginNext('/workbench/kitchen/prep-plan?day=2026-10-04'))
      .toBe('/workbench/kitchen/prep-plan?day=2026-10-04')
    expect(resolveLoginNext('https://evil.example/hygiene/daily')).toBe('/')
    // 员工端前缀照旧不算管理端落点（老前缀迁过来之后仍然不是）。
    expect(resolveLoginNext('/hygiene/daily') === '/workbench/floor/daily').toBe(true)
    expect(resolveLoginNext('/workbench/me/today')).toBe('/')
  })

  it('员工栏：老的管理端地址既不是员工端、也不该被当落点', () => {
    expect(resolveStaffNext('/hygiene/daily')).toBe('/workbench/me/today')
    expect(resolveStaffNext('/workbench/daily')).toBe('/workbench/me/today')
    expect(resolveLoginTab('/hygiene/daily', 'admin')).toBe('admin')
    expect(resolveLoginTab('/scheduling/inbox', 'admin')).toBe('admin')
    expect(resolveLoginTab('/workbench/daily', 'admin')).toBe('admin')
  })

  it('员工端：`/staff/*` 换成工作台「我的」组（票 03）——那是唯一发出去过的老地址', () => {
    // 花名册页那张二维码发出去的就是 `/staff/today`，登录页的 `?next=` 里也可能还存着它。
    // 地址本身照旧 404（不留路由），这里换的只是「登录之后回哪儿」。
    expect(resolveStaffNext('/staff/today')).toBe('/workbench/me/today')
    expect(resolveStaffNext('/staff/month?m=2026-09')).toBe('/workbench/me/month?m=2026-09')
    expect(resolveStaffNext('/staff/clean')).toBe('/workbench/me/clean')
    expect(resolveLoginTab('/staff/today', 'admin')).toBe('staff')
    expect(resolveLoginTab('/staff/clean', 'admin')).toBe('staff')
    // 管理栏照样不认它（换完仍在员工前缀里）。
    expect(resolveLoginNext('/staff/today')).toBe('/')
  })
})
