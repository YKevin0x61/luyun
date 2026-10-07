import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))

function read(rel) {
  return readFileSync(join(here, rel), 'utf8')
}

// 停用 / 启用这条链路（2026-10 改版后动作搬进抽屉）。
describe('hygiene roster enable', () => {
  it('shows an enable action for disabled staff and calls the enable endpoint', () => {
    const roster = read('../HygieneRosterView.vue')
    expect(roster).toMatch(/async function enableRow/)
    expect(roster).toMatch(/\/api\/hygiene\/admin\/roster\/\$\{row\.id\}\/enable/)
    expect(roster).toMatch(/v-if="drawerRow\.disabled"/)
    expect(roster).toMatch(/>启用</)
    // 姓名仍然可改、仍然一起发（改版只把它从列表行搬进了抽屉）。
    expect(roster).toMatch(/v-model="draft\.name"/)
    expect(roster).toMatch(/name: String\(source\.name \|\| ''\)\.trim\(\)/)
  })

  it('启用不看批准门槛：账上的门槛只挂在「批准」那颗按钮上', () => {
    const roster = read('../HygieneRosterView.vue')
    // 批准按钮：忙 或 两项不齐 → 不可点。
    expect(roster).toMatch(/:disabled="busy \|\| !canApprove"/)
    // 启用按钮：只有忙这一道（恢复不是新入职）。
    expect(roster).toMatch(/:disabled="busy"\s+@click="enableRow"/)
  })
})
