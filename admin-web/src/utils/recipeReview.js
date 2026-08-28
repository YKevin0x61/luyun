/** Filter helpers for the manage-list「待复核」chip. */

export function recipeNeedsReview(recipe) {
  const value = recipe == null ? 0 : recipe.needs_review
  return value === true || value === 1 || value === '1'
}

export function filterRecipesByReview(recipes, reviewOnly) {
  const list = Array.isArray(recipes) ? recipes : []
  if (!reviewOnly) return list
  return list.filter(recipeNeedsReview)
}

export function recipeHasLegacyMarkdown(recipe) {
  const value = recipe == null ? null : recipe.legacy_markdown
  return value != null && String(value) !== ''
}
