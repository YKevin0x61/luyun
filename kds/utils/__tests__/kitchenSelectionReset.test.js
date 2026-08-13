import { beforeEach, describe, expect, it } from 'vitest'
import { noteSettingsVisit, takeSettingsReturnClear } from '../kitchenSelectionReset.js'

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
