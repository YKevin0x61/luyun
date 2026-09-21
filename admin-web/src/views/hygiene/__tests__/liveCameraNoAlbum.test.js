import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const FILES = [
  join(here, '../HygieneHomeView.vue'),
  join(here, '../HygieneFixView.vue'),
  join(here, '../../../components/hygiene/HygieneLiveCamera.vue'),
]

describe('staff daily capture has no album picker', () => {
  it('uses getUserMedia and has no file/image album input', () => {
    const source = FILES.slice(0, 1).concat(FILES[2]).map((path) => readFileSync(path, 'utf8')).join('\n')
    expect(source).toMatch(/getUserMedia/)
    expect(source).toMatch(/enumerateDevices/)
    expect(source).toMatch(/getCapabilities\(\)/)
    expect(source).toMatch(/applyWideDefault/)
    expect(source).toMatch(/wideMode/)
    expect(source).toMatch(/cameraReady/)
    expect(source).toMatch(/aria-pressed/)
    expect(source).toMatch(/当前镜头不支持广角/)
    expect(source).not.toMatch(/candidates\[/)
    expect(source).toMatch(/object-fit: contain/)
    expect(source).toMatch(/广角/)
    expect(source).toMatch(/type=["']file["']/)
    expect(source).toMatch(/accept=["']image\/\*/)
    expect(source).toMatch(/capture=["']environment["']/)
    expect(source).toMatch(/native-camera-input/)
    expect(source).toMatch(/deep-clean/)
    expect(source).toMatch(/拍清理前/)
  })
})

describe('fix tickets use live camera only', () => {
  it('staff and admin open/reshoot have no album and require getUserMedia', () => {
    const home = readFileSync(FILES[0], 'utf8')
    const admin = readFileSync(FILES[1], 'utf8')
    const camera = readFileSync(FILES[2], 'utf8')
    const source = `${home}\n${admin}\n${camera}`
    expect(source).toMatch(/getUserMedia/)
    expect(admin).not.toMatch(/type=["']file["']/)
    expect(admin).not.toMatch(/accept=["']image/)
    expect(home).toMatch(/开整改单/)
    expect(home).toMatch(/staff\/fix/)
    expect(admin).toMatch(/admin\/fix/)
    expect(admin).toMatch(/此设备没有可用相机/)
    expect(admin).toMatch(/不能选择相册|不能从相册选择/)
    expect(home).toMatch(/镜头不叠图|先看开单原图/)
    expect(home).toMatch(/chinaNowIso/)
    expect(admin).toMatch(/chinaNowIso/)
    expect(home).not.toMatch(/toISOString\(\)/)
    expect(admin).not.toMatch(/toISOString\(\)/)
  })
})

describe('ADR 0050：先看标准图，再开相机（不做同屏分屏）', () => {
  it('标准图弹层里只有标准图与「打开相机」，没有取景框', () => {
    const home = readFileSync(FILES[0], 'utf8')
    const branch = home.slice(
      home.indexOf("sheet.mode === 'standard'"),
      home.indexOf("sheet.mode === 'before-camera'"),
    )
    expect(branch).toMatch(/HygieneStandardOverlay/)
    expect(branch).toMatch(/打开相机/)
    // ADR 0050 明说 "A live split (standard on top, camera on the bottom) is out"：
    // 这里一旦出现取景框就说明又把两步合成一屏了。
    expect(branch).not.toMatch(/HygieneLiveCamera/)
  })
})
