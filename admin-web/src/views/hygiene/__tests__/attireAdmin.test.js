import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const page = readFileSync(join(here, '../HygieneAttireView.vue'), 'utf8')
const router = readFileSync(join(here, '../../../router/index.js'), 'utf8')
const mainPy = readFileSync(join(here, '../../../../../main.py'), 'utf8')

describe('仪容仪表管理页（票 12）', () => {
  it('读管理端那一条，不碰员工门', () => {
    expect(page).toMatch(/api\.get\('\/api\/hygiene\/admin\/attire'\)/)
    expect(page).not.toMatch(/\/api\/hygiene\/staff\//)
    expect(page).not.toMatch(/employee_id=/)
  })

  it('验收与驳回走管理端那两个动作，驳回必须带原因', () => {
    expect(page).toMatch(/\/api\/hygiene\/admin\/attire\/\$\{row\.employee_id\}\/accept/)
    expect(page).toMatch(/\/api\/hygiene\/admin\/attire\/\$\{employeeId\}\/reject/)
    // 服务端也拒空原因：两边都不许放过去一句「不合格」就驳回。
    expect(page).toMatch(/note: rejectNote\.value\.trim\(\)/)
  })

  it('标准图能传也能看当前那一版', () => {
    expect(page).toMatch(/\/api\/hygiene\/admin\/attire\/standard/)
    expect(page).toMatch(/imageUploads\.enqueue\(/)
    expect(page).toMatch(/standardUrl\('preview'\)/)
    // 上传也要能改标注字段（跟日常标准图同一条路）。
    expect(page).toMatch(/form\.append\('markup', '\[\]'\)/)
  })

  it('名单与计数只用服务端给的那一份', () => {
    // 名单来自排班（`admin_day` 的 people）：页面上不再按花名册自己筛一遍，
    // 否则「休假的不在表上」这条口径就有两份实现。
    expect(page).toMatch(/day\.value\.people/)
    expect(page).toMatch(/counts\.pending/)
    expect(page).not.toMatch(/hygiene_employees/)
  })

  it('实时只订仪容仪表那一档', () => {
    expect(page).toMatch(/useHygieneRealtime\(/)
    expect(page).toMatch(/resources: \['attire'\]/)
  })

  it('路由与后端的 SPA 页清单两边都有它', () => {
    // 少一边就是白屏或硬跳 404（AGENTS.md 里那条两个方向都要相等的规矩）。
    expect(router).toMatch(/hygieneAdminPage\('\/workbench\/floor\/attire'/)
    expect(mainPy).toMatch(/"\/workbench\/floor\/attire"/)
  })
})
