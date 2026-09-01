import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const CSS_FILES = {
  'admin-web/public/recipe.css': join(here, '../../../public/recipe.css'),
  'public/recipe.css': join(here, '../../../../public/recipe.css'),
}

function mediaBodies(css, query) {
  const needle = `@media (${query}){`
  const bodies = []
  let from = 0
  while (true) {
    const start = css.indexOf(needle, from)
    if (start < 0) break
    const brace = css.indexOf('{', start)
    let depth = 0
    let end = -1
    for (let i = brace; i < css.length; i++) {
      if (css[i] === '{') depth++
      else if (css[i] === '}') {
        depth--
        if (depth === 0) {
          end = i
          break
        }
      }
    }
    if (end < 0) break
    bodies.push(css.slice(brace + 1, end).replace(/\s+/g, ''))
    from = end + 1
  }
  return bodies.join('')
}

describe.each(Object.entries(CSS_FILES))('%s 901px reader chrome split', (label, path) => {
  const css = readFileSync(path, 'utf8')
  const compact = css.replace(/\s+/g, '')
  const desktop = mediaBodies(css, 'min-width:901px')
  const mobile = mediaBodies(css, 'max-width:900px')

  it('hides the bottom bar at min-width 901px and keeps header-actions visible', () => {
    expect(css).toContain('@media (min-width:901px)')
    expect(desktop).toContain('.sop-bottom-bar{display:none')
    expect(desktop.includes('.sop-header-actions{display:none')).toBe(false)
    expect(compact).toContain('.sop-header-actions{')
  })

  it('shows a mobile-only bottom bar with safe-area padding', () => {
    expect(compact).toContain('.sop-bottom-bar{')
    expect(compact).toContain('env(safe-area-inset-bottom)')
    expect(desktop).toContain('.sop-bottom-bar{display:none')
  })

  it('hides the aside TOC and bulky header tools below 901px', () => {
    expect(mobile).toContain('.sop-layout>.sop-toc{display:none')
    expect(mobile).toContain('.sop-header-desktop{display:none')
    expect(mobile).toContain('.sop-bottom-bar{display:flex')
  })

  it('shows reader card actions as a group and keeps them visible on coarse pointers', () => {
    expect(compact).toContain('.recipe-card-actions{')
    expect(compact).toContain('.markdown-body.recipe-card:hover.recipe-card-actions')
    const coarse = mediaBodies(css, 'pointer:coarse')
    expect(coarse).toContain('.recipe-card-actions{opacity:1')
  })

  it('lets desktop reader cards show a grab cursor and strips it on mobile', () => {
    expect(compact).toContain('.sop-reader.recipe-card.is-draggable{cursor:grab')
    expect(mobile).toContain('.sop-reader.recipe-card.is-draggable,.sop-reader.recipe-card.is-draggable:active{cursor:auto')
    const coarse = mediaBodies(css, 'pointer:coarse')
    expect(coarse).toContain('.sop-reader.recipe-card.is-draggable,.sop-reader.recipe-card.is-draggable:active{cursor:auto')
  })

  it('contains a drawer that traps overscroll', () => {
    expect(compact).toContain('.sop-drawer{')
    expect(compact).toContain('overscroll-behavior:contain')
  })
})

describe.each(Object.entries(CSS_FILES))('%s compact reader fills the page width', (label, path) => {
  const css = readFileSync(path, 'utf8')
  const compact = css.replace(/\s+/g, '')
  const phone = mediaBodies(css, 'max-width:640px')

  it('lays compact cards in a filling grid, not a single CSS column', () => {
    expect(compact).toContain('.sop-density-compact.markdown-body.sop-section-grid{display:grid')
    expect(compact).toContain('repeat(auto-fill,minmax(min(100%,13rem),1fr))')
    expect(compact.includes('display:block;column-gap:.6rem;columns:13rem')).toBe(false)
    expect(compact.includes('.sop-density-compact.markdown-body.sop-section-grid{columns:13rem')).toBe(false)
  })

  it('keeps a single card column on narrow phones', () => {
    expect(phone).toContain('.sop-density-compact.markdown-body.sop-section-grid{grid-template-columns:1fr')
    expect(phone.includes('.sop-density-compact.markdown-body.sop-section-grid{columns:1')).toBe(false)
  })

  it('visually hides the in-document station title on the reader, keeping the toolbar chip', () => {
    const start = compact.indexOf('.sop-reader.markdown-body.sop-doc-title{')
    expect(start).toBeGreaterThan(0)
    const block = compact.slice(start, compact.indexOf('}', start))
    expect(block).toContain('clip:rect(0,0,0,0)')
  })

  it('only reserves the desktop TOC column when the TOC is shown', () => {
    expect(compact).toContain('.sop-layout.sop-layout--with-toc{grid-template-columns:13remminmax(0,1fr)')
  })
})

