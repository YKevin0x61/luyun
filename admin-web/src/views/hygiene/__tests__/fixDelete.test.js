import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const fix = readFileSync(join(here, '../HygieneFixView.vue'), 'utf8')
const home = readFileSync(join(here, '../HygieneHomeView.vue'), 'utf8')

describe('admin fix ticket delete', () => {
  it('deletes a fix ticket with a Chinese confirm from the super-admin page', () => {
    expect(fix).toMatch(/ConfirmDialog/)
    expect(fix).toMatch(/删除整改单/)
    expect(fix).toMatch(/会连同开单原图、回拍和逾期记录一起删除/)
    expect(fix).toMatch(/api\.delete\(`\/api\/hygiene\/admin\/fix\/\$\{row\.id\}`\)/)
  })

  it('does not expose fix deletion on the staff phone', () => {
    expect(home).not.toMatch(/\/api\/hygiene\/admin\/fix/)
    expect(home).not.toMatch(/deleteFix/)
  })
})
