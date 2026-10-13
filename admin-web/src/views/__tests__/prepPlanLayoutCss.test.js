import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const VIEW = join(here, '../PrepPlanView.vue')

function scopedCss(source) {
  const start = source.indexOf('<style')
  const openEnd = source.indexOf('>', start)
  const close = source.indexOf('</style>', openEnd)
  return source.slice(openEnd + 1, close)
}

function mediaBody(css, query) {
  const needle = `@media (${query}) {`
  const start = css.indexOf(needle)
  if (start < 0) return ''
  const brace = css.indexOf('{', start)
  let depth = 0
  for (let i = brace; i < css.length; i++) {
    if (css[i] === '{') depth++
    else if (css[i] === '}') {
      depth--
      if (depth === 0) return css.slice(brace + 1, i).replace(/\s+/g, '')
    }
  }
  return ''
}

describe('PrepPlanView mobile layout', () => {
  const source = readFileSync(VIEW, 'utf8')
  const css = scopedCss(source)
  const compact = css.replace(/\s+/g, '')
  const phone = mediaBody(css, 'max-width: 720px')
  const desktop = mediaBody(css, 'min-width: 721px')

  it('keeps kitchen controls at 44px and 16px text to avoid iOS zoom', () => {
    expect(compact).toContain('.prep-chip,.prep-refresh,.prep-export{min-height:44px')
    expect(compact).toContain('.prep-datetime{min-height:44px;width:100%;min-width:0;font-size:16px')
    expect(compact).toContain('.prep-auxsummary,.prep-row-moresummary{cursor:pointer;min-height:44px')
  })

  it('does not force datetime fields past a phone width', () => {
    expect(compact).toContain('.prep-datetime{min-height:44px;width:100%;min-width:0;font-size:16px')
    expect(compact.includes('.prep-datetime{min-height:44px;min-width:220px')).toBe(false)
  })

  it('uses a wrapping chip collection and the row / stamp layout', () => {
    expect(compact).toContain('.prep-toolbar-row{display:flex;flex-wrap:wrap')
    expect(compact).toContain('.prep-row-main{display:grid;grid-template-columns:minmax(0,1fr)auto')
    expect(compact).toContain('.prep-stamp{')
  })

  it('hides the duplicate page title on phones and pads modal safe area', () => {
    expect(phone).toContain('.prep-title{position:absolute')
    expect(compact).toContain('env(safe-area-inset-bottom,0px)')
    expect(compact).toContain('.prep-plan:deep(.modal-box){width:min(600px,100%)!important')
  })

  it('keeps refresh / export in a row on desktop', () => {
    expect(desktop).toContain('.prep-action-row.prep-refresh,.prep-action-row.prep-export{width:auto')
    expect(compact).toContain('prefers-reduced-motion:reduce')
    expect(compact).toContain(':focus-visible')
  })

  it('写控件那一套样式也不再留着', () => {
    for (const dead of [
      '.prep-row-actions',
      '.prep-register',
      '.prep-qty-field',
      '.prep-record',
      '.prep-extra',
      '.prep-undo',
      '.prep-discard',
      '.prep-no-master',
      '.prep-metrics',
      '.prep-expiring',
      '.prep-batches',
      'is-danger',
    ]) {
      expect(compact.includes(dead), `${dead} 的样式还在`).toBe(false)
    }
  })
})
