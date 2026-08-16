import { beforeEach, describe, expect, it } from 'vitest'
import {
  noteSettingsVisit,
  stationChangeClearsSelection,
  takeSettingsReturnClear
} from '../kitchenSelectionReset.js'

describe('kitchen selection reset on settings return', () => {
  beforeEach(() => {
    while (takeSettingsReturnClear()) {
      /* drain leftover from a prior case */
    }
  })

  it('clears once after a 系统设置 visit, and not on an ordinary kitchen show', () => {
    expect(takeSettingsReturnClear()).toBe(false)
    noteSettingsVisit()
    expect(takeSettingsReturnClear()).toBe(true)
    expect(takeSettingsReturnClear()).toBe(false)
  })
})

describe('kitchen selection reset on station lock after refresh', () => {
  it('keeps 出餐选中 when a new-order refresh re-locks the same watched station', () => {
    expect(stationChangeClearsSelection('changfen', 'changfen')).toBe(false)
  })

  it('clears 出餐选中 only when the displayed station actually changes', () => {
    expect(stationChangeClearsSelection('changfen', 'shulong')).toBe(true)
    expect(stationChangeClearsSelection('', 'changfen')).toBe(true)
    expect(stationChangeClearsSelection('changfen', '')).toBe(false)
  })
})
