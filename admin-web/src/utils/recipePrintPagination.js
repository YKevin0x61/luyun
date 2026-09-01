/** A4 portrait used by the on-screen print preview sheets. */
export const A4_WIDTH_MM = 210
export const A4_HEIGHT_MM = 297
export const A4_PAD_Y_MM = 8
export const A4_PAD_X_MM = 10
export const A4_CONTENT_HEIGHT_MM = A4_HEIGHT_MM - A4_PAD_Y_MM * 2

/** Print preview newspaper packing. Keep in sync with `.sop-section-grid` / `.sop-print-col`. */
export const PRINT_COLUMN_COUNT = 3
export const PRINT_CARD_GAP_MM = 2

/**
 * Greedy pack: keep adding items while `fits(next, pageIndex)` is true.
 * The first item on a page is always accepted so a single oversized item still gets a sheet.
 */
export function packItemsIntoPages(items, fits) {
  const list = Array.isArray(items) ? items : []
  const pages = []
  let current = []
  for (const item of list) {
    const next = current.concat(item)
    if (current.length === 0 || fits(next, pages.length)) {
      current = next
      continue
    }
    pages.push(current)
    current = [item]
  }
  if (current.length) pages.push(current)
  return pages.length ? pages : [[]]
}

function emptyColumns(count) {
  return Array.from({ length: count }, () => [])
}

/**
 * Newspaper / masonry pack: each card goes into the shortest column where it still
 * fits. Ties go left, so the first three cards stay left-to-right. A tall card does
 * not open a new row — the next short card stacks under the shortest neighbor.
 * Chrome has no `grid-template-rows: masonry`; this is the print-preview stand-in.
 */
export function packNewspaperPages(items, { columns = PRINT_COLUMN_COUNT, gap = 0, pageHeightFor, heightOf } = {}) {
  const list = Array.isArray(items) ? items : []
  const colCount = Math.max(1, Number(columns) || PRINT_COLUMN_COUNT)
  const gapPx = Number(gap) || 0
  const measure = typeof heightOf === 'function' ? heightOf : () => 0
  const limitFor = typeof pageHeightFor === 'function' ? pageHeightFor : () => 0

  const pages = []
  let buckets = emptyColumns(colCount)
  let heights = Array(colCount).fill(0)
  let pageIndex = 0

  const used = () => buckets.some((col) => col.length)
  const flush = () => {
    if (!used()) return
    pages.push(buckets)
    pageIndex += 1
    buckets = emptyColumns(colCount)
    heights = Array(colCount).fill(0)
  }
  const fits = (col, h) => {
    const extra = buckets[col].length ? gapPx : 0
    return heights[col] + extra + h <= limitFor(pageIndex) + 1
  }

  for (const item of list) {
    const h = Number(measure(item)) || 0
    while (true) {
      let best = -1
      for (let col = 0; col < colCount; col++) {
        if (!fits(col, h)) continue
        if (best < 0 || heights[col] < heights[best]) best = col
      }
      if (best < 0 && !used()) {
        buckets[0].push(item)
        heights[0] = h
        break
      }
      if (best < 0) {
        flush()
        continue
      }
      const extra = buckets[best].length ? gapPx : 0
      buckets[best].push(item)
      heights[best] += extra + h
      break
    }
  }
  flush()
  return pages.length ? pages : [emptyColumns(colCount)]
}

export function stationPageHtml(titleHtml, columns) {
  const title = titleHtml || ''
  const cols = Array.from({ length: PRINT_COLUMN_COUNT }, (_, i) => {
    const cards = Array.isArray(columns?.[i]) ? columns[i] : []
    return `<div class="sop-print-col">${cards.join('')}</div>`
  })
  return `${title}<div class="sop-doc"><div class="sop-section"><div class="sop-section-grid">${cols.join('')}</div></div></div>`
}

export function paginateStationCards({ titleHtml, cards, pagePx, gapPx, titleReserve = 0 }) {
  const pages = packNewspaperPages(Array.isArray(cards) ? cards : [], {
    columns: PRINT_COLUMN_COUNT,
    gap: gapPx,
    heightOf: (card) => Number(card?.height) || 0,
    pageHeightFor: (pageIndex) => Math.max(0, pagePx - (pageIndex === 0 ? titleReserve : 0)),
  })
  return pages.map((cols, pageIndex) =>
    stationPageHtml(
      pageIndex === 0 ? titleHtml : '',
      cols.map((col) => col.map((card) => card.html)),
    ),
  )
}

export function mmToPx(mm, doc = document) {
  const probe = doc.createElement('div')
  probe.style.cssText = `position:absolute;visibility:hidden;width:${mm}mm;height:0;pointer-events:none`
  doc.body.appendChild(probe)
  const px = probe.getBoundingClientRect().width
  probe.remove()
  return px
}

export function allPageIndexes(pageCount) {
  const n = Math.max(0, Math.floor(Number(pageCount) || 0))
  return Array.from({ length: n }, (_, i) => i)
}

export function normalizeSelectedPages(selected, pageCount) {
  const n = Math.max(0, Math.floor(Number(pageCount) || 0))
  const seen = new Set()
  const picked = []
  for (const raw of Array.isArray(selected) ? selected : []) {
    const index = Number(raw)
    if (!Number.isInteger(index) || index < 0 || index >= n || seen.has(index)) continue
    seen.add(index)
    picked.push(index)
  }
  picked.sort((a, b) => a - b)
  return picked
}

/** Keep in-range checks; fill all pages when nothing valid is selected. */
export function syncSelectedPages(selected, pageCount) {
  const n = Math.max(0, Math.floor(Number(pageCount) || 0))
  const kept = normalizeSelectedPages(selected, n)
  return kept.length ? kept : allPageIndexes(n)
}

export function lastSelectedPageIndex(selected) {
  const list = Array.isArray(selected) ? selected : []
  if (!list.length) return -1
  return Math.max(...list)
}

/** Packing uses an off-screen measure host; skip while print media is emulated or the dialog is open. */
export function shouldRepackPrintPreview(matchMediaFn = globalThis.matchMedia) {
  if (typeof matchMediaFn !== 'function') return true
  try {
    return !matchMediaFn('print')?.matches
  } catch {
    return true
  }
}
