/** Helpers for recipe-card action buttons on the station reader. */

export function readerToggleLabel(isInactive) {
  return isInactive ? '启用' : '停用'
}

export function recipeCardIsInactive(card) {
  return !!(card && card.classList && card.classList.contains('recipe-card--inactive'))
}

export function recipeCardName(card) {
  if (!card || typeof card.querySelector !== 'function') return ''
  const h3 = card.querySelector('.recipe-card-head h3, h3.recipe-title')
  return (h3 && h3.textContent ? h3.textContent : '').trim()
}

export function recipeCardId(card) {
  const raw = card && typeof card.getAttribute === 'function'
    ? String(card.getAttribute('data-recipe-id') || '')
    : ''
  return /^\d+$/.test(raw) ? raw : ''
}
