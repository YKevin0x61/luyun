/** 员工手机端路径名单（票 05 抽出来的一份）：判据只有这一处，边界要钉死。
 *
 *  名单被三处消费（路由守卫、401 白名单、PWA 清单归属），任何一处判错都是
 *  员工被甩去管理端登录页 / 挂错清单 —— 所以这里把边界一条条写下来，
 *  而不是只在 loginNext、pwaManifest 的测试里顺手覆盖。
 *
 *  票 02 起名单拆成两层语义：`isStaffPhonePath`（员工侧界面 = 前缀 ∪ 精确）与
 *  `isStaffLandingPath`（登录后落点 = 只要前缀）。`/register` 属于前者、不属于后者。
 *  票 03 起前缀是 `/workbench/me`（三页：今天 /workbench/me/today、整月
 *  /workbench/me/month、卫生待办 /workbench/me/clean），旧的 `/staff/*` 一律不再算
 *  员工路径 —— 它连页面都不是了（自然 404，不留别名）。
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import {
  STAFF_ENTRY_PATH,
  STAFF_PHONE_EXACT,
  STAFF_PHONE_PREFIXES,
  isStaffLandingPath,
  isStaffPhonePath,
} from '../staffPaths.js'

const here = dirname(fileURLToPath(import.meta.url))

describe('isStaffPhonePath', () => {
  it('员工端三页都算员工端', () => {
    expect(isStaffPhonePath('/workbench/me/today')).toBe(true)
    expect(isStaffPhonePath('/workbench/me/month')).toBe(true)
    expect(isStaffPhonePath('/workbench/me/clean')).toBe(true)
  })

  it('前缀下面的子页面算员工端，带尾斜杠也算', () => {
    expect(isStaffPhonePath('/workbench/me')).toBe(true)
    expect(isStaffPhonePath('/workbench/me/')).toBe(true)
    expect(isStaffPhonePath('/workbench/me/today/')).toBe(true)
    expect(isStaffPhonePath('/workbench/me/month/')).toBe(true)
  })

  it('注册页在前缀之外，靠精确名单算员工端（装出来才是员工 PWA）', () => {
    expect(isStaffPhonePath('/register')).toBe(true)
  })

  it('旧路径搬走之后不再算员工端（不做别名，也就不该有任何豁免）', () => {
    expect(isStaffPhonePath('/staff')).toBe(false)
    expect(isStaffPhonePath('/staff/today')).toBe(false)
    expect(isStaffPhonePath('/staff/month')).toBe(false)
    expect(isStaffPhonePath('/staff/clean')).toBe(false)
    expect(isStaffPhonePath('/today')).toBe(false)
    expect(isStaffPhonePath('/today/month')).toBe(false)
    expect(isStaffPhonePath('/hygiene')).toBe(false)
  })

  it('长得像但不是子路径的不算（管理端那几页最容易被前缀顺手带走）', () => {
    expect(isStaffPhonePath('/workbench/mex')).toBe(false)
    expect(isStaffPhonePath('/workbench/mex/today')).toBe(false)
    expect(isStaffPhonePath('/staffx')).toBe(false)
    expect(isStaffPhonePath('/hygienex')).toBe(false)
    // 工作台里店长那几页与员工三页只差一个词，别顺手带走。
    expect(isStaffPhonePath('/workbench/roster')).toBe(false)
    expect(isStaffPhonePath('/workbench/zones')).toBe(false)
    expect(isStaffPhonePath('/workbench/daily')).toBe(false)
    // 越权落点是两套身份共用的，不是员工页。
    expect(isStaffPhonePath('/workbench/forbidden')).toBe(false)
  })

  it('精确名单不能靠前缀猜中：/register/ 带尾斜杠不算（服务端也没放行它）', () => {
    expect(isStaffPhonePath('/register/')).toBe(false)
    expect(isStaffPhonePath('/registering')).toBe(false)
  })

  it('管理端页面不算员工端', () => {
    expect(isStaffPhonePath('/')).toBe(false)
    expect(isStaffPhonePath('/admin')).toBe(false)
    expect(isStaffPhonePath('/workbench')).toBe(false)
    expect(isStaffPhonePath('/login')).toBe(false)
    expect(isStaffPhonePath('/recipe/detail')).toBe(false)
  })

  it('空值不抛也不当成员工端', () => {
    expect(isStaffPhonePath('')).toBe(false)
    expect(isStaffPhonePath(null)).toBe(false)
    expect(isStaffPhonePath(undefined)).toBe(false)
  })

  it('名单本身是这一条（服务端免墙表**不再**照着它放行，见 tests/test_auth.py）', () => {
    expect(STAFF_PHONE_PREFIXES).toEqual(['/workbench/me'])
  })
})

describe('isStaffLandingPath', () => {
  it('员工前缀下的页是登录后落点', () => {
    expect(isStaffLandingPath('/workbench/me/today')).toBe(true)
    expect(isStaffLandingPath('/workbench/me/today/')).toBe(true)
    expect(isStaffLandingPath('/workbench/me/month')).toBe(true)
    expect(isStaffLandingPath('/workbench/me/clean')).toBe(true)
    expect(isStaffLandingPath('/workbench/me')).toBe(true)
  })

  it('注册页不是落点：还没批准、会话也不存在，送过去就是死路', () => {
    expect(isStaffLandingPath('/register')).toBe(false)
  })

  it('管理端页面同样不是落点，旧员工路径也不再是落点', () => {
    expect(isStaffLandingPath('/')).toBe(false)
    expect(isStaffLandingPath('/admin')).toBe(false)
    expect(isStaffLandingPath('/staff/today')).toBe(false)
    expect(isStaffLandingPath('/staff')).toBe(false)
    expect(isStaffLandingPath('/today')).toBe(false)
    expect(isStaffLandingPath('/hygiene')).toBe(false)
    expect(isStaffLandingPath('')).toBe(false)
    expect(isStaffLandingPath(null)).toBe(false)
  })

  it('两份名单的层级：精确名单只加「员工侧界面」，不加「落点」', () => {
    expect(STAFF_PHONE_PREFIXES).toEqual(['/workbench/me'])
    expect(STAFF_PHONE_EXACT).toEqual(['/register'])
    for (const exact of STAFF_PHONE_EXACT) {
      expect(isStaffPhonePath(exact)).toBe(true)
      expect(isStaffLandingPath(exact)).toBe(false)
    }
  })
})

/** 员工端入口路径（票 04 漏改过一处、票 03 又搬了一次家）：门店把地址发给员工只有
 *  花名册页那一个入口，写错就是二维码扫出来一片空白。所以既断值、也断它落在员工落点
 *  白名单里，再拿一条源码契约把「用常量、不硬编码」钉死 —— 缺陷正是硬编码漏改。
 */
describe('STAFF_ENTRY_PATH', () => {
  it('入口是「今天」页，且确实是一个员工落点', () => {
    expect(STAFF_ENTRY_PATH).toBe('/workbench/me/today')
    expect(isStaffLandingPath(STAFF_ENTRY_PATH)).toBe(true)
    expect(isStaffPhonePath(STAFF_ENTRY_PATH)).toBe(true)
  })

  it('花名册页的员工入口用这个常量拼绝对 URL，不许再硬编码路径（防回归）', () => {
    const roster = readFileSync(join(here, '../../views/hygiene/HygieneRosterView.vue'), 'utf8')
    // 仍然用当前 origin（门店可能是内网 IP），但路径必须来自常量。
    expect(roster).toMatch(/window\.location\.origin/)
    expect(roster).toMatch(/\$\{STAFF_ENTRY_PATH\}/)
    expect(roster).toMatch(/STAFF_ENTRY_PATH/)
    // 死路径 `/hygiene`、旧的 `/staff` 与任何写死的新路径都不许出现在入口拼接里。
    expect(roster).not.toMatch(/origin\}\/hygiene/)
    expect(roster).not.toMatch(/origin\}\/staff/)
    expect(roster).not.toMatch(/origin\}\/workbench/)
  })
})
