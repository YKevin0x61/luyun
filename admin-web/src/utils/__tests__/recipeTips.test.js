import { describe, expect, it } from 'vitest'
import {
  RECIPE_TIPS_ITEM_CLASS,
  RECIPE_TIPS_LIST_CLASS,
  addTipRow,
  dropBlankTipRows,
  removeTipRow,
  renderTipsListHtml,
} from '../recipeTips.js'
import { renderStructuredRecipePreviewHtml } from '../recipeSteps.js'

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

describe('renderTipsListHtml', () => {
  it('empty list omits the unordered list', () => {
    expect(renderTipsListHtml([])).toBe('')
    expect(renderTipsListHtml(null)).toBe('')
  })

  it('one tip matches backend class names', () => {
    const html = renderTipsListHtml(['夏天水温要更低'])
    expect(html).toBe(
      '<ul class="recipe-tips">'
      + '<li class="recipe-tips-item">夏天水温要更低</li>'
      + '</ul>',
    )
    expect(RECIPE_TIPS_LIST_CLASS).toBe('recipe-tips')
    expect(RECIPE_TIPS_ITEM_CLASS).toBe('recipe-tips-item')
  })

  it('many tips keep array order', () => {
    const html = renderTipsListHtml(['夏天水温要更低', '饧面不要超过 20 分钟', '按口味调整盐'])
    expect(html).toBe(
      '<ul class="recipe-tips">'
      + '<li class="recipe-tips-item">夏天水温要更低</li>'
      + '<li class="recipe-tips-item">饧面不要超过 20 分钟</li>'
      + '<li class="recipe-tips-item">按口味调整盐</li>'
      + '</ul>',
    )
  })

  it('escapes user strings', () => {
    const html = renderTipsListHtml(['<script>x</script> 1<"'])
    expect(html.includes('<script>')).toBe(false)
    expect(html.includes('&lt;script&gt;')).toBe(true)
  })
})

describe('renderStructuredRecipePreviewHtml with tips', () => {
  it('concatenates ingredients then steps then tips', () => {
    const html = renderStructuredRecipePreviewHtml(
      [{ name: '面粉', amount: '200', unit: 'g' }],
      ['混合面粉与水'],
      ['夏天水温要更低'],
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
      + '</ol>'
      + '<ul class="recipe-tips">'
      + '<li class="recipe-tips-item">夏天水温要更低</li>'
      + '</ul>',
    )
  })

  it('omits missing tip block', () => {
    expect(renderStructuredRecipePreviewHtml([], ['混合'], [])).toBe(
      '<ol class="recipe-steps"><li class="recipe-steps-item">混合</li></ol>',
    )
    expect(renderStructuredRecipePreviewHtml([], [], ['夏天水温要更低'])).toBe(
      '<ul class="recipe-tips"><li class="recipe-tips-item">夏天水温要更低</li></ul>',
    )
  })
})
