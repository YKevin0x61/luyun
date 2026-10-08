// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import IcpFooter from '../IcpFooter.vue'

// 备案号：工信部要求网站在首页底部标明备案号，并链到备案系统。号码只写在
// `IcpFooter.vue` 一处，这里除了断言那一行渲染成什么，还盯住两个挂载点 ——
// 重构把 `<IcpFooter>` 顺手删掉时，这条用例要红。
const ICP_NUMBER = '粤ICP备2025390747号-2'
const ICP_SYSTEM_URL = 'https://beian.miit.gov.cn/'

const here = dirname(fileURLToPath(import.meta.url))

describe('IcpFooter', () => {
  it('渲染备案号，链到工信部备案系统（新窗口 + noopener）', () => {
    const wrapper = mount(IcpFooter)
    const link = wrapper.get('a')
    expect(link.text()).toBe(ICP_NUMBER)
    expect(link.attributes('href')).toBe(ICP_SYSTEM_URL)
    expect(link.attributes('target')).toBe('_blank')
    expect(link.attributes('rel')).toContain('noopener')
  })

  it('两个公开入口（登录 / 注册）与后台主壳各挂一份', () => {
    const login = readFileSync(join(here, '../../views/LoginView.vue'), 'utf8')
    const register = readFileSync(join(here, '../../views/hygiene/HygieneStaffAuthLayout.vue'), 'utf8')
    const app = readFileSync(join(here, '../../App.vue'), 'utf8')
    expect(login).toContain('<IcpFooter')
    expect(register).toContain('<IcpFooter')
    expect(app).toContain('<IcpFooter')
  })
})
