import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const source = readFileSync(join(here, '../HygieneZonesView.vue'), 'utf8')

describe('admin overdue clocks', () => {
  it('lets super configure day and night clocks over HTTP, not hardcoded 15:00 only', () => {
    expect(source).toMatch(/\/api\/hygiene\/admin\/overdue-clocks/)
    expect(source).toMatch(/aria-label="白班日常逾期点"/)
    expect(source).toMatch(/aria-label="夜班日常逾期点"/)
    expect(source).toMatch(/type="time"/)
    expect(source).toMatch(/method:\s*['"]PATCH['"]|api\.patch/)
  })
})
