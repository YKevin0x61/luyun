/** 任务完成照的本地草稿（IndexedDB，复用上传草稿那条通道）。
 *
 * 员工在相机里拍完到点「提交」之间，照片只在本机内存里；安卓/iOS 回收 webview、
 * 页面刷新都会丢。这里在拍完的当下就把 Blob 写进 IndexedDB，提交成功再删 ——
 * 刷新回来还能接着传（票 03 验收 ④）。
 *
 * 与 `uploadDrafts.js` 的关系：共用同一个库与 store，只约定 `transport` 前缀，
 * 不另开数据库（一个浏览器只该有一个「待上传」队列）。存不了（隐私模式）时静默
 * 降级，不影响本次提交。
 */
import { createUploadDraftStore } from './uploadDrafts'

export const TASK_DRAFT_TRANSPORT = 'work-task'
export const TASK_DRAFT_PREFIX = 'work-task-draft:'

// 模块级单例：同一页面里多个弹层共用一份连接。
const draftStore = createUploadDraftStore()

export function taskDraftId(instanceId, index) {
  return `${TASK_DRAFT_PREFIX}${instanceId}:${index}`
}

/** 拍完一张先落盘（提交成功前一直留着；`captureId` 有了就带上，重试不重复上传）。 */
export async function saveTaskDraft(instanceId, index, blob, extra = {}) {
  return draftStore.put({
    id: taskDraftId(instanceId, index),
    transport: TASK_DRAFT_TRANSPORT,
    instanceId: Number(instanceId),
    index: Number(index),
    blob,
    filename: 'capture.jpg',
    createdAt: Date.now(),
    captureId: extra.captureId || '',
  })
}

/** 上传成功后回写 capture_id：刷新/重试时这张不再重复上传。 */
export async function markTaskDraftUploaded(instanceId, index, captureId) {
  const records = await listTaskDrafts(instanceId)
  const record = records.find((item) => Number(item.index) === Number(index))
  if (!record) return false
  return draftStore.put({ ...record, captureId: String(captureId || '') })
}

export async function listTaskDrafts(instanceId) {
  const all = await draftStore.list()
  return all
    .filter(
      (record) =>
        record.transport === TASK_DRAFT_TRANSPORT &&
        Number(record.instanceId) === Number(instanceId),
    )
    .sort((a, b) => Number(a.index) - Number(b.index))
}

export async function removeTaskDraft(instanceId, index) {
  return draftStore.remove(taskDraftId(instanceId, index))
}

export async function clearTaskDrafts(instanceId) {
  const records = await listTaskDrafts(instanceId)
  for (const record of records) await draftStore.remove(record.id)
  return records.length
}
