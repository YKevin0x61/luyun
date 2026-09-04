import { describe, expect, it } from 'vitest'
import {
  addTipRow,
  dropBlankTipRows,
  removeTipRow,
} from '../recipeTips.js'

describe('tip row list', () => {
  it('addTipRow appends an empty string row', () => {
    expect(addTipRow([])).toEqual([''])
    expect(addTipRow(['夏天水温要更低'])).toEqual(['夏天水温要更低', ''])
  })

  it('removeTipRow drops the index and does not mutate the source', () => {
    const src = ['夏天水温要更低', '饧面不要超过 20 分钟']
    expect(removeTipRow(src, 0)).toEqual(['饧面不要超过 20 分钟'])
    expect(src).toEqual(['夏天水温要更低', '饧面不要超过 20 分钟'])
  })

  it('dropBlankTipRows drops empty and whitespace rows', () => {
    expect(dropBlankTipRows(['', '夏天水温要更低', '  '])).toEqual(['夏天水温要更低'])
  })
})
