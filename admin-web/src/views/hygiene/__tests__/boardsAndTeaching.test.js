import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))

function readView(name) {
  return readFileSync(join(here, `../${name}`), 'utf8')
}

describe('hygiene boards and teaching UI', () => {
  it('admin boards page lists weekly counts and teaching, with no score fields', () => {
    const source = readView('HygieneBoardsView.vue')
    expect(source).toMatch(/\/api\/hygiene\/admin\/boards/)
    expect(source).toMatch(/\/api\/hygiene\/admin\/teaching/)
    expect(source).toMatch(/人的红黑榜/)
    expect(source).toMatch(/卫生责任区红黑榜/)
    expect(source).toMatch(/卫生教材/)
    expect(source).toMatch(/周一 06:00/)
    expect(source).not.toMatch(/考核分/)
    expect(source).not.toMatch(/score/i)
    expect(source).not.toMatch(/points/i)
  })

  it('staff phone shows both boards and opens teaching comparisons', () => {
    const source = readView('HygieneHomeView.vue')
    expect(source).toMatch(/\/api\/hygiene\/staff\/boards/)
    expect(source).toMatch(/\/api\/hygiene\/staff\/teaching/)
    expect(source).toMatch(/人的红黑榜/)
    expect(source).toMatch(/卫生责任区红黑榜/)
    expect(source).toMatch(/卫生教材/)
    expect(source).not.toMatch(/考核分/)
    expect(source).not.toMatch(/\bscore\b/i)
    expect(source).not.toMatch(/\bpoints\b/i)
  })

  it('daily and deep-clean review can mark a passed pair as teaching', () => {
    const daily = readView('HygieneDailyView.vue')
    const deep = readView('HygieneDeepCleanView.vue')
    expect(daily).toMatch(/\/api\/hygiene\/admin\/teaching/)
    expect(daily).toMatch(/标为卫生教材/)
    expect(deep).toMatch(/\/api\/hygiene\/admin\/teaching/)
    expect(deep).toMatch(/标为卫生教材/)
    expect(daily).not.toMatch(/考核分/)
    expect(deep).not.toMatch(/考核分/)
  })
})
