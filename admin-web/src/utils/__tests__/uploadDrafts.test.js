import { describe, expect, it } from 'vitest'
import {
  createUploadDraftStore,
  deserializeFormData,
  serializeTask,
} from '../uploadDrafts'

function fakeRequest(result) {
  const request = { result, onsuccess: null, onerror: null }
  queueMicrotask(() => request.onsuccess?.())
  return request
}

function createFakeIndexedDB() {
  const rows = new Map()
  const objectStore = {
    put: (record) => {
      rows.set(record.id, record)
      return fakeRequest(record.id)
    },
    delete: (id) => {
      rows.delete(id)
      return fakeRequest(undefined)
    },
    getAll: () => fakeRequest([...rows.values()]),
  }
  const db = {
    objectStoreNames: { contains: () => true },
    createObjectStore: () => {},
    transaction: () => ({ objectStore: () => objectStore }),
  }
  return {
    rows,
    open: () => {
      const request = { result: db, onsuccess: null, onerror: null, onupgradeneeded: null }
      queueMicrotask(() => {
        request.onupgradeneeded?.()
        request.onsuccess?.()
      })
      return request
    },
  }
}

function formDataWith(entries) {
  const form = new FormData()
  for (const [key, value, filename] of entries) {
    if (filename) form.append(key, value, filename)
    else form.append(key, value)
  }
  return form
}

describe('uploadDrafts', () => {
  it('round-trips a queued capture, blob included', () => {
    const blob = new Blob([new Uint8Array([1, 2, 3])], { type: 'image/jpeg' })
    const task = {
      id: 'image-upload-1',
      path: '/api/hygiene/staff/daily/7/submit',
      transport: 'staff',
      label: '日常实拍 · 案板',
      detail: '案板 · 白班',
      createdAt: 123,
      formData: formDataWith([
        ['file', blob, 'capture.jpg'],
        ['live', 'true'],
        ['shift', '白班'],
      ]),
    }

    const record = serializeTask(task)
    expect(record.fields).toEqual([['live', 'true'], ['shift', '白班']])
    expect(record.files).toHaveLength(1)
    expect(record.files[0].key).toBe('file')
    expect(record.files[0].filename).toBe('capture.jpg')

    const rebuilt = deserializeFormData(record)
    expect(rebuilt.get('live')).toBe('true')
    expect(rebuilt.get('shift')).toBe('白班')
    expect(rebuilt.get('file')).toBeTruthy()
  })

  it('keeps both files of a deep-clean pair', () => {
    const task = {
      id: 'image-upload-2',
      path: '/deep',
      transport: 'staff',
      formData: formDataWith([
        ['before', new Blob([new Uint8Array([1])]), 'before.jpg'],
        ['after', new Blob([new Uint8Array([2])]), 'after.jpg'],
        ['live', 'true'],
      ]),
    }
    const record = serializeTask(task)
    expect(record.files.map((file) => file.key)).toEqual(['before', 'after'])
    const rebuilt = deserializeFormData(record)
    expect(rebuilt.get('before')).toBeTruthy()
    expect(rebuilt.get('after')).toBeTruthy()
  })

  it('stores, lists and clears by transport', async () => {
    const factory = createFakeIndexedDB()
    const store = createUploadDraftStore({ indexedDB: factory })

    await store.put({ id: 'a', transport: 'staff', path: '/a' })
    await store.put({ id: 'b', transport: 'admin', path: '/b' })
    expect((await store.list()).map((record) => record.id).sort()).toEqual(['a', 'b'])

    expect(await store.clearByTransport('staff')).toBe(1)
    expect((await store.list()).map((record) => record.id)).toEqual(['b'])

    expect(await store.remove('b')).toBe(true)
    expect(await store.list()).toEqual([])
  })

  it('degrades quietly when IndexedDB is unavailable', async () => {
    // 隐私模式下拿不到 indexedDB：不能因为存不了草稿就让上传本身失败。
    const store = createUploadDraftStore({ indexedDB: null })
    expect(store.available).toBe(false)
    expect(await store.put({ id: 'a' })).toBe(false)
    expect(await store.list()).toEqual([])
    expect(await store.clearByTransport('staff')).toBe(0)
  })
})
