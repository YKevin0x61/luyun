import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

// 图表组件是纯展示（几何在 utils/systemHealthCharts.js 里已单测），
// 这里按仓库既有做法做「源码契约」检查：无障碍属性、固定高度、不引入 echarts、窄屏不横向滚动。
const here = dirname(fileURLToPath(import.meta.url))
const SYSTEM_DIR = join(here, '..')
const VIEWS_DIR = join(here, '../../../views')

function read(name) {
  return readFileSync(join(SYSTEM_DIR, name), 'utf8')
}
function compact(source) {
  return source.replace(/\s+/g, '')
}

const healthSummary = read('HealthSummary.vue')
const bulletGauge = read('BulletGauge.vue')
const usageBar = read('UsageBar.vue')
const readinessGrid = read('ReadinessGrid.vue')
const reconcileProgress = read('ReconcileProgress.vue')
const setupView = readFileSync(join(VIEWS_DIR, 'SetupView.vue'), 'utf8')

describe('系统健康图表组件契约', () => {
  it('三类量表都用 role="img" + aria-label，且数值/阈值有可见文字', () => {
    const gauge = compact(bulletGauge)
    expect(gauge).toContain('role="img"')
    expect(gauge).toContain(':aria-label="ariaLabel"')
    expect(gauge).toContain('aria-hidden="true"')
    // 数值与阈值文字必须渲染，状态不能只靠颜色
    expect(gauge).toContain('{{valueText}}')
    expect(gauge).toContain('{{thresholdText}}')
    expect(gauge).toContain('{{levelLabel')
    expect(compact(usageBar)).toContain('role="img"')
    expect(compact(usageBar)).toContain(':aria-label="ariaLabel"')
    expect(compact(usageBar)).toContain('{{display}}')
  })

  it('对账进度用 role="progressbar" 并暴露 aria-valuenow/min/max', () => {
    const bar = compact(reconcileProgress)
    expect(bar).toContain('role="progressbar"')
    expect(bar).toContain(':aria-valuenow=')
    expect(bar).toContain(':aria-valuemin="0"')
    expect(bar).toContain(':aria-valuemax=')
    expect(bar).toContain(':aria-label="state.ariaLabel"')
  })

  it('图表预留固定高度与最小条宽，避免加载抖动和小值消失', () => {
    expect(compact(bulletGauge)).toContain('min-height:118px')
    expect(compact(bulletGauge)).toContain('.bullet__caption{margin:4px00;min-height:1.5em')
    expect(compact(usageBar)).toContain('min-width:3px')
    expect(compact(reconcileProgress)).toContain('min-width:3px')
  })

  it('就绪检查用符号 + 文字表达，未就绪时摊开 details 原文', () => {
    const grid = read('ReadinessGrid.vue')
    expect(grid).toContain('{{ item.symbol }}')
    expect(grid).toContain('{{ item.statusText }}')
    expect(grid).toContain('hasFailure && details.length')
    expect(grid).toContain('未就绪详情')
  })

  it('重新检查按钮是原生 button 且触控目标 >= 44px，键盘可达', () => {
    const summary = compact(healthSummary)
    expect(summary).toContain('type="button"')
    expect(summary).toContain('min-height:44px')
    expect(summary).toContain('min-width:44px')
    expect(summary).toContain(':focus-visible')
    expect(summary).toContain('@click="$emit(\'refresh\')"')
  })

  it('概览条同时给出结论文字、版本、运行时长与失败提示', () => {
    const summary = compact(healthSummary)
    expect(summary).toContain('总体{{overallLabel}}')
    expect(summary).toContain('{{version}}')
    expect(summary).toContain('{{uptimeLabel}}')
    expect(summary).toContain('部分检查不可用')
  })
})

describe('系统健康面板在 SetupView 中的落地', () => {
  const healthPanel = setupView.slice(
    setupView.indexOf('activeSection === \'health\''),
    setupView.indexOf('activeSection === \'backup\''),
  )
  const scopedCss = setupView.slice(setupView.indexOf('<style scoped>'))
  const css = compact(scopedCss)

  it('五块图表都挂在健康节里，且不再用纯文本 meta-grid 罗列核心指标', () => {
    expect(healthPanel).toContain('<HealthSummary')
    expect(healthPanel).toContain('<BulletGauge')
    expect(healthPanel).toContain('<UsageBar')
    expect(healthPanel).toContain('<ReadinessGrid')
    expect(healthPanel).toContain('<ReconcileProgress')
    expect(healthPanel).toContain('sysHealthDiskGauge')
    expect(healthPanel).toContain('sysHealthMemoryGauge')
    expect(healthPanel).toContain('sysHealthFailureGauge')
    expect(healthPanel).toContain('sysHealthRawFacts')
  })

  it('卡片用 grid + auto-fit，窄屏单列，长路径换行而不横向滚动', () => {
    expect(css).toContain('.health-cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr))')
    expect(css).toContain('.health-cards{grid-template-columns:minmax(0,1fr);}')
    expect(css).toContain('overflow-wrap:anywhere')
    expect(css).toContain('.health-card{display:flex;flex-direction:column;gap:8px;min-width:0')
    // 健康节不使用固定像素宽度，避免窄屏横向滚动
    expect(healthPanel).not.toMatch(/width:\s*\d{3,}px/)
  })

  it('详情折叠保留峰值内存、GC 次数、清理时间、采集与对账时间', () => {
    expect(healthPanel).toContain('峰值内存')
    expect(healthPanel).toContain('GC 次数')
    expect(healthPanel).toContain('上次内存清理')
    expect(healthPanel).toContain('全部原始指标')
  })

  it('健康面板不加载 echarts', () => {
    for (const source of [healthSummary, bulletGauge, usageBar, readinessGrid, reconcileProgress]) {
      expect(source).not.toContain('echarts')
    }
    expect(healthPanel).not.toContain('echarts')
  })
})
