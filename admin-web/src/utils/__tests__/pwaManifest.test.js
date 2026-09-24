import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it, vi } from 'vitest'
import { applyPwaManifest, selectPwaManifest } from '../pwaManifest'

const here = dirname(fileURLToPath(import.meta.url))

describe('pwaManifest', () => {
  it('selects role manifests by route', () => {
    expect(selectPwaManifest('/').role).toBe('admin')
    expect(selectPwaManifest('/hygiene').role).toBe('hygiene')
    expect(selectPwaManifest('/hygiene/login').role).toBe('hygiene')
    expect(selectPwaManifest('/hygiene-roster').role).toBe('admin')
    expect(selectPwaManifest('/recipe/manage').role).toBe('recipe')
    expect(selectPwaManifest('/setup').role).toBe('admin')
  })

  it('员工端的「今天」页挂员工清单，不是管理端那份', () => {
    // 票 05 之后员工落在 /today：挂错清单的话手机上装出来的是「禄云管理」。
    expect(selectPwaManifest('/today').role).toBe('hygiene')
    expect(selectPwaManifest('/today/').role).toBe('hygiene')
    expect(selectPwaManifest('/today').themeColor).toBe('#16a34a')
    expect(selectPwaManifest('/today').manifest).toBe('/pwa/manifests/hygiene.webmanifest')
  })

  it('员工端清单从今天页进，范围盖得住两块', () => {
    // 清单的 start_url/scope 跟页面归属是一件事的两半：start_url 还是 /hygiene
    // 的话，桌面上那个图标点开永远先看到卫生首页（验收 1 说的「不该是第一眼」）。
    const manifest = JSON.parse(
      readFileSync(join(here, '../../../public/pwa/manifests/hygiene.webmanifest'), 'utf8'),
    )
    expect(manifest.start_url).toBe('/today')
    expect(selectPwaManifest(manifest.start_url).role).toBe('hygiene')
    expect(selectPwaManifest('/hygiene').role).toBe('hygiene')
    // scope 要同时罩住 /today 与 /hygiene：只管 /hygiene 的话，从今天页点进卫生会跳出应用。
    expect(manifest.scope).toBe('/')
  })

  it('updates manifest, apple icon and theme color', () => {
    const elements = {
      manifest: { setAttribute: vi.fn() },
      appleIcon: { setAttribute: vi.fn() },
      theme: { setAttribute: vi.fn() },
    }
    const documentRef = {
      getElementById: (id) => {
        if (id === 'app-manifest') return elements.manifest
        if (id === 'app-apple-touch-icon') return elements.appleIcon
        return null
      },
      querySelector: () => elements.theme,
    }

    const selected = applyPwaManifest('/hygiene/home', documentRef)

    expect(selected.role).toBe('hygiene')
    expect(elements.manifest.setAttribute).toHaveBeenCalledWith(
      'href',
      '/pwa/manifests/hygiene.webmanifest',
    )
    expect(elements.appleIcon.setAttribute).toHaveBeenCalledWith(
      'href',
      '/pwa/icons/hygiene-192.png',
    )
    expect(elements.theme.setAttribute).toHaveBeenCalledWith('content', '#16a34a')
  })

  it('ignores an invalid document-like argument', () => {
    expect(applyPwaManifest('/recipe', '/previous-route')).toMatchObject({
      role: 'recipe',
    })
  })
})
