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

// A6：390 下分节导航是横向滚动容器（352 / 848px），第 4–7 项（备份中心、系统更新、
// 数据库凭据、账号与 API Token）完全落在屏幕外，第 3 项刚好卡在边界上——看起来像
// "只有 3 个分节"。而备份与更新正是用户最常来找的两块。滚动本身早就有了，缺的是
// 「看得出还能滚」和「当前分节自己滚进来」。
describe('SetupView 分节导航的溢出与当前项', () => {
  it('窄屏隐藏滚动条，并给两端加只在溢出时出现的渐隐提示', () => {
    const src = compact(setupView)
    // 溢出容器没有滚动条时，"右边还有内容"没有任何线索
    expect(src).toContain('scrollbar-width:none;')
    expect(src).toContain('.setup-nav::-webkit-scrollbar{display:none;}')
    expect(src).toContain('.setup-nav::before,')
    expect(src).toContain('.setup-nav::after{')
    // 只在真的还有内容的那一侧出现
    expect(src).toContain('.setup-nav:not(.is-scroll-end)::after{opacity:1;}')
    expect(src).toContain('.setup-nav:not(.is-scroll-start)::before{opacity:1;}')
    // 渐隐是绝对定位的，窄屏关掉 sticky 后必须自己撑起定位基准
    expect(src).toContain('position:relative;')
  })

  it('切分节（含 ?section= 直接落进来）会把当前项滚进视野', () => {
    const src = compact(setupView)
    expect(src).toContain('constsetupNav=createScrollHints()')
    expect(src).toContain('ref="setupNavEl"')
    expect(src).toContain(":class=\"{'is-scroll-start':setupNav.atStart.value,")
    // 时机：active 换格要等这次渲染落地，滚早了滚的是上一个分节
    expect(src).toContain('nextTick(()=>setupNav.scrollActiveIntoView())')
  })
})

// A2/A19：本页四个手写 modal（两步确认、恢复完成、重置数据库密码、Token 已生成）
// 都不响应 Esc —— 实测 Esc 之后框还开着、还挡着后面的内容，鼠标用户以为页面卡住，
// 键盘用户被困在框里。同站另一套弹窗（components/admin/ConfirmDialog.vue）自带 Esc，
// 所以这是"同一产品两种行为"。
describe('SetupView 弹窗的 Esc 关闭', () => {
  it('四个 modal 各自接上 Esc，并走它自己的关闭路径', () => {
    const src = compact(setupView)
    expect(src).toContain("useEscapeClose(()=>confirmState.open,closeConfirm)")
    expect(src).toContain("useEscapeClose(()=>importSuccessModal.show,confirmImportSuccessRedirect)")
    expect(src).toContain("useEscapeClose(()=>dbResetConfirm.open,closeDbReset)")
    expect(src).toContain("useEscapeClose(()=>tokenModal.show,closeTokenModal)")
  })

  it('用的是共享实现，不在页面里再手写一遍 keydown', () => {
    const src = compact(setupView)
    expect(src).toContain("import{useEscapeClose}from'../composables/useEscapeClose'")
    expect(src).not.toContain("addEventListener('keydown'")
  })
})
