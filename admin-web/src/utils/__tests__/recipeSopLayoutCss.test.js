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
  })

  it('contains a drawer that traps overscroll', () => {
    expect(compact).toContain('.sop-drawer{')
    expect(compact).toContain('overscroll-behavior:contain')
  })
})
