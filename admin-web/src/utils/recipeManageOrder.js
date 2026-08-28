/** Section combobox options and sort_order assignment for recipe manage list. */

export const NEW_SECTION_VALUE = '__new__'

export function buildSectionOptions(recipes) {
  const seen = new Set()
  const options = []
  for (const recipe of recipes || []) {
    const section = recipe.section
    if (section == null || seen.has(section)) continue
    seen.add(section)
    options.push({ value: section, label: section })
  }
  options.push({ value: NEW_SECTION_VALUE, label: '新建章节…' })
  return options
}

export function assignSortOrders(orderedRecipes) {
  return (orderedRecipes || []).map((recipe, index) => ({
    id: recipe.id,
    sort_order: index,
  }))
}

