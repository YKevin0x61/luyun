import { describe, expect, it } from 'vitest'
import {
  readerToggleLabel,
  recipeCardId,
  recipeCardIsInactive,
  recipeCardName,
} from '../recipeReaderActions.js'

describe('readerToggleLabel', () => {
  it('停用卡显示启用，启用卡显示停用', () => {
    expect(readerToggleLabel(true)).toBe('启用')
    expect(readerToggleLabel(false)).toBe('停用')
  })
})

describe('recipeCardId', () => {
  it('只接受正整数字符串', () => {
    expect(recipeCardId({ getAttribute: () => '120' })).toBe('120')
    expect(recipeCardId({ getAttribute: () => 'nope' })).toBe('')
    expect(recipeCardId(null)).toBe('')
  })
})

describe('recipeCardIsInactive', () => {
  it('认 recipe-card--inactive', () => {
    expect(recipeCardIsInactive({ classList: { contains: (c) => c === 'recipe-card--inactive' } })).toBe(true)
    expect(recipeCardIsInactive({ classList: { contains: () => false } })).toBe(false)
  })
})

describe('recipeCardName', () => {
  it('reads the card heading', () => {
    expect(recipeCardName({
      querySelector: () => ({ textContent: '  牛肉球馅  ' }),
    })).toBe('牛肉球馅')
    expect(recipeCardName({ querySelector: () => null })).toBe('')
  })
})
