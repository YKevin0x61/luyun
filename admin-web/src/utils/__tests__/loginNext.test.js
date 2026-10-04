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
  it('岗位列表、详情、打印、二维码是阅读面', () => {
    expect(isRecipeReaderPath('/recipe')).toBe(true)
    expect(isRecipeReaderPath('/recipe/detail')).toBe(true)
    expect(isRecipeReaderPath('/recipe/print')).toBe(true)
    expect(isRecipeReaderPath('/recipe/qr')).toBe(true)
  })

  it('配方管理不是阅读面', () => {
    expect(isRecipeReaderPath('/recipe/manage')).toBe(false)
    expect(isRecipeReaderPath('/admin')).toBe(false)
  })
})

describe('shouldSkipLoginRedirect', () => {
  it('登录页、配置页和配方阅读面不因 401 整页跳登录', () => {
    expect(shouldSkipLoginRedirect('/login')).toBe(true)
    expect(shouldSkipLoginRedirect('/settings')).toBe(true)
    expect(shouldSkipLoginRedirect('/recipe/detail')).toBe(true)
  })

  it('旧配置页地址 /setup 改名后不再有豁免（不做别名）', () => {
    expect(shouldSkipLoginRedirect('/setup')).toBe(false)
  })

  it('员工手机端的页不因 401 跳后台登录（票 04 起在 /staff/*）', () => {
    expect(shouldSkipLoginRedirect('/staff/today')).toBe(true)
    expect(shouldSkipLoginRedirect('/staff/today/')).toBe(true)
    expect(shouldSkipLoginRedirect('/staff/month')).toBe(true)
    expect(shouldSkipLoginRedirect('/staff/clean')).toBe(true)
    // 注册页搬到顶层 /register：它没有管理端会话，401 也不该整页跳 /login。
    expect(shouldSkipLoginRedirect('/register')).toBe(true)
  })

  it('旧员工路径已删除：401 不再给它们任何豁免（不做别名）', () => {
    expect(shouldSkipLoginRedirect('/today')).toBe(false)
    expect(shouldSkipLoginRedirect('/today/month')).toBe(false)
    expect(shouldSkipLoginRedirect('/hygiene')).toBe(false)
  })

  it('管理面和运营页仍跳登录', () => {
    expect(shouldSkipLoginRedirect('/recipe/manage')).toBe(false)
    expect(shouldSkipLoginRedirect('/admin')).toBe(false)
    expect(shouldSkipLoginRedirect('/workbench/roster')).toBe(false)
    expect(shouldSkipLoginRedirect('/workbench/zones')).toBe(false)
    expect(shouldSkipLoginRedirect('/workbench/daily')).toBe(false)
    expect(shouldSkipLoginRedirect('/workbench/deep-clean')).toBe(false)
    expect(shouldSkipLoginRedirect('/workbench/fix')).toBe(false)
    expect(shouldSkipLoginRedirect('/workbench/boards')).toBe(false)
  })
})

describe('buildLoginNextFromRoute', () => {
  it('用已解码的 query 拼 next，slug 只编码一次', () => {
    const next = buildLoginNextFromRoute({
      path: '/recipe/detail',
      query: { slug: '肠粉档' },
    })
    expect(next).toBe('/recipe/detail?slug=%E8%82%A0%E7%B2%89%E6%A1%A3')
    expect(next).not.toContain('%25')
  })

  it('没有 query 时只返回 path', () => {
    expect(buildLoginNextFromRoute({ path: '/recipe/manage', query: {} })).toBe('/recipe/manage')
  })
})

