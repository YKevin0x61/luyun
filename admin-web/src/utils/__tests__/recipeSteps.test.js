import { describe, expect, it } from 'vitest'
import {
  RECIPE_STEPS_ITEM_CLASS,
  RECIPE_STEPS_LIST_CLASS,
  addStepRow,
  dropBlankStepRows,
  moveStepRow,
  removeStepRow,
  renderStepsListHtml,
  renderStructuredRecipePreviewHtml,
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

describe('renderStepsListHtml', () => {
  it('empty list omits the ordered list', () => {
    expect(renderStepsListHtml([])).toBe('')
    expect(renderStepsListHtml(null)).toBe('')
  })

  it('one step matches backend class names', () => {
    const html = renderStepsListHtml(['混合面粉与水'])
    expect(html).toBe(
      '<ol class="recipe-steps">'
      + '<li class="recipe-steps-item">混合面粉与水</li>'
      + '</ol>',
    )
    expect(RECIPE_STEPS_LIST_CLASS).toBe('recipe-steps')
    expect(RECIPE_STEPS_ITEM_CLASS).toBe('recipe-steps-item')
  })

  it('many steps keep array order', () => {
    const html = renderStepsListHtml(['混合面粉与水', '静置 10 分钟', '分成剂子'])
    expect(html).toBe(
      '<ol class="recipe-steps">'
      + '<li class="recipe-steps-item">混合面粉与水</li>'
      + '<li class="recipe-steps-item">静置 10 分钟</li>'
      + '<li class="recipe-steps-item">分成剂子</li>'
      + '</ol>',
    )
  })

  it('escapes user strings', () => {
    const html = renderStepsListHtml(['<script>x</script> 1<"'])
    expect(html.includes('<script>')).toBe(false)
    expect(html.includes('&lt;script&gt;')).toBe(true)
  })
})

describe('renderStructuredRecipePreviewHtml', () => {
  it('concatenates ingredients table then steps list', () => {
    const html = renderStructuredRecipePreviewHtml(
      [{ name: '面粉', amount: '200', unit: 'g' }],
      ['混合面粉与水'],
    )
    expect(html).toBe(
      '<table class="recipe-ingredients"><tbody>'
      + '<tr>'
      + '<td class="recipe-ingredients-name">面粉</td>'
      + '<td class="recipe-ingredients-amount">200 g</td>'
      + '</tr>'
      + '</tbody></table>'
      + '<ol class="recipe-steps">'
      + '<li class="recipe-steps-item">混合面粉与水</li>'
      + '</ol>',
    )
  })

  it('omits missing blocks', () => {
    expect(renderStructuredRecipePreviewHtml([], ['混合'])).toBe(
      '<ol class="recipe-steps"><li class="recipe-steps-item">混合</li></ol>',
    )
    expect(renderStructuredRecipePreviewHtml(
      [{ name: '盐', amount: '1', unit: 'g' }],
      [],
    )).toBe(
      '<table class="recipe-ingredients"><tbody>'
      + '<tr>'
      + '<td class="recipe-ingredients-name">盐</td>'
      + '<td class="recipe-ingredients-amount">1 g</td>'
      + '</tr>'
      + '</tbody></table>',
    )
    expect(renderStructuredRecipePreviewHtml([], [])).toBe('')
  })
})
