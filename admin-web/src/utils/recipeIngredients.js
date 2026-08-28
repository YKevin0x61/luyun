/** Ingredient row list helpers and preview HTML. Class names match services/recipes/structured_render.py. */

export const RECIPE_INGREDIENTS_TABLE_CLASS = 'recipe-ingredients'
export const RECIPE_INGREDIENTS_NAME_CLASS = 'recipe-ingredients-name'
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

function escapeHtml(text) {
  return String(text ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#x27;')
}

export function formatIngredientQty(amount, unit) {
  const amountText = String(amount || '').trim()
  const unitText = String(unit || '').trim()
  if (amountText && unitText) return `${amountText} ${unitText}`
  return amountText || unitText
}

export function renderIngredientsTableHtml(rows) {
  const filled = dropBlankIngredientRows(rows)
  if (!filled.length) return ''
  const trs = filled.map((row) => {
    const name = escapeHtml(String(row.name || '').trim())
    const qty = escapeHtml(formatIngredientQty(row.amount, row.unit))
    return (
      '<tr>'
      + `<td class="${RECIPE_INGREDIENTS_NAME_CLASS}">${name}</td>`
      + `<td class="${RECIPE_INGREDIENTS_AMOUNT_CLASS}">${qty}</td>`
      + '</tr>'
    )
  }).join('')
  return `<table class="${RECIPE_INGREDIENTS_TABLE_CLASS}"><tbody>${trs}</tbody></table>`
}
