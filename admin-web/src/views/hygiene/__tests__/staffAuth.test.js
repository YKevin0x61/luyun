import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))

function read(rel) {
  return readFileSync(join(here, rel), 'utf8')
}

describe('hygiene staff auth gate', () => {
  it('login and register sit in the staff gate, not the admin subnav', () => {
    const router = read('../../../router/index.js')
    expect(router).toMatch(/hygieneStaffAuthPage\('\/hygiene\/login'/)
    expect(router).toMatch(/hygieneStaffAuthPage\('\/hygiene\/register'/)
    expect(router).not.toMatch(/hygieneAdminPage\('\/hygiene\/login'/)
    expect(router).not.toMatch(/hygieneAdminPage\('\/hygiene\/register'/)
  })

  it('gate loads the shared hygiene stylesheet and the 卫 lockup', () => {
    const layout = read('../HygieneStaffAuthLayout.vue')
    expect(layout).toMatch(/useScopedStylesheet\('\/hygiene-admin\.css'\)/)
    expect(layout).toMatch(/class="hygiene-staff"/)
    expect(layout).toMatch(/HYGIENE_BRAND_MARK/)
    expect(layout).toMatch(/HYGIENE_BRAND_TITLE/)
    expect(layout).toMatch(/跳到内容/)
  })

  it('register and login keep the phone forms and drop the dark staff-phone card', () => {
    const register = read('../HygieneRegisterView.vue')
    const login = read('../HygieneLoginView.vue')
    expect(register).not.toMatch(/staff-phone/)
    expect(login).not.toMatch(/staff-phone/)
    expect(register).toMatch(/员工注册/)
    expect(register).toMatch(/const name = ref\(''\)/)
    expect(register).toMatch(/id="regName"/)
    expect(register).toMatch(/name: name\.value\.trim\(\)/)
    expect(login).toMatch(/员工登录/)
    expect(register).toMatch(/to="\/hygiene\/login"/)
    expect(login).toMatch(/to="\/hygiene\/register"/)
    expect(register).toMatch(/\/api\/hygiene\/staff\/register/)
    expect(login).toMatch(/\/api\/hygiene\/staff\/login/)
  })
})
