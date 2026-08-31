export function snapshotRecipeForm(form) {
  const src = form || {}
  return JSON.stringify({
    id: src.id,
    section: src.section == null ? '' : String(src.section),
    recipe_name: src.recipe_name == null ? '' : String(src.recipe_name),
    body: src.body == null ? '' : String(src.body),
    sort_order: src.sort_order,
    is_new: !!src.is_new,
    ingredients: src.ingredients || [],
    steps: src.steps || [],
    tips: src.tips || [],
    base_servings_qty: src.base_servings_qty == null ? '' : String(src.base_servings_qty),
    base_servings_unit: src.base_servings_unit == null ? '' : String(src.base_servings_unit),
  })
}

export function recipeFormIsDirty(form, baseline) {
  if (!baseline) return false
  return snapshotRecipeForm(form) !== baseline
}