describe.each(Object.entries(CSS_FILES))('%s recipe form editor chrome', (label, path) => {
  const css = readFileSync(path, 'utf8')
  const compact = css.replace(/\s+/g, '')

  it('sizes the editor from the modal container, not only the viewport', () => {
    expect(compact).toContain('container-type:inline-size')
    expect(compact).toContain('container-name:recipe-form')
    expect(compact).toContain('@containerrecipe-form(min-width:48rem)')
  })

  it('keeps save actions on one row and stops the name field from wrapping the new flag', () => {
    expect(compact).toContain('.recipe-form-chrome{position:relative;display:flex;flex-wrap:nowrap')
    expect(css).toContain('.recipe-form-name-row .form-input{flex:1 1 0;width:auto')
  })

  it('treats empty add rows as ticket lines, not dashed drop targets', () => {
    expect(compact).toContain('.recipe-form-empty{appearance:none')
    const emptyStart = compact.indexOf('.recipe-form-empty{appearance:none')
    const emptyEnd = compact.indexOf('.recipe-form-empty:focus-visible')
    const emptyBlock = compact.slice(emptyStart, emptyEnd)
    expect(emptyEnd).toBeGreaterThan(emptyStart)
    expect(emptyBlock.includes('dashed')).toBe(false)
  })

  it('renders the editor as a pass window over a kitchen ticket', () => {
    expect(compact).toContain('.recipe-form-overlay')
    expect(compact).toContain('--pass:')
    expect(compact).toContain('--chop:')
    expect(compact).toContain('transform:rotate(-8deg)')
  })
})

describe.each(Object.entries(CSS_FILES))('%s recipe card body type size', (label, path) => {
  const css = readFileSync(path, 'utf8')
  const compact = css.replace(/\s+/g, '')

  it('sets card body (ingredients, steps, tips) to 12px and tables inherit it', () => {
    expect(compact).toContain('--reader-fs:12px')
    expect(css).toContain('.markdown-body .recipe-card-body{flex:1 1 auto;padding:.46rem .6rem .6rem;font-size:var(--reader-fs);line-height:1.45;font-weight:700}')
    expect(css).toContain('.markdown-body .recipe-card-body table{font-size:inherit}')
    expect(css).toContain('.sop-density-compact .markdown-body .recipe-card-body{padding:.42rem .55rem .5rem;font-size:var(--reader-fs);line-height:1.45;font-weight:700}')
    expect(compact).toContain('font-weight:700;-webkit-text-size-adjust:100%')
    expect(compact.includes('font-size:calc(var(--reader-fs)-.5px)')).toBe(false)
  })
})

