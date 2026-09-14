import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const source = readFileSync(join(here, '../HygieneDeepCleanView.vue'), 'utf8')

describe('admin deep-clean config', () => {
  it('lets super add named items without a standard photo and set one overdue clock', () => {
    expect(source).toMatch(/\/api\/hygiene\/admin\/deep-clean\/items/)
    expect(source).toMatch(/\/api\/hygiene\/admin\/deep-clean\/clock/)
    expect(source).toMatch(/\/api\/hygiene\/admin\/deep-clean\/calendar/)
    expect(source).toMatch(/aria-label="专项卫生逾期点"/)
    expect(source).toMatch(/LuyunTimePicker/)
    expect(source).toMatch(/LuyunDatePicker/)
    expect(source).not.toMatch(/:dark="false"/)
    expect(source).toMatch(/<LuyunDatePicker[\s\S]*?\bdark\b/)
    expect(source).not.toMatch(/type="time"/)
    expect(source).not.toMatch(/type="date"/)
    expect(source).toMatch(/HYGIENE_WEEKDAYS/)
    expect(source).toMatch(/不要标准图/)
    expect(source).not.toMatch(/type=["']file["']/)
    expect(source).not.toMatch(/accept=["']image/)
  })
})
