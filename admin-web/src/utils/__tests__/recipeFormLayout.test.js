import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const FORM = join(here, '../../components/recipe/RecipeFormModal.vue')
const MANAGE = join(here, '../../views/recipe/RecipeManageView.vue')
const DETAIL = join(here, '../../views/recipe/RecipeDetailView.vue')

describe('recipe form editor markup', () => {
  const src = readFileSync(FORM, 'utf8')

  it('puts the recipe name before section and servings', () => {
    const name = src.indexOf('for="recipe-form-name"')
    const section = src.indexOf('for="recipe-form-section"')
    const servings = src.indexOf('id="recipe-form-servings-label"')
    expect(name).toBeGreaterThan(0)
    expect(section).toBeGreaterThan(name)
    expect(servings).toBeGreaterThan(section)
  })

  it('keeps 新品 beside the name, not as a following full-width row', () => {
    const rowOpen = src.indexOf('class="recipe-form-name-row"')
    const rowClose = src.indexOf('</div>', rowOpen)
    const flag = src.indexOf('recipe-form-new-flag', rowOpen)
    expect(flag).toBeGreaterThan(rowOpen)
    expect(flag).toBeLessThan(rowClose)
  })

  it('opens as a pass overlay, not a sop-panel card', () => {
    const start = src.indexOf('<!-- 弹窗：新增/编辑配方 -->')
    const end = src.indexOf('v-if="confirmDialog.open"')
    const chunk = src.slice(start, end)
    expect(start).toBeGreaterThan(0)
    expect(end).toBeGreaterThan(start)
    expect(chunk).toContain('recipe-form-overlay')
    expect(chunk.includes('sop-panel')).toBe(false)
  })
})

describe('manage recipe form wiring', () => {
  const src = readFileSync(MANAGE, 'utf8')

  it('opens a recipe from query via slug and edit', () => {
    expect(src).toContain('applyManageQuery')
    expect(src).toContain('route.query.edit')
    expect(src).toContain('openEditRecipe')
    expect(src).toContain('RecipeFormModal')
  })
})

describe('reader recipe form wiring', () => {
  const src = readFileSync(DETAIL, 'utf8')

  it('edits in place with RecipeFormModal instead of navigating away', () => {
    expect(src).toContain('RecipeFormModal')
    expect(src).toContain('onReaderEdit')
    expect(src).not.toContain('recipeManageEditLocation')
    expect(src).not.toContain('useRouter')
  })
})
