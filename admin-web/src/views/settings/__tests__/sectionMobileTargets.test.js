import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

// t13：收口 t9 交下来的两处移动端残留，外加两个「不在通用层覆盖范围内」的小目标。
//
// 这一档为什么**必须写在分节自己的 scoped 里**、而不是 theme.css：折叠把手（`.section-help`、
// `.more`、`.points__*`、`.migration-*`、`.history-error__raw`）与密码框的显隐开关都是**分节
// 自己的类名**，塞进全局主题等于让主题认识页面内部结构（ADR 0099 的四条原则之后还写着：
// 分节样式住分节文件、theme.css 只放通用控件）。而子组件里的那两个折叠头（备份点列表、
// 数据库迁移面板的 scoped 样式比父级更具体）要用 `:deep()` 且带上自己的作用域类名才压得住。
//
// 下面同时钉住"为什么只在窄屏改"：桌面档是鼠标点，密度维持原样（折叠把手 22–36px、
// `.toggle` 24px），所以每条新规则都必须落在 `@media (max-width: 430px)` 里，
// 而基础规则一个字都不许动。
const here = dirname(fileURLToPath(import.meta.url))
const SETTINGS = join(here, '..')
const themeCss = readFileSync(join(here, '../../../styles/theme.css'), 'utf8')
const shell = readFileSync(join(here, '../../SetupView.vue'), 'utf8')

function read(name) {
  return readFileSync(join(SETTINGS, name), 'utf8')
}
function compact(source) {
  return source.replace(/\s+/g, '')
}
/** 取 ≤430px 那一段（按花括号配平，别用正则 —— 块里的规则可能本身换行）。 */
function mobileBlock(source) {
  const at = source.indexOf('@media (max-width: 430px)')
  expect(at, '缺 ≤430px 的小屏规则块').toBeGreaterThan(-1)
  let depth = 0
  for (let i = source.indexOf('{', at); i < source.length; i += 1) {
    if (source[i] === '{') depth += 1
    else if (source[i] === '}') {
      depth -= 1
      if (depth === 0) return compact(source.slice(at, i + 1))
    }
  }
  throw new Error('≤430px 块没有闭合')
}

const collect = read('CollectSection.vue')
const status = read('StatusSection.vue')
const data = read('DataSection.vue')
const system = read('SystemSection.vue')
const account = read('AccountSection.vue')

describe('数据节：清理预览里的长标识符可断行（320px 的 4 个越界元素归零）', () => {
  it('清理预览的每一行给 span 加 overflow-wrap: anywhere', () => {
    // 行里的 `luyun_backup_20260921_220039.luyunbak` 是一整串不可断行的标识符：它把
    // 整行的 min-content 顶到 294px，而 320px 上卡片里只有 262px，于是行连同卡片被裁。
    // `anywhere` 只在放不下时才断，宽屏一个字都不变。
    expect(compact(data)).toContain('.cleanup-list li > span { overflow-wrap: anywhere; }'.replace(/\s+/g, ''))
    // 规则属于这一节：全局主题与壳里都不该出现这个类名。
    expect(themeCss).not.toContain('.cleanup-list')
    expect(shell).not.toContain('.cleanup-list')
  })
})

describe('四个有折叠区的分节：≤430px 折叠把手抬到 44px，桌面档不动', () => {
  it('采集节 / 状态节：.section-help 的 summary', () => {
    expect(mobileBlock(collect)).toContain('.section-help>summary{min-height:44px;}')
    expect(mobileBlock(status)).toContain('.section-help>summary{min-height:44px;}')
    // 桌面档仍是 32 / 36px（只加窄屏规则，基础规则未动）。
    expect(compact(collect)).toContain('.section-help>summary{display:flex;align-items:center;gap:6px;min-height:32px;')
    expect(compact(status)).toContain('.section-help>summary{display:flex;align-items:center;gap:6px;min-height:36px;')
  })

  it('数据节：本节的 .more + 子组件备份点列表的两个分组把手（:deep）', () => {
    const block = mobileBlock(data)
    expect(block).toContain('.more>summary{min-height:44px;}')
    expect(block).toContain('.settings-section:deep(.points__shared>summary),.settings-section:deep(.points__excluded>summary){min-height:44px;}')
    // 备份点列表的高度写在 components/backup/BackupPointList.vue 的 scoped 里（比父级具体），
    // 所以这里必须带上 .settings-section 自己的作用域类名；两个把手的基础值也未动。
    // 基础规则的 min-height 值未被改动（它后来加了 gap: 6px，所以只钉值、不钉整串）。
    const moreBase = compact(data).slice(compact(data).indexOf('.more>summary{'), compact(data).indexOf('.more>summary:focus-visible'))
    expect(moreBase).toContain('min-height:36px')
  })

  it('系统节：四处折叠把手 + 迁移面板的两个（:deep）', () => {
    const block = mobileBlock(system)
    expect(block).toContain('.section-help>summary,.release-more>summary,.history-error__raw>summary{min-height:44px;}')
    expect(block).toContain('.settings-section:deep(.migration-applied>summary),.settings-section:deep(.migration-bootstrap>summary){min-height:44px;}')
    // 桌面档：历史失败原文 22px、.section-help 32px，都保持原样（那条折叠框比较重，鼠标点没问题）。
    const rawBase = compact(system).slice(compact(system).indexOf('.history-error__raw>summary{'), compact(system).indexOf('.history-error__raw>summary::'))
    expect(rawBase).toContain('min-height:22px')
    expect(compact(system)).toContain('.section-help>summary{display:flex;align-items:center;gap:6px;min-height:32px;')
  })

  it('账号节没有折叠区，因此不需要这一档', () => {
    expect(compact(account)).not.toContain('<summary')
    expect(account).not.toContain('@media (max-width: 430px)')
  })
})

describe('密码框的「显示 / 隐藏」开关：≤430px 40px，桌面仍是 24px', () => {
  it('采集节里那条抬到 40px，且基础规则没动', () => {
    expect(mobileBlock(collect)).toContain('.password-row.toggle{display:inline-flex;align-items:center;justify-content:center;min-height:40px;padding:08px;}')
    // 桌面档仍是绝对定位的小开关（基础规则一字未动）。
    expect(compact(collect)).toContain('.password-row.toggle{position:absolute;right:8px;top:50%;')
  })
})

describe('分节专属类名没有被塞进通用层', () => {
  it('theme.css 与壳里都不认识这些类名', () => {
    for (const name of ['.section-help', '.points__', '.migration-', '.history-error', '.more>']) {
      expect(themeCss).not.toContain(name)
      expect(shell).not.toContain(name)
    }
  })
})
