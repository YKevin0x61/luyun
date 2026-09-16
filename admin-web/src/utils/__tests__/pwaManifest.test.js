import { describe, expect, it, vi } from 'vitest'
import { applyPwaManifest, selectPwaManifest } from '../pwaManifest'

describe('pwaManifest', () => {
  it('selects role manifests by route', () => {
    expect(selectPwaManifest('/').role).toBe('admin')
    expect(selectPwaManifest('/hygiene').role).toBe('hygiene')
    expect(selectPwaManifest('/hygiene/login').role).toBe('hygiene')
    expect(selectPwaManifest('/hygiene-roster').role).toBe('admin')
    expect(selectPwaManifest('/recipe/manage').role).toBe('recipe')
    expect(selectPwaManifest('/setup').role).toBe('admin')
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
