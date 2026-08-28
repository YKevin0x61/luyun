/** Tip row list helpers and preview HTML. Class names match services/recipes/structured_render.py. */

export const RECIPE_TIPS_LIST_CLASS = 'recipe-tips'
export const RECIPE_TIPS_ITEM_CLASS = 'recipe-tips-item'

export function addTipRow(rows) {
  return [...(rows || []), '']
}

export function removeTipRow(rows, index) {
  const list = rows || []
  if (index < 0 || index >= list.length) return list
  return list.filter((_, i) => i !== index)
}

export function dropBlankTipRows(rows) {
  return (rows || []).filter((text) => String(text || '').trim())
}

function escapeHtml(text) {
  return String(text ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#x27;')
}

export function renderTipsListHtml(rows) {
  const filled = dropBlankTipRows(rows).map((text) => String(text).trim())
  if (!filled.length) return ''
  const items = filled.map((text) => (
    `<li class="${RECIPE_TIPS_ITEM_CLASS}">${escapeHtml(text)}</li>`
  )).join('')
  return `<ul class="${RECIPE_TIPS_LIST_CLASS}">${items}</ul>`
}
