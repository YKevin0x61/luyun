import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const read = (rel) => readFileSync(join(here, rel), 'utf8')

const engine = read('../../../utils/standardPhotoCache.js')
const store = read('../../../stores/standardPhotoCache.js')
const panel = read('../../../components/hygiene/StandardPhotoCachePanel.vue')
const overlay = read('../../../components/hygiene/HygieneStandardOverlay.vue')
const home = read('../HygieneHomeView.vue')

describe('标准图缓存：只提示，手动更新', () => {
  it('引擎里不再有「按网络类型决定要不要自动下载」的判据', () => {
    // wifi 直接下、移动网不足 10MB 也直接下——员工不知情地吃流量，也看不出
    // 「标准图换版了」。这一整套判据连同常量都已移除。
    expect(engine).not.toMatch(/shouldAutoDownload/)
    expect(engine).not.toMatch(/classifyNetwork/)
    expect(engine).not.toMatch(/BATCH_LIMIT_BYTES/)
    expect(engine).not.toMatch(/force = false/)
  })

  it('sync 默认只核对，显式 download 才真拉图', () => {
    expect(engine).toMatch(
      /async function sync\(\{ manifest, download = false, onProgress \} = \{\}\)/,
    )
    expect(engine).toMatch(/if \(!download\) \{/)
    expect(engine).toMatch(/status: 'deferred'/)
  })

  it('store 默认不下载；基线没建好时也不打扰（首次走弹窗）', () => {
    expect(store).toMatch(
      /async checkForUpdates\(\{ download = false, silent = false, manifest = null \} = \{\}\)/,
    )
    expect(store).toMatch(/if \(!this\.stats\.baselineReady && !download\) return null/)
    expect(store).toMatch(/this\.firstPromptOpen/)
  })

  it('后台触发点只核对：切回前台、网络恢复都不带 download', () => {
    expect(panel).toMatch(
      /if \(hiddenAt && Date\.now\(\) - hiddenAt >= 15 \* 60 \* 1000\) \{\s*store\.checkForUpdates\(\)/,
    )
    expect(panel).toMatch(/if \(online\) store\.checkForUpdates\(\)/)
    // 换路由时的核对同样只是核对
    const layout = read('../../hygiene/HygieneAdminLayout.vue')
    expect(layout).toMatch(/standardPhotoCache\.checkForUpdates\(\)/)
    expect(layout).not.toMatch(/checkForUpdates\(\{ download: true \}\)/)
  })

  it('只有用户点「立即更新」/「下载」/「重试下载」才带 download', () => {
    // 面板里那个按钮是「下载 N 张」——后台只提示，动手下载就是靠它。
    expect(panel).toMatch(/@click="downloadStandards"/)
    expect(panel).toMatch(/downloadStandards[\s\S]*?checkForUpdates\(\{ download: true \}\)/)
    expect(panel).toMatch(/下载 \$\{store\.stats\.missingCount\} 张/)
    expect(panel).toMatch(/checkForUpdates\(\{ download: true \}\)/)
    expect(panel).toMatch(/有 \{\{ store\.deferred\.missing\.length \}\} 张标准图待更新/)
    expect(overlay).toMatch(/checkForUpdates\(\{ download: true \}\)/)
  })

  it('换区后重新核对清单，提示才不会是上一个区的图', () => {
    const fn = home.slice(
      home.indexOf('async function pickAssignment'),
      home.indexOf('function openAssignmentPicker'),
    )
    expect(fn).toMatch(/await loadMe\(\)/)
    expect(fn).toMatch(/await standardPhotoCache\.checkForUpdates\(\)/)
  })
})
