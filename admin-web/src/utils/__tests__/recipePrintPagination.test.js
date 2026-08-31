import { describe, expect, it } from 'vitest'
import {
  A4_CONTENT_HEIGHT_MM,
  A4_HEIGHT_MM,
  A4_PAD_X_MM,
  A4_PAD_Y_MM,
  A4_WIDTH_MM,
  PRINT_CARD_GAP_MM,
  PRINT_COLUMN_COUNT,
  packItemsIntoPages,
  packNewspaperPages,
  paginateStationCards,
  shouldRepackPrintPreview,
  stationPageHtml,
} from '../recipePrintPagination.js'

describe('A4 sheet metrics', () => {
  it('uses portrait A4 with 8mm vertical padding', () => {
    expect(A4_WIDTH_MM).toBe(210)
    expect(A4_HEIGHT_MM).toBe(297)
    expect(A4_PAD_Y_MM).toBe(8)
    expect(A4_PAD_X_MM).toBe(10)
    expect(A4_CONTENT_HEIGHT_MM).toBe(281)
  })
})

describe('packItemsIntoPages', () => {
  it('packs until fits() fails, then starts a new page', () => {
    const fits = (list) => list.length <= 3
    expect(packItemsIntoPages(['a', 'b', 'c', 'd', 'e'], fits)).toEqual([
      ['a', 'b', 'c'],
      ['d', 'e'],
    ])
  })

  it('keeps a single oversized item on its own page', () => {
    const fits = () => false
    expect(packItemsIntoPages(['huge', 'a'], fits)).toEqual([['huge'], ['a']])
  })

  it('returns one empty page when there are no items', () => {
    expect(packItemsIntoPages([], () => true)).toEqual([[]])
  })
})

describe('stationPageHtml', () => {
  it('wraps cards in three newspaper columns', () => {
    const html = stationPageHtml('<div class="sop-doc-title">T</div>', [['<article class="recipe-card">1</article>'], [], []])
    expect(html).toContain('sop-doc-title')
    expect(html).toContain('sop-section-grid')
    expect(html.match(/sop-print-col/g)?.length).toBe(PRINT_COLUMN_COUNT)
    expect(html).toContain('recipe-card')
  })
})

describe('packNewspaperPages', () => {
  const ids = (pages) => pages.map((cols) => cols.map((col) => col.map((item) => item.id)))

  it('stacks the next short card under the shortest column instead of opening a new row', () => {
    const items = [
      { id: 'a', h: 20 },
      { id: 'b', h: 20 },
      { id: 'c', h: 90 },
      { id: 'd', h: 20 },
    ]
    expect(ids(packNewspaperPages(items, {
      columns: 3,
      gap: 0,
      heightOf: (item) => item.h,
      pageHeightFor: () => 100,
    }))).toEqual([[['a', 'd'], ['b'], ['c']]])
  })

  it('starts a new page when no column can take the next card', () => {
    const items = [
      { id: 'a', h: 80 },
      { id: 'b', h: 80 },
      { id: 'c', h: 80 },
      { id: 'd', h: 80 },
    ]
    expect(ids(packNewspaperPages(items, {
      columns: 3,
      gap: 0,
      heightOf: (item) => item.h,
      pageHeightFor: () => 100,
    }))).toEqual([
      [['a'], ['b'], ['c']],
      [['d'], [], []],
    ])
  })

  it('keeps an oversized first card on the page so a single card still prints', () => {
    const pages = packNewspaperPages([{ id: 'huge', h: 500 }, { id: 'a', h: 10 }], {
      columns: 3,
      gap: 0,
      heightOf: (item) => item.h,
      pageHeightFor: () => 100,
    })
    expect(ids(pages)).toEqual([[['huge'], ['a'], []]])
  })

  it('counts the column gap when deciding whether a card still fits', () => {
    const items = [
      { id: 'a', h: 40 },
      { id: 'b', h: 40 },
    ]
    expect(ids(packNewspaperPages(items, {
      columns: 1,
      gap: 10,
      heightOf: (item) => item.h,
      pageHeightFor: () => 80,
    }))).toEqual([[['a']], [['b']]])
  })
})

describe('paginateStationCards', () => {
  it('reserves title height on the first page only', () => {
    const cards = [
      { html: '<article class="recipe-card">a</article>', height: 80 },
      { html: '<article class="recipe-card">b</article>', height: 80 },
    ]
    const htmls = paginateStationCards({
      titleHtml: '<div class="sop-doc-title">T</div>',
      cards,
      pagePx: 100,
      gapPx: 0,
      titleReserve: 30,
    })
    expect(htmls).toHaveLength(2)
    expect(htmls[0]).toContain('sop-doc-title')
    expect(htmls[0]).toContain('>a</article>')
    expect(htmls[1]).not.toContain('sop-doc-title')
    expect(htmls[1]).toContain('>b</article>')
  })

  it('keeps the print column gap in mm matching CSS .2cm', () => {
    expect(PRINT_CARD_GAP_MM).toBe(2)
  })
})

describe('shouldRepackPrintPreview', () => {
  it('skips packing when print media is active', () => {
    expect(shouldRepackPrintPreview(() => ({ matches: true }))).toBe(false)
    expect(shouldRepackPrintPreview(() => ({ matches: false }))).toBe(true)
  })
})
