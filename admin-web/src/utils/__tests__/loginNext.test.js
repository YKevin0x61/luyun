import { describe, expect, it } from 'vitest'
import {
  buildLoginNextFromRoute,
  isRecipeReaderPath,
  resolveLoginNext,
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
    expect(shouldSkipLoginRedirect('/setup')).toBe(true)
    expect(shouldSkipLoginRedirect('/recipe/detail')).toBe(true)
  })

  it('员工手机卫生入口不因 401 跳后台登录', () => {
    expect(shouldSkipLoginRedirect('/hygiene')).toBe(true)
    expect(shouldSkipLoginRedirect('/hygiene/login')).toBe(true)
    expect(shouldSkipLoginRedirect('/hygiene/register')).toBe(true)
  })

  it('管理面和运营页仍跳登录', () => {
    expect(shouldSkipLoginRedirect('/recipe/manage')).toBe(false)
    expect(shouldSkipLoginRedirect('/admin')).toBe(false)
    expect(shouldSkipLoginRedirect('/hygiene-roster')).toBe(false)
    expect(shouldSkipLoginRedirect('/hygiene-zones')).toBe(false)
    expect(shouldSkipLoginRedirect('/hygiene-daily')).toBe(false)
    expect(shouldSkipLoginRedirect('/hygiene-deep-clean')).toBe(false)
    expect(shouldSkipLoginRedirect('/hygiene-fix')).toBe(false)
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
})
