import { describe, expect, it } from 'vitest'
import { recipeFormIsDirty, snapshotRecipeForm } from '../recipeFormDirty.js'

const BASE = {
  id: 3,
  section: '浆比例',
  recipe_name: '面团',
  body: '',
  sort_order: 1,
  is_new: false,
  ingredients: [{ name: '面粉', amount: '200', unit: 'g' }],
  steps: ['和面'],
  tips: [],
  base_servings_qty: '10',
  base_servings_unit: '人份',
}

describe('recipeFormIsDirty', () => {
  it('未改过则不脏', () => {
    const baseline = snapshotRecipeForm(BASE)
    expect(recipeFormIsDirty(BASE, baseline)).toBe(false)
  })

  it('改用料即脏', () => {
    const baseline = snapshotRecipeForm(BASE)
    const next = {
      ...BASE,
      ingredients: [{ name: '面粉', amount: '250', unit: 'g' }],
    }
    expect(recipeFormIsDirty(next, baseline)).toBe(true)
  })

  it('没有基线不当成脏（避免误拦）', () => {
    expect(recipeFormIsDirty(BASE, '')).toBe(false)
  })
})
