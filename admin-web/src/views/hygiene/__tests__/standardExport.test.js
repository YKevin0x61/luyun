import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const zones = readFileSync(join(here, '../HygieneZonesView.vue'), 'utf8')
const css = readFileSync(join(here, '../../../../public/hygiene-admin.css'), 'utf8')

describe('导出标准图', () => {
  it('责任区页提供导出入口，且走二进制下载而不是 JSON 请求', () => {
    expect(zones).toMatch(/@click="exportStandards"/)
    expect(zones).toMatch(/导出标准图/)
    expect(zones).toMatch(/正在打包…/)
    expect(zones).toMatch(
      /api\.download\('\/api\/hygiene\/admin\/standards-export', 'hygiene-standards\.zip'\)/,
    )
    // 用 api.get 会把 zip 当 JSON 解析，直接炸在客户端
    expect(zones).not.toMatch(/api\.get\('\/api\/hygiene\/admin\/standards-export'\)/)
  })

  it('导出期间禁用按钮，失败落到页面既有错误提示', () => {
    const fn = zones.slice(
      zones.indexOf('async function exportStandards'),
      zones.indexOf('async function loadZones'),
    )
    expect(fn).toMatch(/if \(exporting\.value\) return/)
    expect(fn).toMatch(/exporting\.value = true/)
    expect(fn).toMatch(/finally \{\s*exporting\.value = false/)
    expect(fn).toMatch(/errorText\.value = err\.message \|\| '导出标准图失败'/)
    expect(zones).toMatch(/:disabled="exporting"/)
  })

  it('两个按钮并排且留出间距', () => {
    expect(zones).toMatch(/class="roster-head-actions"/)
    expect(css).toMatch(/\.hygiene-admin \.roster-head-actions\s*\{[^}]*display: flex/)
    expect(css).toMatch(/\.hygiene-admin \.roster-head-actions\s*\{[^}]*gap:/)
  })
})
