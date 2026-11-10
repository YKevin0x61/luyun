// @vitest-environment node
import { readFileSync } from 'node:fs'
import { expect, it } from 'vitest'

/**
 * 岗位二维码页的读面（2026-11-10 用户裁定：二维码页开只读给勾了「岗位二维码」的管理员）。
 *
 * 页面不能再打 `/api/recipes/stations`：那是阅读面（任一身份），但二维码页要按能力
 * 而不是按身份放行，后端专门开了 `/api/recipes/qr-stations`（员工会话无能力 → 403）。
 * 这里钉住页面用的是那条最小读面，别改回旧端点。
 */
const source = readFileSync(new URL('../RecipeQrView.vue', import.meta.url), 'utf8')

it('岗位二维码页走 /api/recipes/qr-stations（不是旧的 /api/recipes/stations）', () => {
  expect(source).toContain("api.get('/api/recipes/qr-stations')")
  expect(source).not.toContain("api.get('/api/recipes/stations')")
})
