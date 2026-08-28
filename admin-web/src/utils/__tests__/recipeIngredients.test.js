import { describe, expect, it } from 'vitest'
import { SCALE_UNITS } from '../recipeCore.js'
import {
  RECIPE_INGREDIENTS_AMOUNT_CLASS,
  RECIPE_INGREDIENTS_NAME_CLASS,
  RECIPE_INGREDIENTS_TABLE_CLASS,
  addIngredientRow,
  dropBlankIngredientRows,
  emptyIngredient,
  moveIngredientRow,
  removeIngredientRow,
  renderIngredientsTableHtml,
} from '../recipeIngredients.js'

describe('SCALE_UNITS', () => {
  it('从 SCALE_UNIT 词表拆出建议单位，含千克与 g', () => {
    expect(SCALE_UNITS).toContain('千克')
    expect(SCALE_UNITS).toContain('g')
    expect(SCALE_UNITS).toContain('毫升')
    expect(SCALE_UNITS[0]).toBe('千克')
  })
})

describe('ingredient row list', () => {
  it('addIngredientRow appends an empty row', () => {
    expect(addIngredientRow([])).toEqual([emptyIngredient()])
    expect(addIngredientRow([{ name: '盐', amount: '1', unit: 'g' }])).toEqual([
      { name: '盐', amount: '1', unit: 'g' },
      { name: '', amount: '', unit: '' },
    ])
  })

  it('removeIngredientRow drops the index and does not mutate the source', () => {
    const src = [
      { name: '面粉', amount: '200', unit: 'g' },
      { name: '水', amount: '120', unit: 'ml' },
    ]
    expect(removeIngredientRow(src, 0)).toEqual([{ name: '水', amount: '120', unit: 'ml' }])
    expect(src).toEqual([
      { name: '面粉', amount: '200', unit: 'g' },
      { name: '水', amount: '120', unit: 'ml' },
    ])
  })

  it('moveIngredientRow reorders and is a no-op out of range', () => {
    const src = [
      { name: 'A', amount: '1', unit: 'g' },
      { name: 'B', amount: '2', unit: 'g' },
      { name: 'C', amount: '3', unit: 'g' },
    ]
    expect(moveIngredientRow(src, 0, 2)).toEqual([
      { name: 'B', amount: '2', unit: 'g' },
      { name: 'C', amount: '3', unit: 'g' },
      { name: 'A', amount: '1', unit: 'g' },
    ])
    expect(moveIngredientRow(src, 2, 0)).toEqual([
      { name: 'C', amount: '3', unit: 'g' },
      { name: 'A', amount: '1', unit: 'g' },
      { name: 'B', amount: '2', unit: 'g' },
    ])
    expect(moveIngredientRow(src, -1, 0)).toEqual(src)
    expect(moveIngredientRow(src, 0, 9)).toEqual(src)
  })

  it('dropBlankIngredientRows drops all-empty rows', () => {
    expect(dropBlankIngredientRows([
      { name: '', amount: '', unit: '' },
      { name: '盐', amount: '1', unit: 'g' },
      { name: '  ', amount: '', unit: '' },
    ])).toEqual([{ name: '盐', amount: '1', unit: 'g' }])
  })
})

describe('renderIngredientsTableHtml', () => {
  it('empty list omits the table', () => {
    expect(renderIngredientsTableHtml([])).toBe('')
    expect(renderIngredientsTableHtml(null)).toBe('')
  })

  it('one row matches backend class names and amount+unit', () => {
    const html = renderIngredientsTableHtml([
      { name: '面粉', amount: '200', unit: 'g' },
    ])
    expect(html).toBe(
      '<table class="recipe-ingredients"><tbody>'
      + '<tr>'
      + '<td class="recipe-ingredients-name">面粉</td>'
      + '<td class="recipe-ingredients-amount">200 g</td>'
      + '</tr>'
      + '</tbody></table>',
    )
    expect(RECIPE_INGREDIENTS_TABLE_CLASS).toBe('recipe-ingredients')
    expect(RECIPE_INGREDIENTS_NAME_CLASS).toBe('recipe-ingredients-name')
    expect(RECIPE_INGREDIENTS_AMOUNT_CLASS).toBe('recipe-ingredients-amount')
  })

  it('many rows and 适量 amount appear literally', () => {
    const html = renderIngredientsTableHtml([
      { name: '面粉', amount: '200', unit: 'g' },
      { name: '胡椒', amount: '适量', unit: '' },
    ])
    expect(html).toBe(
      '<table class="recipe-ingredients"><tbody>'
      + '<tr>'
      + '<td class="recipe-ingredients-name">面粉</td>'
      + '<td class="recipe-ingredients-amount">200 g</td>'
      + '</tr>'
      + '<tr>'
      + '<td class="recipe-ingredients-name">胡椒</td>'
      + '<td class="recipe-ingredients-amount">适量</td>'
      + '</tr>'
      + '</tbody></table>',
    )
  })

  it('escapes user strings', () => {
    const html = renderIngredientsTableHtml([
      { name: '<script>x</script>', amount: '1<', unit: 'g>"' },
    ])
    expect(html.includes('<script>')).toBe(false)
    expect(html.includes('&lt;script&gt;')).toBe(true)
  })
})
