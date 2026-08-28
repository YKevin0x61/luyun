/** Step row list helpers and preview HTML. Class names match services/recipes/structured_render.py. */

import { renderIngredientsTableHtml } from './recipeIngredients.js'
import { renderTipsListHtml } from './recipeTips.js'

export const RECIPE_STEPS_LIST_CLASS = 'recipe-steps'
export const RECIPE_STEPS_ITEM_CLASS = 'recipe-steps-item'

export function addStepRow(rows) {
  return [...(rows || []), '']
}

export function removeStepRow(rows, index) {
  const list = rows || []
  if (index < 0 || index >= list.length) return list
  return list.filter((_, i) => i !== index)
}

export function moveStepRow(rows, fromIndex, toIndex) {
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

export function dropBlankStepRows(rows) {
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

export function renderStepsListHtml(rows) {
  const filled = dropBlankStepRows(rows).map((text) => String(text).trim())
  if (!filled.length) return ''
  const items = filled.map((text) => (
    `<li class="${RECIPE_STEPS_ITEM_CLASS}">${escapeHtml(text)}</li>`
  )).join('')
  return `<ol class="${RECIPE_STEPS_LIST_CLASS}">${items}</ol>`
}

export function renderStructuredRecipePreviewHtml(ingredients, steps, tips) {
  return renderIngredientsTableHtml(ingredients)
    + renderStepsListHtml(steps)
    + renderTipsListHtml(tips)
}
