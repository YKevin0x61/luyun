import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const FILES = [
  join(here, '../HygieneHomeView.vue'),
  join(here, '../../../components/hygiene/HygieneLiveCamera.vue'),
]

describe('staff daily capture has no album picker', () => {
  it('uses getUserMedia and has no file/image album input', () => {
    const source = FILES.map((path) => readFileSync(path, 'utf8')).join('\n')
    expect(source).toMatch(/getUserMedia/)
    expect(source).not.toMatch(/type=["']file["']/)
    expect(source).not.toMatch(/accept=["']image/)
    expect(source).not.toMatch(/capture=["']environment["']/)
  })
})