describe('resolveLoginNext', () => {
  it('解开误伤的双重编码 slug', () => {
    const raw = '/recipe/detail?slug=%25E8%2582%25A0%25E7%25B2%2589%25E6%25A1%25A3'
    expect(resolveLoginNext(raw)).toBe('/recipe/detail?slug=%E8%82%A0%E7%B2%89%E6%A1%A3')
  })

  it('拒绝开放重定向', () => {
    expect(resolveLoginNext('https://evil.example/phish')).toBe('/')
    expect(resolveLoginNext('//evil.example')).toBe('/')
    expect(resolveLoginNext('/login?next=/admin')).toBe('/')
  })

  it('空值回退', () => {
    expect(resolveLoginNext(null, '/recipe')).toBe('/recipe')
    expect(resolveLoginNext('', '/recipe')).toBe('/recipe')
  })

  it('管理员身份不认员工端落点（身份互斥）', () => {
    // 员工页那扇门只认员工 cookie：管理员被送进去也只会被客户端守卫弹回 /login。
    expect(resolveLoginNext('/staff/today')).toBe('/')
    expect(resolveLoginNext('/staff/today?day=2')).toBe('/')
    expect(resolveLoginNext('/staff/month')).toBe('/')
    expect(resolveLoginNext('/staff/clean')).toBe('/')
    expect(resolveLoginNext('/staff/today', '/admin')).toBe('/admin')
  })

  it('管理员身份照旧放行任意站内管理路径（含 query 与菜谱阅读面）', () => {
    expect(resolveLoginNext('/workbench/roster')).toBe('/workbench/roster')
    expect(resolveLoginNext('/admin?tab=orders')).toBe('/admin?tab=orders')
    expect(resolveLoginNext('/recipe/detail?slug=congee')).toBe('/recipe/detail?slug=congee')
  })
})

describe('resolveStaffNext', () => {
  it('员工那三页原样放行（含子页面与 query）', () => {
    expect(resolveStaffNext('/staff/today')).toBe('/staff/today')
    expect(resolveStaffNext('/staff/clean')).toBe('/staff/clean')
    expect(resolveStaffNext('/staff/month')).toBe('/staff/month')
    expect(resolveStaffNext('/staff/month?m=2026-09')).toBe('/staff/month?m=2026-09')
    expect(resolveStaffNext('/staff/today/')).toBe('/staff/today/')
  })

  it('站外地址与协议相对地址一律回落到员工默认落点', () => {
    expect(resolveStaffNext('https://evil.example/phish')).toBe('/staff/today')
    expect(resolveStaffNext('//evil.example')).toBe('/staff/today')
    expect(resolveStaffNext('/\\evil.example')).toBe('/staff/today')
    expect(resolveStaffNext('javascript:alert(1)')).toBe('/staff/today')
  })

  it('管理端页面不算员工落点（连名字像的也不算）', () => {
    expect(resolveStaffNext('/admin')).toBe('/staff/today')
    expect(resolveStaffNext('/workbench/roster')).toBe('/staff/today')
    expect(resolveStaffNext('/workbench/zones')).toBe('/staff/today')
    expect(resolveStaffNext('/staffx')).toBe('/staff/today')
    expect(resolveStaffNext('/login?next=/admin')).toBe('/staff/today')
  })

  it('旧员工路径不再是落点：搬走之后没留别名', () => {
    for (const stale of ['/today', '/today/month', '/hygiene']) {
      expect(resolveStaffNext(stale)).toBe('/staff/today')
    }
  })

  it('注册页是员工侧界面，但不是登录后落点：?next=/register 回落到默认落点', () => {
    // 注册成功还在等超级管理员批准、会话也不存在 —— 把 /register 当落点就是死路。
    expect(resolveStaffNext('/register')).toBe('/staff/today')
    expect(resolveStaffNext('/register?from=login')).toBe('/staff/today')
  })

  it('默认落在今天页；也认调用方给的兜底', () => {
    expect(resolveStaffNext(null)).toBe('/staff/today')
    expect(resolveStaffNext('')).toBe('/staff/today')
    expect(resolveStaffNext(undefined)).toBe('/staff/today')
    expect(resolveStaffNext(undefined, '/staff/clean')).toBe('/staff/clean')
    expect(resolveStaffNext('/admin', '/staff/clean')).toBe('/staff/clean')
  })

  it('数组取第一个（vue-router 的 query 可能是数组）', () => {
    expect(resolveStaffNext(['/staff/today', '/admin'])).toBe('/staff/today')
    expect(resolveStaffNext(['/admin', '/staff/today'])).toBe('/staff/today')
  })

  it('误伤的双重编码照旧解开', () => {
    expect(resolveStaffNext('/staff/today?next=%252Fstaff%252Ftoday')).toBe('/staff/today?next=%2Fstaff%2Ftoday')
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
    expect(resolveLoginTab('/staff/today', 'admin')).toBe('staff')
    expect(resolveLoginTab('/staff/month?m=2026-09', 'admin')).toBe('staff')
    expect(resolveLoginTab('/staff/clean', 'admin')).toBe('staff')
    // 误伤的双重编码照旧认得出（`%25` 在路径后面时解一层）。
    expect(resolveLoginTab('/staff/today?next=%252Fstaff%252Ftoday', 'admin')).toBe('staff')
  })

  it('管理端路径、站外地址、登录页自身都不强制员工栏', () => {
    expect(resolveLoginTab('/admin', 'admin')).toBe('admin')
    expect(resolveLoginTab('/workbench/roster', 'admin')).toBe('admin')
    expect(resolveLoginTab('//evil.example', 'admin')).toBe('admin')
    expect(resolveLoginTab('//evil.example/staff/today', 'admin')).toBe('admin')
    expect(resolveLoginTab('https://evil.example/staff/today', 'admin')).toBe('admin')
    expect(resolveLoginTab('/login?next=/staff/today', 'admin')).toBe('admin')
    // 旧路径也不是员工端了（搬走 + 删除，不留别名）。
    expect(resolveLoginTab('/today', 'admin')).toBe('admin')
    expect(resolveLoginTab(undefined, undefined)).toBe('staff')
  })

  it('数组取第一个', () => {
    expect(resolveLoginTab(['/admin', '/staff/today'], 'staff')).toBe('staff')
    expect(resolveLoginTab(['/staff/today', '/admin'], 'admin')).toBe('staff')
  })
})

