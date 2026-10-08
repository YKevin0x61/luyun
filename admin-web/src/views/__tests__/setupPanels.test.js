import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

// /settings 配置页改版后的**契约**（ADR 0099）：7 节并成 5 节，壳只剩导航/页头/切换，
// 每一节住进 views/settings/ 下自己的文件。这里锁的不是"哪一行长什么样"，而是
// 三个别的任务（t2–t6）会在同一个壳里并行改分节时必须成立的四件事：
//
//   1. 分节边界：壳里不再有分节内容，5 个分节各承接哪一块；
//   2. 数据与反馈归属：分节自取数据（壳不再代调 load*）、反馈就地渲染（壳不再有全局 alert）；
//   3. 弹窗归属：框与 Esc 在壳里、状态与文案在分节里（4 个弹窗各有注册方）；
//   4. 壳的既有行为：5 项导航（节名 ≤ 4 字、每项带图标）、`?section=` 落地（含旧深链）、
//      当前项滚进视野、页头一行状态摘要（任一端点失败只让该项不出现）。
//
// 以下是源码级断言而非挂载测试：本套前端测试的 vitest 环境是 `node`（见 vite.config.js），
// 全站既有测试（wecomPushLayout / setupPanels 的前身）都是同一路子——读组件源码验证契约。

const here = dirname(fileURLToPath(import.meta.url))
const VIEW = join(here, '../SetupView.vue')
const SETTINGS = join(here, '../settings')

const setupView = readFileSync(VIEW, 'utf8')

/** 五个分节：文件名 → 它承接的原分节（用于下面逐条核对内容归属）。 */
const SECTION_FILES = {
  'CollectSection.vue': '原「POS 凭据」+「运行配置」',
  'StatusSection.vue': '原「系统健康状态」',
  'DataSection.vue': '原「备份中心」+「数据库凭据」',
  'SystemSection.vue': '原「系统更新」',
  'AccountSection.vue': '原「账号与 API Token」',
}

const sections = Object.fromEntries(
  Object.keys(SECTION_FILES).map((name) => [name, readFileSync(join(SETTINGS, name), 'utf8')]),
)
const contract = readFileSync(join(SETTINGS, 'sectionContract.js'), 'utf8')

function compact(source) {
  return source.replace(/\s+/g, '')
}

