import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

// 配置页各分节的接线契约：每个分节都用同一套分节头，结构不再逐节重写。
const here = dirname(fileURLToPath(import.meta.url))
const VIEW = join(here, '../SetupView.vue')

const setupView = readFileSync(VIEW, 'utf8')

function compact(source) {
  return source.replace(/\s+/g, '')
}

describe('SetupView 分节头与导航', () => {
  it('七个分节各有自己的图标，导航渲染图标而不是七个「settings」', () => {
    const src = compact(setupView)
    for (const icon of ['store', 'timer', 'check-circle', 'package', 'refresh-cw', 'database', 'key']) {
      expect(src).toContain(`icon:'${icon}'`)
    }
    expect(src).toContain('<SvgIcon:name="s.icon":size="14"/>')
  })

  it('POS / 运行配置 / 数据库 / 账号四节都改用 PanelHeader 且标题对应', () => {
    const src = compact(setupView)
    const headers = [
      ['store', 'POS凭据'],
      ['timer', '运行配置'],
      ['database', '数据库凭据'],
      ['key', '账号与会话'],
    ]
    for (const [icon, title] of headers) {
      expect(src).toContain(`icon="${icon}"`)
      expect(src).toContain(`title="${title}"`)
    }
    expect(src.match(/<PanelHeader/g)).toHaveLength(4)
  })

  it('四节的事实/胶囊都从 script 里的 computed 来，不在模板里算', () => {
    const src = compact(setupView)
    expect(src).toContain(':facts="posFacts"')
    expect(src).toContain(':facts="runtimeFacts"')
    expect(src).toContain(':facts="dbFacts"')
    expect(src).toContain(':facts="accountFacts"')
    expect(src).toContain(':pill-label="posPill.label"')
    expect(src).toContain(':pill-label="runtimePill.label"')
    // 数组事实里的 wide 项（DSN / env 文件）占满整行
    expect(src).toContain('wide:true')
  })

  it('不再有嵌套 label（外层 label 改成 field-label）', () => {
    const src = compact(setupView)
    expect(src).not.toContain('<label>浏览器无头模式</label>')
    expect(src).toContain('<spanclass="field-label">浏览器无头模式</span>')
    expect(src).toContain('.field-label{display:block;')
  })

  it('Token 表格用状态胶囊、撤销列贴右，窄屏裁掉时间列', () => {
    const src = compact(setupView)
    expect(src).toContain('class="token-listtoken-table"')
    expect(src).toContain(":label=\"t.revoked_at?'已撤销':'有效'\"")
    expect(src).toContain('.token-tableth:last-child,.token-tabletd:last-child{width:1%;text-align:right;white-space:nowrap;}')
    expect(src).toContain('.token-tableth:nth-child(3),.token-tabletd:nth-child(3)')
  })

  it('模板里不留布局用的 inline style（只保留进度条宽度绑定）', () => {
    const inlineStyles = [...setupView.matchAll(/style="([^"]*)"/g)]
      .map((match) => match[1])
      .filter((value) => !value.includes('width:'))
    expect(inlineStyles).toEqual([])
  })

  it('辅助块（拉取门店 / URL 解析）用统一的 field-block 结构', () => {
    const src = compact(setupView)
    expect(src.match(/class="field-block"/g)).toHaveLength(2)
    expect(src).toContain('.field-block__head.btn{margin-left:auto;}')
    expect(src).toContain('.grid.luyun-check-row{display:flex;}')
  })

  it('页头不再重复分节状态（凭据状态只在 POS 分节头出现一次）', () => {
    const src = compact(setupView)
    expect(src).toContain('<h1>系统配置</h1>')
    expect(src).not.toContain('凭据已配置\',\'凭据未配置')
  })

  it('清空凭据 / 退出登录 / 撤销 Token 都走站内两步确认', () => {
    const src = compact(setupView)
    for (const fn of ['onClearCredentialsConfirm', 'onLogoutConfirm', 'onRevokeTokenConfirm']) {
      expect(src).toContain(`function${fn}(`)
      expect(src).toContain(`@click="${fn}`)
    }
    // composable 里不再自己弹浏览器原生 confirm
    const account = compact(readFileSync(join(here, '../../composables/useAccountSettings.js'), 'utf8'))
    const posCred = compact(readFileSync(join(here, '../../composables/usePosCredentials.js'), 'utf8'))
    expect(account).not.toContain('window.confirm')
    expect(posCred).not.toContain('window.confirm')
  })

  it('账号事实列用短用户名，完整说明留在说明段', () => {
    const src = compact(setupView)
    expect(src).toContain(':description="sessionUserHint"')
    expect(src).toContain("v:sessionUsername.value||'—'")
  })
})
