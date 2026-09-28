import { existsSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))

function read(rel) {
  return readFileSync(join(here, rel), 'utf8')
}

describe('hygiene staff auth gate', () => {
  it('员工鉴权排印布局只剩注册页，员工登录页不再是一条路由', () => {
    const router = read('../../../router/index.js')
    expect(router).toMatch(/hygieneStaffAuthPage\('\/register'/)
    // 票 03：员工登录页并入 `/login` 的员工栏，路径与视图一起删掉。
    expect(router).not.toMatch(/hygieneStaffAuthPage\('\/hygiene\/login'/)
    expect(router).not.toMatch(/hygieneAdminPage\('\/hygiene\/login'/)
    expect(router).not.toMatch(/hygieneAdminPage\('\/register'/)
    expect(existsSync(join(here, '../HygieneLoginView.vue'))).toBe(false)
    // `/login` 仍是公开的独立页（不显示主导航、不连实时）。
    expect(router).toMatch(/path: '\/login'[\s\S]{0,200}?standalone: true, public: true/)
  })

  it('gate loads the shared hygiene stylesheet and the 卫 lockup', () => {
    const layout = read('../HygieneStaffAuthLayout.vue')
    expect(layout).toMatch(/useScopedStylesheet\('\/hygiene-admin\.css'\)/)
    expect(layout).toMatch(/class="hygiene-staff"/)
    expect(layout).toMatch(/HYGIENE_BRAND_MARK/)
    expect(layout).toMatch(/HYGIENE_BRAND_TITLE/)
    expect(layout).toMatch(/跳到内容/)
  })

  it('注册页保留手机表单，员工那套手机表单搬进 /login 的员工栏', () => {
    const register = read('../HygieneRegisterView.vue')
    const login = read('../../LoginView.vue')
    expect(register).not.toMatch(/staff-phone/)
    expect(login).not.toMatch(/staff-phone/)
    expect(register).toMatch(/员工注册/)
    expect(register).toMatch(/const name = ref\(''\)/)
    expect(register).toMatch(/id="regName"/)
    expect(register).toMatch(/name: name\.value\.trim\(\)/)
    expect(register).toMatch(/\/api\/hygiene\/staff\/register/)
    // 注册页（票 02 起在顶层 /register）回登录页的入口：票 03 起是 `/login` 的员工栏。
    expect(register).toMatch(/to="\/login"/)
    expect(register).not.toMatch(/to="\/hygiene\/login"/)
    // 员工表单（手机号 + 密码）搬进了 `/login` 的员工栏，仍打员工登录接口。
    expect(login).toMatch(/id="staffPhone"/)
    expect(login).toMatch(/type="tel"/)
    expect(login).toMatch(/\/api\/hygiene\/staff\/login/)
    expect(login).toMatch(/to="\/register"/)
  })

  it('register 成功后仍说明要等超级管理员批准，并给回登录页的入口', () => {
    const register = read('../HygieneRegisterView.vue')
    expect(register).toMatch(/v-if="submitted"/)
    expect(register).toMatch(/等超级管理员在花名册里批准后再登录/)
  })
})