describe('分节边界：壳只剩导航/页头/切换，内容全在 settings/ 五个分节里', () => {
  it('导航恰好 5 项，节名 ≤ 4 字，每项都带自己的图标', () => {
    const src = compact(setupView)
    const labels = [...src.matchAll(/\{id:'(\w+)',label:'([^']+)',icon:'([\w-]+)'\}/g)]
    expect(labels.map((m) => m[1])).toEqual(['collect', 'status', 'data', 'system', 'account'])
    expect(labels.map((m) => m[2])).toEqual(['采集', '状态', '数据', '系统', '账号'])
    // 390px 下横滑 tab 要露出后面几节，节名长了就把第 4、5 节顶出屏幕（A6）。
    for (const [, , label] of labels) expect(label.length).toBeLessThanOrEqual(4)
    // 图标是逐项传进去的，不是五项共用一个 settings 图标。
    expect(new Set(labels.map((m) => m[3])).size).toBe(5)
    expect(src).toContain('<SvgIcon:name="s.icon":size="14"/>')
  })

  it('壳不再包含任何分节的表单字段与业务文案', () => {
    // 只看壳真正渲染的那一段：注释里提到旧节名（"原「账号与 API Token」"）不算内容。
    const shellTemplate = setupView.slice(setupView.indexOf('<template>'), setupView.indexOf('<style'))
    for (const marker of [
      'POS 凭据', '运行配置', '系统健康状态', '备份中心', '系统更新', '数据库凭据',
      '账号与 API Token', '账号与会话', '加密口令', '保留份数', '更新环境自检',
      '发行版目录', 'API Token（KDS 等设备）', '修改登录密码', '营业时段',
    ]) {
      expect(shellTemplate).not.toContain(marker)
    }
    for (const field of ['credForm', 'runtimeForm', 'exportForm', 'retentionForm', 'githubForm', 'changePwdForm']) {
      expect(setupView).not.toContain(field)
    }
    for (const fieldId of ['id="phone"', 'id="importPass"', 'id="githubToken"', 'id="oldPassword"', 'id="snapshotKeep"']) {
      expect(setupView).not.toContain(fieldId)
    }
  })

  it('分节的 composable 也搬出了壳：壳不再替分节取数据', () => {
    for (const composable of [
      'usePosCredentials', 'useRuntimeSettings', 'useSystemHealth', 'useBackupCenter',
      'useDbCredentials', 'useSystemUpdate', 'useAccountSettings',
    ]) {
      expect(setupView).not.toContain(composable)
    }
    for (const loader of ['loadPoints(', 'loadHealth(', 'loadVersionCheck(', 'loadSysHealth(', 'loadTokenList(', 'loadDbCred(']) {
      expect(setupView).not.toContain(loader)
    }
  })

  it('五个分节文件各自承接原节，且只调自己的 composable（不在分节里手写请求）', () => {
    const ownership = {
      'CollectSection.vue': ['usePosCredentials', 'useRuntimeSettings', 'posFacts', 'runtimeForm'],
      'StatusSection.vue': ['useSystemHealth', 'sysHealthDiskGauge', 'sysHealthRawFacts'],
      'DataSection.vue': ['useBackupCenter', 'useDbCredentials', 'onExportBackup', 'retentionForm'],
      'SystemSection.vue': ['useSystemUpdate', 'DatabaseMigrations', 'preflightChecks', 'loadHistory'],
      'AccountSection.vue': ['useAccountSettings', 'loadTokenList', 'changePwdForm', 'tokenLabel'],
    }
    for (const [name, markers] of Object.entries(ownership)) {
      for (const marker of markers) expect(sections[name]).toContain(marker)
      // 请求逻辑仍在 composable 里：分节不直接 api.get / api.post（壳的页头摘要除外）。
      expect(sections[name]).not.toMatch(/\bapi\.(get|post|put|patch|delete)\(/)
    }
  })

  it('每个分节的头部注释写死了它的 props / emits / inject 契约', () => {
    for (const [name, origin] of Object.entries(SECTION_FILES)) {
      const src = sections[name]
      const header = src.slice(0, src.indexOf('</script>'))
      expect(header).toContain('契约')
      expect(header).toContain('props')
      expect(header).toContain('emits')
      expect(header).toContain('inject')
      expect(header).toContain(origin)
      // props 只有 active（+采集节的 refreshKey）：分节自取数据，壳不喂数据。
      expect(src).toContain('active: { type: Boolean, default: false }')
      expect(src).toContain('./sectionContract')
    }
  })
})

describe('分节自取数据、反馈就地渲染', () => {
  it('每个分节在 active 翻真时自己拉数据（watch + immediate）', () => {
    for (const src of Object.values(sections)) {
      expect(src).toContain('() => props.active')
      expect(src, '分节要在挂载/被切到时自取数据').toContain('{ immediate: true }')
    }
    // 壳只负责"现在是哪一节"，不再按 id 分派 load*。
    expect(setupView).not.toContain("if (id === '")
  })

  it('写操作的反馈落在本分节内：分节自持提示条，壳不再有页面级 alert', () => {
    expect(setupView).not.toContain('v-if="alert.show"')
    expect(setupView).not.toContain('function showAlert')
    // 有写操作的四个分节：提示条渲染在自己这一节的根节点里（就地），且把
    // showAlert/clearAlert 交给了自己的 composable —— 反馈不再飘到整页顶部。
    for (const name of ['CollectSection.vue', 'DataSection.vue', 'SystemSection.vue', 'AccountSection.vue']) {
      const src = sections[name]
      expect(src).toContain('const alert = reactive(')
      expect(src).toContain('function showAlert(')
      expect(src).toContain('function clearAlert(')
      expect(src).toContain('v-if="alert.show"')
      expect(src).toContain(':class="alert.type"')
      expect(src).toMatch(/\(\{ showAlert, clearAlert/)
    }
    // 状态节是只读的（没有写操作）：不挂一条永远为空的提示条，并在头部注释里写明
    // 「失败反馈由 HealthSummary 的 error 属性渲染在本节内」。
    const status = compact(sections['StatusSection.vue'])
    expect(status).not.toContain('function showAlert(')
    expect(status).toContain('不渲染就地提示条')
    expect(status).toContain('useSystemHealth({})')
  })

  it('样式一律写在分节自己的 <style scoped> 里', () => {
    for (const src of Object.values(sections)) {
      expect(src).toContain('<style scoped>')
      expect(src.match(/<style(?! scoped)/)).toBeNull()
    }
  })
})

describe('四个弹窗：框与 Esc 在壳里，状态与文案在分节里', () => {
  it('壳渲染四个弹窗，各自的开关与动作来自注册进来的控制器', () => {
    const src = compact(setupView)
    for (const kind of ['confirm', 'importSuccess', 'dbReset', 'token']) {
      expect(src).toContain(`modals.${kind}?.isOpen()`)
    }
    expect(src).toContain("modals.confirm.close()")
    expect(src).toContain("modals.importSuccess.close()")
    expect(src).toContain("modals.dbReset.close()")
    expect(src).toContain("modals.token.close()")
    expect(src).toContain('provide(SETTINGS_MODAL_HOST,modals)')
  })

  it('分节把四个弹窗各自注册上来（数据节 3 个、账号节 1 个）', () => {
    const data = compact(sections['DataSection.vue'])
    for (const kind of ['confirm', 'importSuccess', 'dbReset']) {
      expect(data).toContain(`register('${kind}',{`)
    }
    // 两步确认是共用通道：采集节与账号节的危险动作也走它，不各写一套。
    expect(compact(sections['AccountSection.vue'])).toContain("register('token',{")
    expect(compact(sections['CollectSection.vue'])).toContain('modalHost?.requestConfirm(')
    expect(compact(sections['AccountSection.vue'])).toContain('modalHost?.requestConfirm(')
    expect(compact(contract)).toContain('exportconstSETTINGS_MODAL_HOST')
    expect(compact(contract)).toContain('exportfunctioncreateSectionModalHost()')
  })

  it('Esc 只有一条入口并分派给开着的那一个（四条各注册一条只有栈顶会响应）', () => {
    const src = compact(setupView)
    // 共享实现只让"最后注册"的那一格响应（useEscapeClose 的栈），而本页四个弹窗都是
    // 常驻 v-if —— 四条各注册一条，Esc 只会落到最后一条上，前三个按了没反应。
    expect(src.match(/useEscapeClose\(/g)).toHaveLength(1)
    expect(src).toContain('constMODAL_ORDER=')
    expect(src).toContain('modals[MODAL_ORDER[i]]?.isOpen()')
    expect(src).toContain('if(kind)modals[kind].close()')
    expect(src).toContain("import{useEscapeClose}from'../composables/useEscapeClose'")
    expect(src).not.toContain("addEventListener('keydown'")
  })
})

describe('壳的既有行为：?section= 落地、当前项滚进视野、页头一行状态摘要', () => {
  it('?section= 落地到五个 id，旧 7 节的深链继续有效', () => {
    const src = compact(setupView)
    expect(src).toContain('resolveSectionId(route.query.section)')
    for (const alias of ['pos:', 'runtime:', 'health:', 'backup:', 'database:', 'update:', 'account:']) {
      expect(src).toContain(alias)
    }
    // 旧深链指向新分节：DataTable 的「备份 / 迁移」按钮用的是 ?section=backup。
    expect(src).toContain("backup:'data'")
    expect(src).toContain("update:'system'")
    expect(src).toContain("pos:'collect'")
  })

  it('切分节（含 ?section= 直接落进来）会把当前项滚进视野，并保住两端的溢出渐隐', () => {
    const src = compact(setupView)
    expect(src).toContain('constsetupNav=createScrollHints()')
    expect(src).toContain('ref="setupNavEl"')
    expect(src).toContain(":class=\"{'is-scroll-start':setupNav.atStart.value,")
    // 时机：active 换格要等这次渲染落地，滚早了滚的是上一个分节。
    expect(src).toContain('nextTick(()=>setupNav.scrollActiveIntoView())')
    // 溢出容器没有滚动条时，"右边还有内容"没有任何线索。
    expect(src).toContain('scrollbar-width:none;')
    expect(src).toContain('.setup-nav::-webkit-scrollbar{display:none;}')
    expect(src).toContain('.setup-nav::before,')
    expect(src).toContain('.setup-nav::after{')
    expect(src).toContain('.setup-nav:not(.is-scroll-end)::after{opacity:1;}')
    expect(src).toContain('.setup-nav:not(.is-scroll-start)::before{opacity:1;}')
  })

  it('页头是一行状态摘要（采集状态 · 版本 · 上次备份），三项各自独立取数', () => {
    const src = compact(setupView)
    // 机制解释类的副标题（原 .subtitle 那段）不该留在页头。
    expect(setupView).not.toContain('class="subtitle"')
    expect(src).toContain('class="status-summary"')
    expect(src).toContain('v-if="summaryItems.length"')
    // 取数只走既有只读端点。
    expect(src).toContain("api.get('/api/scraper/status',null,null,'no-store')")
    expect(src).toContain("api.get('/api/system/health',null,null,'no-store')")
    expect(src).toContain("api.get('/api/backup/health',null,null,'no-store')")
    // 任一端点失败只让该项不显示：allSettled（不是 all），且逐项判 fulfilled。
    expect(src).toContain('Promise.allSettled(')
    expect(src).not.toContain('Promise.all([')
    expect(src.match(/status==='fulfilled'/g)).toHaveLength(3)
    expect(src).toContain("status==='fulfilled'&&health.value?.version")
  })

  it('整库恢复之后：数据节抛事件，壳翻成采集节的刷新计数（跨节不直接调用）', () => {
    const src = compact(setupView)
    expect(src).toContain('@data-restored="onDataRestored"')
    expect(src).toContain(':refresh-key="collectRefresh"')
    expect(compact(sections['DataSection.vue'])).toContain("defineEmits(['data-restored'])")
    // 采集节据此重取被恢复覆盖过的两份配置。
    expect(compact(sections['CollectSection.vue'])).toContain('()=>props.refreshKey')
  })
})

// t9：移动端实测（.scratch/settings-mobile-audit/report.md）暴露的两条硬约束 ——
//   A) 320 / 360 机型上导航 5 项放不下（内容 348px vs 可视 282 / 322px），第 5 个 tab
//      「账号」在屏外，而它是退出登录与 API Token 唯一的入口；
//   B) 一批操作控件的触控高度只有 18–36px（勾选行 18px、.btn-sm 24px、主按钮 31–36px）。
// 下面锁的是"怎么修的"，因为修法有两条被明确否掉的捷径：不许靠缩小字号 / 删节名换宽度，
// 也不许放大 16×16 的复选框方块凑热区 —— 两者都在断言里排除。
describe('移动端：320px 起 5 项导航全可见 + 触控尺寸下限', () => {
  const themeCss = readFileSync(join(here, '../../styles/theme.css'), 'utf8')

  /** 取某个文件里 `@media (max-width: 430px) { … }` 那一块（块内不嵌套花括号）。 */
  function mobileBlock(source) {
    // 按花括号配平取这一块：块里的规则可能自己换行，正则会在第一个行首 `}` 上截断。
    const at = source.indexOf('@media (max-width: 430px)')
    expect(at, '缺 ≤430px 的小屏规则块').toBeGreaterThan(-1)
    let depth = 0
    for (let i = source.indexOf('{', at); i < source.length; i += 1) {
      if (source[i] === '{') depth += 1
      else if (source[i] === '}') {
        depth -= 1
        if (depth === 0) return { raw: source.slice(at, i + 1), src: compact(source.slice(at, i + 1)) }
      }
    }
    throw new Error('≤430px 块没有闭合')
  }

  it('320/360 机型靠压内边距与收窄图标让 5 项放下，不动字号、不删节名', () => {
    const { src } = mobileBlock(setupView)
    // 容器左右各让 6px（320px 下可视内宽 282 → 294）
    expect(src).toContain('.container{padding:18px12px;}')
    // 图标与文字的间距、水平内边距各收一档；图标本身收到 12px
    expect(src).toContain('.setup-nav.nav-item{padding:8px6px;gap:4px;}')
    expect(src).toContain('.setup-nav.nav-item:deep(svg){width:12px;height:12px;}')
    // 两条捷径都排除：块里不许出现字号改动，也不许把节名藏起来/截断。
    expect(src).not.toContain('font-size')
    expect(src).not.toContain('display:none')
    expect(src).not.toContain('text-overflow')
  })

  it('子项按内容宽度排的坑已堵住：纵向排列时子项回到容器宽度', () => {
    // `align-items: flex-start` 是桌面那一行的（侧栏与内容顶端对齐）；纵向排列后它会让
    // 每个子项按 fit-content 排，内容比容器宽就把分节撑出容器（320px 数据节实测 332 > 294）。
    expect(compact(setupView)).toContain('flex-direction:column;gap:12px;align-items:stretch;')
  })

  it('导航项与返回按钮在窄屏也是 44px 的触控目标', () => {
    const src = compact(setupView)
    const navItem = src.slice(src.indexOf('.setup-nav.nav-item{'), src.indexOf('.setup-nav.nav-item :deep(svg)'))
    expect(navItem).toContain('min-height:44px;')
    const backBtn = src.slice(src.indexOf('.back-btn{'), src.indexOf('.back-btn:hover'))
    expect(backBtn).toContain('min-height:44px;')
  })

  it('密码框的「显示 / 隐藏」在壳与采集节取同一档位（≤430px 40px）', () => {
    // 本页有两处同名交互：采集节密码框在分节里、重置数据库密码弹窗在壳里。一个交互两种
    // 尺寸就是"在不同地方点到的热区不一样"，所以两处必须同档，且都只在窄屏抬。
    const toggleRule = '.password-row.toggle{display:inline-flex;align-items:center;justify-content:center;min-height:40px;padding:08px;}'
    expect(mobileBlock(setupView).src).toContain(toggleRule)
    expect(mobileBlock(sections['CollectSection.vue']).src).toContain(toggleRule)
    // 桌面那一档仍是绝对定位的小开关（两处的基础规则都一字未动）。
    expect(compact(setupView)).toContain('.password-row.toggle{position:absolute;right:8px;top:50%;')
    expect(compact(sections['CollectSection.vue'])).toContain('.password-row.toggle{position:absolute;right:8px;top:50%;')
  })

  it('主题里给三类通用控件压了触控下限，且只在 ≤430px 生效', () => {
    const { src } = mobileBlock(themeCss)
    // 主按钮 40px、次级小按钮 36px（!important 是有意的：要压过组件里为桌面密度写的
    // `.panel-header__actions :deep(.btn) { min-height: 36px }` 那类同名覆盖）。
    expect(src).toContain('.btn{min-height:40px!important;}')
    expect(src).toContain('.btn-sm{min-height:36px!important;}')
    // 勾选行靠 min-height + 上下 padding 撑热区。
    expect(src).toContain('.luyun-check-row,.luyun-radio-row{min-height:44px;padding:11px0;}')
    // 勾选行的 16×16 方块本身不动：这一块里不出现 .luyun-checkbox。
    expect(src).not.toContain('.luyun-checkbox')
    expect(compact(themeCss)).toContain('.luyun-checkbox{all:unset;box-sizing:border-box;width:16px;height:16px;')
    // `fieldset` 的 UA `min-inline-size: min-content` 会让整张卡比容器宽。
    expect(src).toContain('fieldset{min-inline-size:0;}')
    // 只动尺寸与间距：不碰颜色、背景、边框与布局方式。
    for (const prop of ['color:', 'background', 'border', 'display:', 'grid-template', 'position:']) {
      expect(src).not.toContain(prop)
    }
  })
})
