import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))

function read(rel) {
  return readFileSync(join(here, rel), 'utf8')
}

describe('hygiene image upload queue', () => {
  it('mounts one global queue panel for background uploads', () => {
    const app = read('../../../App.vue')
    const panel = read('../../../components/ImageUploadQueuePanel.vue')

    expect(app).toMatch(/<ImageUploadQueuePanel \/>/)
    expect(panel).toMatch(/排队中/)
    expect(panel).toMatch(/服务端处理中/)
    expect(panel).toMatch(/role="progressbar"/)
    expect(panel).toMatch(/store\.retry\(task\.id\)/)
  })

  it('queues standard photos instead of blocking the admin editor', () => {
    // 2026-10-19：标准图上传在编辑组件里（桌面右栏与手机独立页共用同一个编辑器）。
    const editor = read('../../../components/hygiene/HygieneItemEditor.vue')

    expect(editor).toMatch(/useImageUploadQueueStore/)
    expect(editor).toMatch(/imageUploads\.enqueue\(/)
    expect(editor).toMatch(/onSuccess: \(\) => emit\('saved'\)/)
    expect(editor).not.toMatch(/await api\.upload/)
  })

  it('lets staff continue shooting while earlier captures upload', () => {
    const home = read('../HygieneHomeView.vue')
    const fix = read('../HygieneFixView.vue')

    expect(home).toMatch(/useImageUploadQueueStore/)
    expect(home).toMatch(/transport: 'staff'/)
    expect(home).toMatch(/label: `日常实拍/)
    expect(home).toMatch(/continueDaily\(row\)/)
    expect(home).not.toMatch(/staffUploadWithProgress/)
    expect(fix).toMatch(/imageUploads\.enqueue\(/)
    expect(fix).not.toMatch(/await api\.upload/)
  })

  it('clears staff uploads when the staff session changes', () => {
    const home = read('../HygieneHomeView.vue')
    // 票 03 起员工登录页并入 `/login` 的员工栏：登录成功与退出登录都要清上一个人的草稿。
    const login = read('../../LoginView.vue')

    expect(home).toMatch(/clearTasksByTransport\('staff'\)/)
    expect(login).toMatch(/clearTasksByTransport\('staff'\)/)
  })
})
