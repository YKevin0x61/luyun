import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

// 复盘页的基线只能来自服务端 measure：页面与 CSV 都不许再拿 suggested_qty / final_qty
// 自己挑一个，也不许再算一次「刚好」判定（0.05 容差那套已经删了）。
const here = dirname(fileURLToPath(import.meta.url))
const VIEW = join(here, '../PrepReviewView.vue')
const CSV = join(here, '../../utils/prepReviewCsv.js')
const read = (path) => readFileSync(path, 'utf8')

describe('复盘两条基线只由服务端选', () => {
  it('页面不再读旧字段，也不自己判「刚好」', () => {
    const src = read(VIEW)
    expect(src).not.toMatch(/suggested_qty|final_qty/)
    expect(src).not.toMatch(/function\s+verdict\s*\(/)
  })

  it('CSV 只导出服务端给的两套数', () => {
    const src = read(CSV)
    expect(src).not.toMatch(/suggested_qty|final_qty/)
  })
})
