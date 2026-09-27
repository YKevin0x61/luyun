import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))

function read(rel) {
  return readFileSync(join(here, rel), 'utf8')
}

describe('hygiene roster enable', () => {
  it('shows an enable action for disabled staff and calls the enable endpoint', () => {
    const roster = read('../HygieneRosterView.vue')
    expect(roster).toMatch(/async function enable\(row\)/)
    expect(roster).toMatch(/\/api\/hygiene\/admin\/roster\/\$\{row\.id\}\/enable/)
    expect(roster).toMatch(/v-if="row\.disabled"/)
    expect(roster).toMatch(/>启用</)
    expect(roster).toMatch(/可重新启用/)
    expect(roster).toMatch(/v-model="drafts\[row\.id\]\.name"/)
    expect(roster).toMatch(/name: draft\.name/)
    expect(roster).toMatch(/drafts\[row\.id\]\.zone_id/)
    // 改派（票 10 起）写的是排班的单日覆盖：卫生那条 /assignment 接口现在固定 403，
    // 这一行仍然要留着「今天在哪个班、哪个区」的入口。
    expect(roster).toMatch(/\/api\/scheduling\/overrides\//)
    expect(roster).toMatch(/改今天/)
  })
})
