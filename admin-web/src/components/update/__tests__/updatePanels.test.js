import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

// 更新面板是纯展示（步骤推导在 utils/updateProgress.js 里已单测），这里按仓库既有
// 做法做「源码契约」检查：结论有文字、GitHub 低频配置折叠、阶段用符号 + 文字表达。
const here = dirname(fileURLToPath(import.meta.url))
const UPDATE_DIR = join(here, '..')
const VIEWS_DIR = join(here, '../../../views')

const overview = readFileSync(join(UPDATE_DIR, 'UpdateOverview.vue'), 'utf8')
const stages = readFileSync(join(UPDATE_DIR, 'UpdateStageProgress.vue'), 'utf8')
const setupView = readFileSync(join(VIEWS_DIR, 'SetupView.vue'), 'utf8')

function compact(source) {
  return source.replace(/\s+/g, '')
}

describe('UpdateOverview 契约', () => {
  it('状态胶囊 + 版本对照 + 检测入口在同一处', () => {
    const src = compact(overview)
    expect(src).toContain(':label="versionCheck?statusSummary:\'尚未检测\'"')
    for (const label of ['当前安装', 'APP_VERSION', '最新正式版', '部署模式']) {
      expect(src).toContain(`<dt>${label}</dt>`)
    }
    expect(src).toContain('@click="$emit(\'refresh\')"')
    expect(src).toContain(':disabled="loading"')
  })

  it('身份异常时给出具体原因，而不是只变色', () => {
    const src = compact(overview)
    expect(src).toContain('degradedReason')
    expect(src).toContain('本机已装身份异常')
  })

  it('docker 部署带上容器名或明确的缺省提示', () => {
    const src = compact(overview)
    expect(src).toContain("vc.deploy_mode!=='docker'")
    expect(src).toContain('未设LUYUN_DOCKER_CONTAINER')
  })
})

describe('UpdateStageProgress 契约', () => {
  it('用有序列表 + aria-label 描述整条阶段链', () => {
    const src = compact(stages)
    expect(src).toContain('<olclass="stages__list"')
    expect(src).toContain(':aria-label="`更新阶段：${view.summary}`"')
    expect(src).toContain('stepSymbol(item.state)')
    expect(src).toContain('{{item.label}}')
  })

  it('进行中/完成/失败三种状态各有样式，未开始用中性默认', () => {
    const src = compact(stages)
    for (const state of ['done', 'active', 'failed']) {
      expect(src).toContain(`.stage.is-${state}`)
    }
    // 未开始不额外上色：基础 .stage 就是中性态
    expect(src).not.toContain('.stage.is-pending')
    expect(src).toContain('.stages.is-failed.stages__summary')
    expect(src).toContain('.stages.is-unhealthy.stages__summary')
    expect(src).toContain('.stages.is-succeeded.stages__summary')
  })

  it('尊重 prefers-reduced-motion', () => {
    expect(compact(stages)).toContain('prefers-reduced-motion:reduce')
  })

  it('备份快照时间戳原样显示，ISO 时间统一截断', () => {
    const src = compact(stages)
    expect(src).toContain('^\\d{8}-\\d{4}$')
    expect(src).toContain('formatTs(value)')
  })
})

describe('SetupView 系统更新接线契约', () => {
  it('版本总览 / 自检清单 / 阶段进度都用组件', () => {
    const src = compact(setupView)
    expect(src).toContain('<UpdateOverview')
    expect(src).toContain(':degraded-reason="degradedReasonLabel(versionCheck?.degraded_reason)"')
    expect(src).toContain('<CheckList:items="preflightChecks"')
    expect(src).toContain('<UpdateStageProgress:job="job"')
  })

  it('GitHub 连接折叠为 details，不再占据面板首屏', () => {
    const src = compact(setupView)
    expect(src).toContain('<detailsclass="github-panel">')
    expect(src).toContain('GitHub连接（公开仓通常无需配置）')
    const detailsAt = src.indexOf('<detailsclass="github-panel">')
    const githubTokenAt = src.indexOf('id="githubToken"')
    expect(detailsAt).toBeGreaterThan(0)
    expect(githubTokenAt).toBeGreaterThan(detailsAt)
  })

  it('危险操作仍保留两步确认与勾选项', () => {
    const src = compact(setupView)
    expect(src).toContain('我确认丢弃部署目录中的本地改动')
    expect(src).toContain('我已知晓营业高峰风险，仍然执行更新')
    expect(src).toContain('确认开始更新')
  })

  it('发行版目录只直接列最新几个，其余折进 details 且共用同一行组件', () => {
    const src = compact(setupView)
    expect(src).toContain('v-for="rinvisibleReleases"')
    expect(src).toContain('<detailsv-if="hiddenReleases.length"class="release-more">')
    expect(src).toContain('v-for="rinhiddenReleases"')
    expect(src).toContain('展开其余{{hiddenReleases.length}}个版本')
    expect(src).toContain('hiddenReleasesHasInstalled')
    // 折叠区有方向指示，展开后旋转
    expect(src).toContain('class="release-more__icon"')
    expect(src).toContain('.release-more[open].release-more__icon')
    // 两处都用 ReleaseRow，避免两份 tr 标记各写一遍
    expect(src.match(/<ReleaseRow/g)).toHaveLength(2)
    expect(src).not.toContain('v-for="rinversionCheck.releases"')
  })

  it('自检未通过时只在目录级提示一次，不在每行重复', () => {
    const src = compact(setupView)
    expect(src).toContain('v-if="!canShowApply"class="hintis-warnrelease-blocked"')
    expect(src).toContain('「应用此版本」入口已全部禁用')
    // 每行不再各自渲染「自检未通过」（注释里提到它是为了解释为什么没有这个标记）
    expect(compact(readFileSync(join(UPDATE_DIR, 'ReleaseRow.vue'), 'utf8'))).not.toContain('>自检未通过<')
  })

  it('操作列右对齐、Tag/发布时间收紧，避免胶囊贴边与名称列被挤', () => {
    const src = compact(setupView)
    expect(src).toContain('.release-listth:last-child,.release-listtd:last-child{width:1%;text-align:right;white-space:nowrap;}')
    expect(src).toContain('justify-content:flex-end')
    expect(src).toContain('.release-listtbodytr:hover')
  })
})

describe('ReleaseRow 契约', () => {
  const row = readFileSync(join(UPDATE_DIR, 'ReleaseRow.vue'), 'utf8')

  it('当前 / 最新 / 应用此版本三种标记都在一行里', () => {
    const src = compact(row)
    expect(src).toContain("label=\"当前\"")
    expect(src).toContain("label=\"最新\"")
    expect(src).toContain('应用此版本')
    expect(src).toContain("$emit('apply',release.tag)")
  })

  it('身份异常时不给「当前」标记，自检未通过不给应用入口', () => {
    const src = compact(row)
    expect(src).toContain('!props.degraded&&!!props.installedTag')
    expect(src).toContain('v-if="canApply"')
    // 不给入口时也不留下占位文案：结论由目录级提示统一说明
    expect(src).not.toContain('>自检未通过<')
    expect(src).toContain('release-actions-cell')
  })
})
