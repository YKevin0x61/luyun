/** Ingredient row list helpers. Amount cell class matches structured_render (reader scaling). */

export const RECIPE_INGREDIENTS_AMOUNT_CLASS = 'recipe-ingredients-amount'

export function emptyIngredient() {
  return { name: '', amount: '', unit: '' }
}

export function addIngredientRow(rows) {
  return [...(rows || []), emptyIngredient()]
}

export function removeIngredientRow(rows, index) {
  const list = rows || []
  if (index < 0 || index >= list.length) return list
  return list.filter((_, i) => i !== index)
}

export function moveIngredientRow(rows, fromIndex, toIndex) {
  const list = rows || []
  if (
    fromIndex === toIndex
    || fromIndex < 0
    || toIndex < 0
    || fromIndex >= list.length
    || toIndex >= list.length
  ) {
    return list
  }
  const next = [...list]
  const [moved] = next.splice(fromIndex, 1)
  next.splice(toIndex, 0, moved)
  return next
}

function ingredientHasContent(row) {
  if (!row) return false
  return Boolean(
    String(row.name || '').trim()
    || String(row.amount || '').trim()
    || String(row.unit || '').trim(),
  )
}

export function dropBlankIngredientRows(rows) {
  return (rows || []).filter(ingredientHasContent)
}
