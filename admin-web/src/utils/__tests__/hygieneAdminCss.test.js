import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const CSS_FILES = {
  'admin-web/public/hygiene-admin.css': join(here, '../../../public/hygiene-admin.css'),
  'public/hygiene-admin.css': join(here, '../../../../public/hygiene-admin.css'),
}

describe.each(Object.entries(CSS_FILES))('%s hygiene admin chrome', (_label, path) => {
  const css = readFileSync(path, 'utf8')

  it('keeps tokens and chrome scoped to the hygiene admin shell', () => {
    expect(css).toContain('.hygiene-admin,')
    expect(css).toContain('.hygiene-staff {')
    expect(css).toContain('--hy-pool: #0f6f78')
    expect(css).toContain('.hygiene-admin .hy-tabbar')
    expect(css).toContain('.hygiene-work .hy-tabbar')
    expect(css).toContain('.hygiene-admin .hy-tab.router-link-active')
    expect(css).toContain('.hygiene-staff .hy-staff-card')
    expect(css).toContain('prefers-reduced-motion')
  })
})
