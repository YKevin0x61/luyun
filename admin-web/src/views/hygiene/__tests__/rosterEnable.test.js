import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))

function read(rel) {
  return readFileSync(join(here, rel), 'utf8')
}

// 登记离职 / 取消离职这条链路（2026-10-29 取代「停用 / 启用」；动作在抽屉底部）。
describe('hygiene roster 取消离职', () => {
  it('已离职的人给「取消离职」，打的是 cancel-resignation', () => {
    const roster = read('../HygieneRosterView.vue')
    expect(roster).toMatch(/async function confirmCancelResignation/)
    expect(roster).toMatch(/\/api\/hygiene\/admin\/roster\/\$\{row\.id\}\/cancel-resignation/)
    expect(roster).toMatch(/v-if="drawerRow\.resigned"/)
    expect(roster).toMatch(/>取消离职</)
    // 姓名仍然可改、仍然一起发（改版只把它从列表行搬进了抽屉）。
    expect(roster).toMatch(/v-model="draft\.name"/)
    expect(roster).toMatch(/name: String\(source\.name \|\| ''\)\.trim\(\)/)
  })

  it('取消离职不看批准门槛：账上的门槛只挂在「批准」那颗按钮上', () => {
    const roster = read('../HygieneRosterView.vue')
    // 批准按钮：忙 或 两项不齐 → 不可点。
    expect(roster).toMatch(/:disabled="busy \|\| !canApprove"/)
    // 取消离职按钮：只有忙这一道（恢复不是新入职）。
    expect(roster).toMatch(/:disabled="busy"\s+@click="askCancelResignation"/)
  })

  it('登记离职是两步：先 dry_run 拿计数，再真写', () => {
    const roster = read('../HygieneRosterView.vue')
    expect(roster).toMatch(/async function planResign/)
    expect(roster).toMatch(/dry_run: true/)
    expect(roster).toMatch(/async function confirmResign/)
    expect(roster).toMatch(/assignments_to_remove/)
  })
})
