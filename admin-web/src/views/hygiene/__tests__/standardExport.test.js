import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const zones = readFileSync(join(here, '../HygieneZonesView.vue'), 'utf8')
const css = readFileSync(join(here, '../../../../public/hygiene-admin.css'), 'utf8')

describe('导出标准图', () => {
  it('走任务式端点：先 POST 拿 job_id，再轮询，最后才下载', () => {
    // 同步请求会让浏览器干等十几秒（几十张原图要解码重编码），界面上没有反馈。
    expect(zones).toMatch(/api\.post\(\s*`\/api\/hygiene\/admin\/standards-export\/jobs\?size=\$\{exportSize\.value\}`/)
    expect(zones).toMatch(/api\.get\(`\/api\/hygiene\/admin\/standards-export\/jobs\/\$\{job\.job_id\}`\)/)
    expect(zones).toMatch(/api\.download\(\s*`\/api\/hygiene\/admin\/standards-export\/jobs\/\$\{job\.job_id\}\/download`/)
    // 不能再用那个一次性返回 zip 的老端点
    expect(zones).not.toMatch(/api\.download\('\/api\/hygiene\/admin\/standards-export'/)
  })

  it('轮询里 failed 立刻抛错，done 才跳出', () => {
    const fn = zones.slice(
      zones.indexOf('async function exportStandards'),
      zones.indexOf('const exportPercent'),
    )
    expect(fn).toMatch(/if \(state\.state === 'failed'\) throw new Error\(/)
    expect(fn).toMatch(/if \(state\.state === 'done'\) break/)
    expect(fn).toMatch(/exportProgress\.value = \{ done: state\.done, total: state\.total/)
    expect(fn).toMatch(/finally \{\s*exporting\.value = false/)
  })

  it('界面上看得到进度：按钮文案 + 细进度条', () => {
    expect(zones).toMatch(/正在打包 \$\{progress\.done\}\/\$\{progress\.total\}…/)
    expect(zones).toMatch(/正在下载…/)
    expect(zones).toMatch(/正在准备…/)
    expect(zones).toMatch(/class="export-progress"/)
    expect(zones).toMatch(/:style="\{ width: exportPercent \+ '%' \}"/)
    expect(css).toMatch(/\.hygiene-admin \.export-progress-bar\s*\{/)
  })

  it('默认导出预览图，可切原图', () => {
    // 门店 36 项原图合计 80+ MB；预览图 1600px，页面显示 440px、打印也够。
    expect(zones).toMatch(/const exportSize = ref\('preview'\)/)
    expect(zones).toMatch(/<option value="preview">/)
    expect(zones).toMatch(/<option value="original">/)
    expect(css).toMatch(/\.hygiene-admin \.export-size\s*\{/)
  })

  it('导出期间禁用按钮与规格选择', () => {
    expect(zones).toMatch(/:disabled="exporting"/)
    expect(zones).toMatch(/<select v-model="exportSize" :disabled="exporting"/)
  })
})
