import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const FILES = [
  join(here, '../HygieneHomeView.vue'),
  join(here, '../HygieneFixView.vue'),
  join(here, '../../../components/hygiene/HygieneLiveCamera.vue'),
]

describe('staff daily capture has no album picker', () => {
  it('uses getUserMedia and has no file/image album input', () => {
    const source = FILES.slice(0, 1).concat(FILES[2]).map((path) => readFileSync(path, 'utf8')).join('\n')
    expect(source).toMatch(/getUserMedia/)
    expect(source).not.toMatch(/type=["']file["']/)
    expect(source).not.toMatch(/accept=["']image/)
    expect(source).not.toMatch(/capture=["']environment["']/)
    expect(source).toMatch(/deep-clean/)
    expect(source).toMatch(/拍清理前/)
  })
})

describe('fix tickets use live camera only', () => {
  it('staff and admin open/reshoot have no album and require getUserMedia', () => {
    const home = readFileSync(FILES[0], 'utf8')
    const admin = readFileSync(FILES[1], 'utf8')
    const camera = readFileSync(FILES[2], 'utf8')
    const source = `${home}\n${admin}\n${camera}`
    expect(source).toMatch(/getUserMedia/)
    expect(admin).not.toMatch(/type=["']file["']/)
    expect(admin).not.toMatch(/accept=["']image/)
    expect(home).toMatch(/开整改单/)
    expect(home).toMatch(/staff\/fix/)
    expect(admin).toMatch(/admin\/fix/)
    expect(admin).toMatch(/这台电脑没有相机，不能开整改单/)
    expect(admin).toMatch(/没有相册/)
    expect(home).toMatch(/镜头不叠图|先看开单原图/)
  })
})
