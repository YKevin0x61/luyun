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
    const zones = read('../HygieneZonesView.vue')

    expect(zones).toMatch(/useImageUploadQueueStore/)
    expect(zones).toMatch(/imageUploads\.enqueue\(/)
    expect(zones).toMatch(/onSuccess: loadZones/)
    expect(zones).not.toMatch(/await api\.upload/)
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
    const login = read('../HygieneLoginView.vue')

    expect(home).toMatch(/clearTasksByTransport\('staff'\)/)
    expect(login).toMatch(/clearTasksByTransport\('staff'\)/)
  })
})
