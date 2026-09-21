/** 未完成上传任务的本地暂存（IndexedDB）。
 *
 * 上传队列本体只在内存里：页面刷新、PWA 更新、安卓/iOS 回收 webview 都会把员工
 * 刚拍的照片一起丢掉，而后台连记录都没有（请求还没发出去）。这里只存"还没传
 * 成功"的任务，成功或主动移除时立刻删；下次打开页面自动续传。
 *
 * 隐私模式/老浏览器拿不到 indexedDB 时静默降级成"不暂存"——不能因为存不了草稿
 * 就让本次上传失败。
 */

const DB_NAME = 'luyun-uploads'
const DB_VERSION = 1
const STORE = 'pending'

function isFileLike(value) {
  if (typeof Blob !== 'undefined' && value instanceof Blob) return true
  return typeof File !== 'undefined' && value instanceof File
}

/** FormData → 可结构化克隆的记录（Blob 可以直接进 IndexedDB）。 */
export function serializeTask(task) {
  const fields = []
  const files = []
  for (const [key, value] of task.formData.entries()) {
    if (isFileLike(value)) {
      files.push({ key, blob: value, filename: value.name || 'capture.jpg' })
    } else {
      fields.push([key, String(value)])
    }
  }
  return {
    id: task.id,
    path: task.path,
    transport: task.transport,
    label: task.label,
    detail: task.detail,
    fields,
    files,
    // 把「这一项对应哪条待办」与已试次数一起落盘：恢复后待办才能继续把它当"交过了"，
    // 重试预算也不会因为刷新而重置。
    pendingKey: task.pendingKey || '',
    attempt: Number(task.attempt) || 0,
    createdAt: task.createdAt || Date.now(),
  }
}

export function deserializeFormData(record) {
  const form = new FormData()
  for (const [key, value] of record.fields || []) form.append(key, value)
  for (const file of record.files || []) {
    form.append(file.key, file.blob, file.filename || 'capture.jpg')
  }
  return form
}

function openDb(factory) {
  return new Promise((resolve, reject) => {
    const request = factory.open(DB_NAME, DB_VERSION)
    request.onupgradeneeded = () => {
      const db = request.result
      if (!db.objectStoreNames.contains(STORE)) {
        db.createObjectStore(STORE, { keyPath: 'id' })
      }
    }
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })
}

function runRequest(db, mode, action) {
  return new Promise((resolve, reject) => {
    // action 拿到的是 objectStore（事务在回调外开，否则事务会因 await 提前失活）。
    const request = action(db.transaction(STORE, mode).objectStore(STORE))
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })
}

/**
 * @param {object} [options]
 * @param {IDBFactory|null} [options.indexedDB] 注入点，测试用；缺省取全局。
 */
export function createUploadDraftStore({ indexedDB: factory = null } = {}) {
  const resolved = factory || (typeof globalThis !== 'undefined' ? globalThis.indexedDB : null)
  let dbPromise = null

  function db() {
    if (!resolved) return Promise.resolve(null)
    if (!dbPromise) {
      dbPromise = openDb(resolved).catch(() => null)
    }
    return dbPromise
  }

  async function put(record) {
    const handle = await db()
    if (!handle) return false
    try {
      await runRequest(handle, 'readwrite', (store) => store.put(record))
      return true
    } catch {
      return false
    }
  }

  async function remove(id) {
    const handle = await db()
    if (!handle) return false
    try {
      await runRequest(handle, 'readwrite', (store) => store.delete(id))
      return true
    } catch {
      return false
    }
  }

  async function list() {
    const handle = await db()
    if (!handle) return []
    try {
      const all = await runRequest(handle, 'readonly', (store) => store.getAll())
      return Array.isArray(all) ? all : []
    } catch {
      return []
    }
  }

  async function clearByTransport(transport) {
    const records = await list()
    const matching = records.filter((record) => record.transport === transport)
    for (const record of matching) await remove(record.id)
    return matching.length
  }

  async function dropAll() {
    const records = await list()
    for (const record of records) await remove(record.id)
    return records.length
  }

  return { put, remove, list, clearByTransport, dropAll, available: Boolean(resolved) }
}
