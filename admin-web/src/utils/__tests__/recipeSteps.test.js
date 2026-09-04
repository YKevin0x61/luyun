import { describe, expect, it } from 'vitest'
import {
  addStepRow,
  dropBlankStepRows,
  moveStepRow,
  removeStepRow,
} from '../recipeSteps.js'

describe('step row list', () => {
  it('addStepRow appends an empty string row', () => {
    expect(addStepRow([])).toEqual([''])
    expect(addStepRow(['混合'])).toEqual(['混合', ''])
  })

  it('removeStepRow drops the index and does not mutate the source', () => {
    const src = ['混合面粉与水', '静置 10 分钟']
    expect(removeStepRow(src, 0)).toEqual(['静置 10 分钟'])
    expect(src).toEqual(['混合面粉与水', '静置 10 分钟'])
  })

  it('moveStepRow reorders and is a no-op out of range', () => {
    const src = ['A', 'B', 'C']
    expect(moveStepRow(src, 0, 2)).toEqual(['B', 'C', 'A'])
    expect(moveStepRow(src, 2, 0)).toEqual(['C', 'A', 'B'])
    expect(moveStepRow(src, -1, 0)).toEqual(src)
    expect(moveStepRow(src, 0, 9)).toEqual(src)
  })

  it('dropBlankStepRows drops empty and whitespace rows', () => {
    expect(dropBlankStepRows(['', '混合面粉与水', '  '])).toEqual(['混合面粉与水'])
  })
})
