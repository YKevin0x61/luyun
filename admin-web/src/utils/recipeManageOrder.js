/** Section combobox options and sort_order assignment for recipe manage list.
 * Keep RECIPE_SECTIONS in lockstep with services/recipes/sections.py.
 */

export const RECIPE_SECTIONS = ['配方', '出品标准', '检核要求', '食安要求']
export const DEFAULT_RECIPE_SECTION = '配方'

export function canonicalizeSection(name) {
  const text = String(name || '').trim()
  if (RECIPE_SECTIONS.includes(text)) return text
  if (text.includes('食安')) return '食安要求'
  if (text.includes('检核')) return '检核要求'
  if (text.includes('出品') && text.includes('标准')) return '出品标准'
  return DEFAULT_RECIPE_SECTION
}

export function buildSectionOptions() {
  return RECIPE_SECTIONS.map((section) => ({ value: section, label: section }))
}

export function defaultSectionForNewRecipe() {
  return DEFAULT_RECIPE_SECTION
}

export function assignSortOrders(orderedRecipes) {
  return (orderedRecipes || []).map((recipe, index) => ({
    id: recipe.id,
    sort_order: index,
  }))
}
