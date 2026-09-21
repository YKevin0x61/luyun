import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { buildWorkQueue } from '../hygieneWorkFlow.js'

const here = dirname(fileURLToPath(import.meta.url))
const home = readFileSync(
  join(here, '../../views/hygiene/HygieneHomeView.vue'),
  'utf8',
)

const DAILY_ROW = {
  item_id: 7,
  shift: '白班',
  item_name: '案板-台面',
  zone_name: '案板',
  status: '待拍',
  business_date: '2026-09-13',
}

const DEEP_ROW = {
  item_id: 3,
  item_name: '抽油烟机',
  status: '待拍',
  business_date: '2026-09-13',
}

const FIX_ROW = {
  id: 11,
  zone_name: '馅档',
  ticket_type: '卫生',
  body_text: '台面有油',
  status: '待回拍',
  deadline: '2026-09-13T12:00:00+08:00',
}

describe('待办里的「已入队但还没确认」', () => {
  it('hides a task from the queue once it is queued for upload', () => {
    // 3G 下一张照片要传几十秒。不藏起来的话，员工切回待办会看到"还没拍"，
    // 于是把同一项再拍一遍。
    const before = buildWorkQueue({ inbox: [DAILY_ROW] })
    expect(before.map((task) => task.key)).toEqual(['daily:7:白班'])

    const after = buildWorkQueue({
      inbox: [DAILY_ROW],
      pendingKeys: new Set(['daily:7:白班']),
    })
    expect(after).toHaveLength(0)
  })

  it('covers deep-clean and fix tickets with the same key scheme', () => {
    expect(buildWorkQueue({ deepInbox: [DEEP_ROW] }).map((task) => task.key))
      .toEqual(['deep:3'])
    expect(buildWorkQueue({
      deepInbox: [DEEP_ROW],
      pendingKeys: new Set(['deep:3']),
    })).toHaveLength(0)

    expect(buildWorkQueue({ fixInbox: [FIX_ROW] }).map((task) => task.key))
      .toEqual(['fix:11'])
    expect(buildWorkQueue({
      fixInbox: [FIX_ROW],
      pendingKeys: new Set(['fix:11']),
    })).toHaveLength(0)
  })

  it('ignores a missing or bogus pendingKeys value', () => {
    expect(buildWorkQueue({ inbox: [DAILY_ROW], pendingKeys: null })).toHaveLength(1)
    expect(buildWorkQueue({ inbox: [DAILY_ROW], pendingKeys: ['daily:7:白班'] }))
      .toHaveLength(1)
  })
})

describe('提交提示不再谎报「已交」', () => {
  it('says uploaded, not submitted, while the request is still in flight', () => {
    expect(home).not.toMatch(/已交，下一项/)
    expect(home).toMatch(/已上传，下一项：/)
    expect(home).toMatch(/已上传，下一张对照/)
  })

  it('derives the pending marks from the queue so a reload cannot lose them', () => {
    // 不能是组件级 ref：刷新页面/回收 webview 后草稿会被恢复继续传，那时局部状态
    // 已经没了，这一项会重新出现在待办里——员工就会重拍一遍，正好是这套机制要防的。
    expect(home).toMatch(/const pendingKeys = computed\(\(\) => new Set\(/)
    expect(home).toMatch(/imageUploads\.activeTasks/)
    expect(home).not.toMatch(/const pendingKeys = ref\(new Set\(\)\)/)
    expect(home).toMatch(/pendingKeys: pendingKeys\.value/)
  })

  it('tags every queued submit with its queue key', () => {
    expect(home).toMatch(/const pendingKey = `daily:\$\{row\.item_id\}:\$\{row\.shift\}`/)
    expect(home).toMatch(/imageUploads\.enqueue\(\{\n      pendingKey,/)
    // store 侧要把这个键落进任务与草稿
    const store = readFileSync(
      join(here, '../../stores/imageUploadQueue.js'),
      'utf8',
    )
    expect(store).toMatch(/pendingKey: pendingKey \|\| ''/)
    expect(store).toMatch(/pendingKey: record\.pendingKey \|\| ''/)
  })

  it('names the item when the upload really fails', () => {
    expect(home).toMatch(/function notifySubmitFailed\(label\)/)
    expect(home).toMatch(/上传失败，可在上传列表里重试/)
  })
})
