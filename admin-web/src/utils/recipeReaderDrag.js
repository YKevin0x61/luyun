/** Desktop-only recipe-card reorder on the station reader.
 * Mobile (narrow or coarse pointer) must never enable HTML5 drag — it fights scroll.
 */

export const READER_DRAG_BLOCK_MQ = '(max-width: 900px), (pointer: coarse)'

export const READER_DRAG_IGNORE_SELECTOR = 'button, a, input, textarea, select, .recipe-copy, .recipe-card-actions, .sop-scale-wrap'

export function eventElement(e) {
  const t = e && e.target
  if (!t) return null
  return t.nodeType === 1 ? t : t.parentElement
}

export function readerDragAllowed({ canEdit, blocked } = {}) {
  return !!canEdit && !blocked
}

export function applySubsetOrder(fullIds, subsetIdsInNewOrder) {
  const full = (fullIds || []).map((id) => Number(id))
  const seen = new Set()
  const queue = []
  for (const raw of subsetIdsInNewOrder || []) {
    const id = Number(raw)
    if (!Number.isInteger(id) || seen.has(id)) continue
    seen.add(id)
    queue.push(id)
  }
  const inFull = new Set(full)
  const filteredQueue = queue.filter((id) => inFull.has(id))
  const inSubset = new Set(filteredQueue)
  let i = 0
  return full.map((id) => {
    if (!inSubset.has(id)) return id
    return filteredQueue[i++]
  })
}

export function collectGridRecipeIds(grid) {
  if (!grid || typeof grid.querySelectorAll !== 'function') return []
  return Array.from(grid.querySelectorAll(':scope > article.recipe-card[data-recipe-id]'))
    .map((el) => Number(el.getAttribute('data-recipe-id')))
    .filter((id) => Number.isInteger(id) && id > 0)
}

export function insertCardRelative(fromCard, toCard) {
  if (!fromCard || !toCard || fromCard === toCard) return false
  const grid = toCard.parentElement
  if (!grid || fromCard.parentElement !== grid) return false
  const cards = Array.from(grid.querySelectorAll(':scope > article.recipe-card'))
  const fromIdx = cards.indexOf(fromCard)
  const toIdx = cards.indexOf(toCard)
  if (fromIdx < 0 || toIdx < 0 || fromIdx === toIdx) return false
  if (fromIdx < toIdx) toCard.after(fromCard)
  else toCard.before(fromCard)
  return true
}

export function sameSectionGrid(fromCard, toCard) {
  if (!fromCard || !toCard) return false
  const fromGrid = fromCard.closest?.('.sop-section-grid')
  const toGrid = toCard.closest?.('.sop-section-grid')
  return !!fromGrid && fromGrid === toGrid
}
