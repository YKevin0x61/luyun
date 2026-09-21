import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const zones = readFileSync(join(here, '../HygieneZonesView.vue'), 'utf8')
const home = readFileSync(join(here, '../HygieneHomeView.vue'), 'utf8')

describe('标准图标注编辑（不换图）', () => {
  it('每行都有「编辑标注」入口，且与「换标准图」并存', () => {
    expect(zones).toMatch(/@click="startEditMarkup\(item\)"/)
    expect(zones).toMatch(/编辑标注/)
    expect(zones).toMatch(/@click="startReplace\(item\)"/)
  })

  it('走 PATCH 标注端点，而不是重传图片', () => {
    expect(zones).toMatch(
      /api\.patch\(`\/api\/hygiene\/admin\/items\/\$\{replacingId\.value\}\/standard\/markup`/,
    )
    expect(zones).toMatch(/markup: markup\.value/)
    // 不能在标注模式里退回 FormData 上传
    expect(zones).toMatch(/if \(markupEditing\.value\) \{\s*\n\s*void saveMarkup\(\)/)
  })

  it('标注模式不要求选文件（canSave 的分支顺序）', () => {
    const canSave = zones.slice(
      zones.indexOf('const canSave = computed'),
      zones.indexOf('const missedByZone = computed'),
    )
    expect(canSave).toMatch(/if \(markupEditing\.value\) return Boolean\(replacingId\.value\)/)
    // 这一句必须在 `!file.value` 之前，否则没选文件就永远存不了
    expect(canSave.indexOf('markupEditing.value')).toBeLessThan(canSave.indexOf('!file.value'))
  })

  it('标注模式下隐藏名称与文件选择，并把预览切到现有标准图', () => {
    expect(zones).toMatch(/v-if="!markupEditing" class="editor-field">\s*\n\s*检查项名称/)
    expect(zones).toMatch(/v-if="!markupEditing" class="editor-field">\s*\n\s*标准图/)
    expect(zones).toMatch(/markupEditing \? '编辑标准图标注'/)
    expect(zones).toMatch(/markupEditing \? \(savingMarkup \? '保存中…' : '保存标注'\)/)
  })

  it('说清「会生成新一版、历史记录不受影响」', () => {
    expect(zones).toMatch(/只改标注、不换图/)
    expect(zones).toMatch(/已经交过的记录仍看当时那一版/)
  })

  it('clearEditor / startReplace 会把标注模式关掉，避免串台', () => {
    expect(zones).toMatch(/function clearEditor\(\)[\s\S]*?markupEditing\.value = false/)
    expect(zones).toMatch(/function startReplace\(item\)[\s\S]*?markupEditing\.value = false/)
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
