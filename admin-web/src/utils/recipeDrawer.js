/** Mobile recipe reader drawer machine. No DOM. */

export const RECIPE_DRAWER_PANELS = ['toc', 'more', 'search', 'font', 'servings']

export function isRecipeDrawerPanel(id) {
  return RECIPE_DRAWER_PANELS.includes(id)
}

export function nextRecipeDrawerState(state, event) {
  const current = isRecipeDrawerPanel(state && state.open) ? state.open : null
  const type = event && event.type
  const id = isRecipeDrawerPanel(event && event.id) ? event.id : null

  if (type === 'escape' || type === 'backdrop' || type === 'close') {
    return { open: null, restoreFocus: current }
  }
  if (type === 'toggle') {
    if (!id) return { open: current, restoreFocus: null }
    if (current === id) return { open: null, restoreFocus: id }
    return { open: id, restoreFocus: null }
  }
  return { open: current, restoreFocus: null }
}
