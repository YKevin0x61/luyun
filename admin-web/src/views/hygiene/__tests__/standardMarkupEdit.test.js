import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
// 2026-10-19：清单整块抽进 `components/hygiene/HygieneZoneItemsPanel.vue`；编辑器再抽一层
// `HygieneItemEditor.vue`（按被编辑项就地渲染）。断言跟各自的文件走。
const items = readFileSync(join(here, '../../../components/hygiene/HygieneZoneItemsPanel.vue'), 'utf8')
const editor = readFileSync(join(here, '../../../components/hygiene/HygieneItemEditor.vue'), 'utf8')
const home = readFileSync(join(here, '../HygieneHomeView.vue'), 'utf8')

describe('标准图标注编辑（不换图）', () => {
  it('每行都有「编辑标注」入口，且与「换标准图」并存', () => {
    expect(items).toMatch(/openEditor\(item, 'markup'\)/)
    expect(items).toMatch(/编辑标注/)
    expect(items).toMatch(/openEditor\(item, 'replace'\)/)
    expect(items).toMatch(/换标准图/)
  })

  it('走 PATCH 标注端点，而不是重传图片', () => {
    expect(editor).toMatch(
      /api\.patch\(`\/api\/hygiene\/admin\/items\/\$\{replacingItem\.value\.id\}\/standard\/markup`/,
    )
    expect(editor).toMatch(/markup: markup\.value/)
    // 不能在标注模式里退回 FormData 上传
    expect(editor).toMatch(/if \(markupEditing\.value\) \{\s*\n\s*void saveMarkup\(\)/)
  })

  it('批注可以改文字（不必删掉重画）', () => {
    // 2026-10-19 用户裁定：标注列表里的批注要能重新编辑文字。
    expect(editor).toMatch(/function startEditCaption\(index\)/)
    expect(editor).toMatch(/function saveCaption\(\)/)
    // 只有批注有文字可改；圆圈/箭头不给入口。
    expect(editor).toMatch(/mark\.kind === 'caption'/)
    expect(editor).toMatch(/>改文字</)
    expect(editor).toMatch(/删除标注/)
    // 保存是替换那一条的 text，不动其它标注。
    expect(editor).toMatch(/markup\.value = markup\.value\.map\(\(mark, i\) => \(i === index \? \{ \.\.\.mark, text \} : mark\)\)/)
  })

  it('标注模式不要求选文件（canSave 的分支顺序）', () => {
    const canSave = editor.slice(
      editor.indexOf('const canSave = computed'),
      editor.indexOf('const title = computed'),
    )
    expect(canSave).toMatch(/if \(markupEditing\.value\) return Boolean\(replacingItem\.value\)/)
    // 这一句必须在 `!file.value` 之前，否则没选文件就永远存不了
    expect(canSave.indexOf('markupEditing.value')).toBeLessThan(canSave.indexOf('!file.value'))
  })

  it('标注模式下隐藏名称与文件选择，并把预览切到现有标准图', () => {
    expect(editor).toMatch(/v-if="!markupEditing" class="editor-field">\s*\n\s*检查项名称/)
    expect(editor).toMatch(/v-if="!markupEditing" class="editor-field">\s*\n\s*标准图/)
    // 标题带上项名（2026-10-19 二次拆分）：编辑标注 · <项名> / 换标准图 · <项名>
    expect(editor).toMatch(/if \(props\.mode === 'markup'\) return `编辑标注 · /)
    expect(editor).toMatch(/if \(props\.mode === 'replace'\) return `换标准图 · /)
    expect(editor).toMatch(/markupEditing \? \(savingMarkup \? '保存中…' : '保存标注'\)/)
  })

  it('说清「会生成新一版、历史记录不受影响」', () => {
    expect(editor).toMatch(/只改标注、不换图/)
    expect(editor).toMatch(/已经交过的记录仍看当时那一版/)
  })

  it('模式由 props 决定、编辑器随项重建：不会把上一项的标注带过来', () => {
    // markupEditing 不再是可变状态，而是由 `mode` 推导；不同项各自挂一个编辑器实例，
    // 挂载时按 item 重新填名称与标注。
    expect(editor).toMatch(/const markupEditing = computed\(\(\) => props\.mode === 'markup'\)/)
    expect(editor).toMatch(/if \(props\.mode !== 'new' && props\.item\) \{/)
    expect(editor).toMatch(/markup\.value = parseMarkup\(props\.item\.markup\)/)
  })

  it('员工端不出现这条管理端路径', () => {
    expect(home).not.toMatch(/standard\/markup/)
  })

  it('员工端收到 standard_updated 会刷新待办（否则看不到新标注）', () => {
    expect(home).toMatch(/if \(event\?\.scope\?\.action === 'standard_updated'\) await loadInbox\(\)/)
    // 已选区的员工原来会被 `!employee.value.zone_id` 挡掉，整条 nudge 被忽略
    const branch = home.slice(home.indexOf("if (resource === 'zones')"))
    expect(branch).toMatch(/if \(!employee\.value\.zone_id\) await loadZones\(\{ force: true \}\)/)
    expect(branch).toMatch(/action === 'standard_updated'/)
  })
})