describe('老前缀的 ?next= 迁移（票 03/04）', () => {
  // 老路由已经删干净了，`?next=` 里可能还存着搬家前的老地址（旧书签、上一次被挡下来
  // 写进 URL 的那条）——在入口处换成新前缀，原样放行就是跳进白屏。
  it('管理端：老前缀换成工作台前缀，query 与 hash 原样带过去', () => {
    expect(resolveLoginNext('/hygiene/daily')).toBe('/workbench/daily')
    expect(resolveLoginNext('/hygiene/deep-clean')).toBe('/workbench/deep-clean')
    expect(resolveLoginNext('/scheduling')).toBe('/workbench')
    expect(resolveLoginNext('/scheduling/inbox')).toBe('/workbench/inbox')
    expect(resolveLoginNext('/scheduling/shifts?tab=add')).toBe('/workbench/shifts?tab=add')
    expect(resolveLoginNext('/hygiene/daily#top')).toBe('/workbench/daily#top')
  })

  it('编码过的形态到这里是"挡掉"而不是"认下来"（解码在 route.query 那层）', () => {
    // `?next=%2Fhygiene%2Fdaily` 进来时已被 vue-router 解成 `/hygiene/daily` —— 上一条覆盖的
    // 就是这个形态。裸的 `%2F…` 不是这条函数的输入，落到这里就按"不是站内路径"安全回落。
    expect(resolveLoginNext('%2Fhygiene%2Fdaily')).toBe('/')
    expect(resolveLoginNext('%2Fhygiene%2Fdaily', '/admin')).toBe('/admin')
  })

  it('新前缀、别的后台页、站外地址都不受影响', () => {
    expect(resolveLoginNext('/workbench/daily')).toBe('/workbench/daily')
    expect(resolveLoginNext('/logs')).toBe('/logs')
    expect(resolveLoginNext('/prep-plan?day=2026-10-04')).toBe('/prep-plan?day=2026-10-04')
    expect(resolveLoginNext('https://evil.example/hygiene/daily')).toBe('/')
    // 员工端前缀照旧不算管理端落点（老前缀迁过来之后仍然不是）。
    expect(resolveLoginNext('/hygiene/daily') === '/workbench/daily').toBe(true)
    expect(resolveLoginNext('/staff/today')).toBe('/')
  })

  it('员工栏：老的管理端地址既不是员工端、也不该被当落点', () => {
    expect(resolveStaffNext('/hygiene/daily')).toBe('/staff/today')
    expect(resolveLoginTab('/hygiene/daily', 'admin')).toBe('admin')
    expect(resolveLoginTab('/scheduling/inbox', 'admin')).toBe('admin')
  })
})
