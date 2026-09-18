import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

// 两个共享展示组件的源码契约：语义用文字表达、颜色只是辅助。
const here = dirname(fileURLToPath(import.meta.url))
const UI_DIR = join(here, '..')

const pill = readFileSync(join(UI_DIR, 'StatusPill.vue'), 'utf8')
const checks = readFileSync(join(UI_DIR, 'CheckList.vue'), 'utf8')
const panelHeader = readFileSync(join(UI_DIR, 'PanelHeader.vue'), 'utf8')

function compact(source) {
  return source.replace(/\s+/g, '')
}

describe('StatusPill', () => {
  it('五档 tone 都有对应样式，且默认中性', () => {
    const src = compact(pill)
    expect(src).toContain("tone:{type:String,default:'neutral'}")
    for (const tone of ['ok', 'warn', 'error', 'info', 'neutral']) {
      expect(src).toContain(`.pill.is-${tone}`)
    }
  })

  it('文本与圆点分离：圆点只是辅助，label / slot 才是结论', () => {
    const src = compact(pill)
    expect(src).toContain('aria-hidden="true"')
    expect(src).toContain('<slot>{{label}}</slot>')
  })
})

describe('CheckList', () => {
  it('逐条给符号 + 文字结论 + 说明，缺一不可', () => {
    const src = compact(checks)
    expect(src).toContain("{{item.ok?'✓':'!'}}")
    expect(src).toContain("{{item.ok?passLabel:failLabel}}")
    expect(src).toContain('{{item.message}}')
    expect(src).toContain(':class="item.ok?\'is-ok\':\'is-fail\'"')
    expect(src).toContain('aria-hidden="true"')
  })

  it('空清单有可选占位文案，不渲染空列表', () => {
    const src = compact(checks)
    expect(src).toContain('<ulv-if="items.length"')
    expect(src).toContain('<pv-else-if="emptyText"')
  })

  it('窄屏改成两列，说明整行展开', () => {
    const src = compact(checks)
    expect(src).toContain('@media(max-width:560px)')
    expect(src).toContain('.checks__message{grid-column:1/-1;}')
  })
})

describe('PanelHeader', () => {
  it('头部四件套：图标 + 标题 + 状态胶囊 + 操作区', () => {
    const src = compact(panelHeader)
    expect(src).toContain('<SvgIcon:name="icon":size="15"/>')
    expect(src).toContain('{{title}}')
    expect(src).toContain('v-if="pillLabel"')
    expect(src).toContain('<slotname="actions"/>')
  })

  it('关键事实用 props 传（插槽内容属于父作用域，dt/dd 样式会落不到）', () => {
    const src = compact(panelHeader)
    expect(src).toContain('facts:{type:Array,default:()=>[]}')
    expect(src).toContain('v-for="iteminfacts"')
    expect(src).toContain('<dt>{{item.k}}</dt><dd>{{item.v}}</dd>')
    expect(src).toContain("'is-wide':item.wide")
    expect(src).not.toContain('<slotname="facts"')
  })

  it('说明与提示行分层，提示支持 warn / error 语义色', () => {
    const src = compact(panelHeader)
    expect(src).toContain('panel-header__description')
    expect(src).toContain('panel-header__note')
    expect(src).toContain('.panel-header__note.is-warn')
    expect(src).toContain('.panel-header__note.is-error')
  })

  it('窄屏操作区整行、按钮等宽，长路径换行不撑出横向滚动', () => {
    const src = compact(panelHeader)
    expect(src).toContain('@media(max-width:560px)')
    expect(src).toContain('.panel-header__actions:deep(.btn){flex:110;}')
    expect(src).toContain('overflow-wrap:anywhere')
  })
})
