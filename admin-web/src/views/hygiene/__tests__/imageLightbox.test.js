import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))

function read(rel) {
  return readFileSync(join(here, rel), 'utf8')
}

describe('hygiene image lightbox', () => {
  it('renders a closable full-screen image with markup and watermark', () => {
    const lightbox = read('../../../components/hygiene/HygieneImageLightbox.vue')
    const overlay = read('../../../components/hygiene/HygieneMarkupOverlay.vue')
    expect(lightbox).toMatch(/<Teleport to="body">/)
    expect(lightbox).toMatch(/event\.key === 'Escape'/)
    expect(lightbox).toMatch(/HygieneMarkupOverlay/)
    expect(lightbox).toMatch(/HygieneWatermarkOverlay/)
    expect(lightbox).toMatch(/关闭全屏预览/)
    expect(lightbox).toMatch(/HygieneMarkupOverlay :markup="markup" fullscreen/)
    expect(overlay).toMatch(/is-fullscreen/)
    expect(overlay).toMatch(/captionSize/)
    expect(overlay).toMatch(/overflow-wrap: anywhere/)
  })

  it('makes standard photos, review pairs, and fresh captures zoomable', () => {
    const standard = read('../../../components/hygiene/HygieneStandardOverlay.vue')
    const pair = read('../../../components/hygiene/HygieneReviewPair.vue')
    const home = read('../HygieneHomeView.vue')
    expect(standard).toMatch(/openLightbox/)
    expect(standard).toMatch(/editable && zoomable/)
    expect(pair).toMatch(/class="preview-zoom"/)
    expect(pair).toMatch(/openLightbox\(standardSrc/)
    expect(pair).toMatch(/openLightbox\(captureSrc/)
    expect(home).toMatch(/openImageLightbox/)
    expect(home).toMatch(/HygieneImageLightbox/)
  })
})