describe.each(Object.entries(CSS_FILES))('%s print preview packs 3 newspaper columns', (label, path) => {
  const css = readFileSync(path, 'utf8')
  const printStart = css.indexOf('@media print{')
  const previewCss = css.slice(0, printStart)
  const printCss = css.slice(printStart).replace(/\s+/g, '')

  it('keeps a 3-col newspaper stack, without CSS column-count or masonry', () => {
    const compactPreview = previewCss.replace(/\s+/g, '')
    expect(previewCss).toContain('body.sop-print-preview-page .markdown-body .sop-section-grid')
    expect(compactPreview).toContain('grid-template-columns:repeat(3,1fr)')
    expect(compactPreview).toContain('body.sop-print-preview-page.markdown-body.sop-print-col{display:flex;flex-direction:column;gap:.2cm')
    expect(compactPreview).toContain('.sop-print-preview-measure.sop-print-measure-col{width:calc((100%-.56cm)/3)')
    expect(compactPreview.includes('column-count:3')).toBe(false)
    expect(compactPreview.includes('column-fill:balance')).toBe(false)
    expect(previewCss).toContain('body.sop-print-preview-page .markdown-body .sop-section-head')
  })

  it('locks print-preview cards to light paper tokens matching print type', () => {
    const start = previewCss.indexOf('body.sop-print-preview-page{')
    const block = previewCss.slice(start, start + 420)
    const compactPreview = previewCss.replace(/\s+/g, '')
    expect(start).toBeGreaterThan(0)
    expect(block).toContain('color-scheme:light')
    expect(block).toContain('--surface:#ffffff')
    expect(block).toContain('--ink:#1f1d18')
    expect(previewCss).toContain('background:#fff;color:#1f1d18')
    expect(compactPreview).toContain('.recipe-card-headh3{font-size:9.5pt;padding:.1cm.2cm')
    expect(compactPreview).toContain('.recipe-card-body{font-size:8.5pt;line-height:1.35;padding:.1cm.2cm.15cm;font-weight:700')
    expect(compactPreview).toContain('font-size:11pt;line-height:1.4;font-weight:700;--reader-fs:8.5pt')
  })

  it('packs print section grids as a 3-col newspaper stack', () => {
    expect(printCss).toContain('grid-template-columns:repeat(3,1fr)')
    expect(printCss).toContain('body.sop-print-preview-page.markdown-body.sop-print-col{display:flex;flex-direction:column;gap:.2cm')
    expect(printCss.includes('column-count:3')).toBe(false)
    expect(printCss).toContain('.markdown-body.sop-section-head{display:none')
  })

  it('keeps recipe cards unbroken and content-height in print', () => {
    const idx = printCss.indexOf('.markdown-body.recipe-card')
    const slice = printCss.slice(idx, idx + 400)
    expect(slice).toContain('break-inside:avoid')
    expect(slice).toContain('page-break-inside:avoid')
    expect(slice).toContain('height:auto')
  })

  it('paginates the screen preview into stacked A4 sheets and prints those same sheets', () => {
    const compactPreview = previewCss.replace(/\s+/g, '')
    expect(compactPreview).toContain('.sop-print-preview-sheet{position:relative;width:210mm;height:297mm')
    expect(compactPreview).toContain('flex-direction:column')
    expect(compactPreview).toContain('.sop-print-preview-measure-host{position:absolute')
    expect(printCss).toContain('.sop-print-preview-measure-host{display:none')
    expect(printCss).toContain('.sop-print-preview-sheet-wrap{display:block')
    expect(printCss).toContain('break-after:page')
    expect(printCss).toContain('page-break-after:always')
    expect(printCss).toContain('.sop-print-preview-sheet.is-print-skipped{display:none!important}')
    expect(printCss).toContain('.sop-print-preview-sheet.is-print-tail')
  })
})

describe('RecipeDetailView reader body binding', () => {
  const src = readFileSync(join(here, '../../views/recipe/RecipeDetailView.vue'), 'utf8')

  it('builds the TOC after loading finishes so the body node exists', () => {
    const start = src.indexOf('async function load()')
    const end = src.indexOf('function afterContentRendered')
    const load = src.slice(start, end)
    expect(load.lastIndexOf('afterContentRendered()')).toBeGreaterThan(load.lastIndexOf('loading.value = false'))
  })

  it('injects copy plus logged-in delete, toggle, and edit actions', () => {
    expect(src).toContain('injectCardActions')
    expect(src).toContain('data-reader-action')
    expect(src).toContain('RecipeFormModal')
    expect(src).toContain('deleteRecipeConfirmCopy')
  })

  it('only adds the desktop TOC column when the TOC is visible', () => {
    expect(src).toContain("sop-layout--with-toc")
    expect(src).toContain('tocVisible')
  })
})
