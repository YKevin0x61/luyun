import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const camera = readFileSync(
  join(here, '../../../components/hygiene/HygieneLiveCamera.vue'),
  'utf8',
)

describe('抓拍要降采样再上传', () => {
  it('draws the frame scaled to a 1600px long edge, not at native size', () => {
    expect(camera).toMatch(/const CAPTURE_LONG_EDGE = 1600/)
    expect(camera).toMatch(/const CAPTURE_QUALITY = 0\.75/)
    // 关键：画布就是目标尺寸，视频帧直接缩放绘制进去
    expect(camera).toMatch(/canvas\.width = width/)
    expect(camera).toMatch(/canvas\.height = height/)
    expect(camera).toMatch(/drawImage\(video, 0, 0, width, height\)/)
    expect(camera).toMatch(/Math\.min\(\s*1,\s*CAPTURE_LONG_EDGE \/ Math\.max\(sourceWidth, sourceHeight\)/)
  })

  it('no longer paints a full-size canvas at q0.92', () => {
    expect(camera).not.toMatch(/canvas\.width = video\.videoWidth \|\| 1280/)
    expect(camera).not.toMatch(/'image\/jpeg', 0\.92/)
  })

  it('keeps the encoder quality readable from one place', () => {
    expect(camera).toMatch(/toBlob\(resolve, 'image\/jpeg', CAPTURE_QUALITY\)/)
  })
})

describe('原相机分支要先挡住超限文件', () => {
  it('checks the file size against the server limit before emitting', () => {
    expect(camera).toMatch(/const MAX_UPLOAD_BYTES = 20 \* 1024 \* 1024/)
    expect(camera).toMatch(/if \(file\.size > MAX_UPLOAD_BYTES\)/)
    expect(camera).toMatch(/超过 20MB 上限/)
  })

  it('does not disable the shutter while telling you to use it', () => {
    // 提示写进 errorText 的话，「拍一张」「广角」都会被 Boolean(errorText) 禁掉，
    // 而这条提示恰恰是让店员改用「拍一张」的——自相矛盾且只能退出重进。
    expect(camera).toMatch(/const hintText = ref\(''\)/)
    expect(camera).toMatch(/hintText\.value = `这张照片/)
    expect(camera).not.toMatch(/errorText\.value = `这张照片/)
    expect(camera).toMatch(/v-else-if="hintText"/)
  })

  it('clears the input before the early return so the same file can be re-picked', () => {
    const handler = camera.match(/function onNativeCameraChange[\s\S]*?\n\}/)[0]
    const clearAt = handler.indexOf('input.value =')
    const guardAt = handler.indexOf('file.size > MAX_UPLOAD_BYTES')
    expect(clearAt).toBeGreaterThan(-1)
    expect(clearAt).toBeLessThan(guardAt)
  })
})
