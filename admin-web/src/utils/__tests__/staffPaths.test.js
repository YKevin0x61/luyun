/** 员工手机端路径名单（票 05 抽出来的一份）：判据只有这一处，边界要钉死。
 *
 *  名单被三处消费（路由守卫、401 白名单、PWA 清单归属），任何一处判错都是
 *  员工被甩去管理端登录页 / 挂错清单 —— 所以这里把边界一条条写下来，
 *  而不是只在 loginNext、pwaManifest 的测试里顺手覆盖。
 */
import { describe, expect, it } from 'vitest'
import { STAFF_PHONE_PREFIXES, isStaffPhonePath } from '../staffPaths.js'

describe('isStaffPhonePath', () => {
  it('两个入口本身算员工端', () => {
    expect(isStaffPhonePath('/today')).toBe(true)
    expect(isStaffPhonePath('/hygiene')).toBe(true)
  })

  it('前缀下面的子页面算员工端，带尾斜杠也算', () => {
    expect(isStaffPhonePath('/hygiene/login')).toBe(true)
    expect(isStaffPhonePath('/hygiene/daily')).toBe(true)
    expect(isStaffPhonePath('/today/')).toBe(true)
    expect(isStaffPhonePath('/hygiene/')).toBe(true)
  })

  it('长得像但不是子路径的不算（管理端那几页最容易被前缀顺手带走）', () => {
    expect(isStaffPhonePath('/todayx')).toBe(false)
    expect(isStaffPhonePath('/hygienex')).toBe(false)
    expect(isStaffPhonePath('/hygiene-roster')).toBe(false)
    expect(isStaffPhonePath('/hygiene-zones')).toBe(false)
  })

  it('管理端页面不算员工端', () => {
    expect(isStaffPhonePath('/')).toBe(false)
    expect(isStaffPhonePath('/admin')).toBe(false)
    expect(isStaffPhonePath('/scheduling')).toBe(false)
    expect(isStaffPhonePath('/login')).toBe(false)
    expect(isStaffPhonePath('/recipe/detail')).toBe(false)
  })

  it('空值不抛也不当成员工端', () => {
    expect(isStaffPhonePath('')).toBe(false)
    expect(isStaffPhonePath(null)).toBe(false)
    expect(isStaffPhonePath(undefined)).toBe(false)
  })

  it('名单本身是这两条（服务端 main.py 的 HTML_AUTH_* 照着它对表）', () => {
    expect(STAFF_PHONE_PREFIXES).toEqual(['/hygiene', '/today'])
  })
})
