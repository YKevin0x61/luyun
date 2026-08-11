import { describe, expect, it } from 'vitest'
import {
  SAVED_VALUE_SLOT,
  buildLoginSignature,
  parseTargetUrl,
  sigField,
} from '../posCredentials.js'

describe('parseTargetUrl', () => {
  it('parses tableList URL', () => {
    const parsed = parseTargetUrl(
      'https://cy7mm.wuuxiang.com/home/tableList/1/100001/200002?shopName=ExampleShop',
    )
    expect(parsed).toEqual({
      shop_id: '100001',
      company_id: '200002',
      shop_name: 'ExampleShop',
    })
  })

  it('parses tableStateInfo path shape', () => {
    const parsed = parseTargetUrl(
      'https://cy7mm.wuuxiang.com/home/tableStateInfo/100001/200002?shopName=A',
    )
    expect(parsed).toEqual({
      shop_id: '100001',
      company_id: '200002',
      shop_name: 'A',
    })
  })

  it('rejects placeholder templates', () => {
    expect(parseTargetUrl('https://x/home/tableList/1/{shopId}/{companyId}')).toBeNull()
  })

  it('rejects non-numeric ids', () => {
    expect(parseTargetUrl('https://x/home/tableList/1/abc/200002')).toBeNull()
  })
})

describe('buildLoginSignature / sigField', () => {
  it('joins trimmed fields', () => {
    expect(buildLoginSignature(' 1 ', 'pw', 's', 'c', 'n', 'd')).toBe('1||pw||s||c||n||d')
  })

  it('uses saved slot when configured and empty', () => {
    expect(sigField('', true)).toBe(SAVED_VALUE_SLOT)
    expect(sigField('', false)).toBe('')
    expect(sigField('x', true)).toBe('x')
  })
})
