import { api } from './client'

// 工作台「任务」的接口封装（票 01 起立，票 02 接发布/列表）。
//
// 身份口径：管理端页面（`/workbench/tasks*`）走默认的管理端 cookie；员工端
// 「我的任务」的发布/上传/派工目录端点走 `require_any_identity_session` —— 共用
// 电脑上两套 cookie 同时存在时，后端默认选管理端，所以员工页必须显式带
// `?identity=staff`（与损耗登记页 `withIdentity` 同款做法）。`GET /mine` 后端走
// `require_staff_session`，不需要这个参数。

const STAFF_PARAMS = { identity: 'staff' }

function identityParams(staff) {
  return staff ? STAFF_PARAMS : undefined
}

export function getTaskAssignees({ staff = false } = {}) {
  return api.get('/api/tasks/assignees', identityParams(staff))
}

export function getTaskBoard({ countsOnly = false } = {}) {
  return api.get('/api/tasks/board', countsOnly ? { counts_only: 'true' } : undefined)
}

export function getTaskInstance(instanceId, { staff = false } = {}) {
  return api.get(`/api/tasks/instances/${instanceId}`, identityParams(staff))
}

export function getMyTasks() {
  return api.get('/api/tasks/mine')
}

export function createTask(payload, { staff = false } = {}) {
  return api.post('/api/tasks/definitions', payload, identityParams(staff))
}

export function getTaskDefinitions() {
  return api.get('/api/tasks/definitions')
}

export function updateTask(definitionId, payload) {
  return api.patch(`/api/tasks/definitions/${definitionId}`, payload)
}

export function stopTaskDefinition(definitionId) {
  return api.post(`/api/tasks/definitions/${definitionId}/stop`)
}

export function reassignTask(instanceId, assignee, { staff = false } = {}) {
  return api.post(
    `/api/tasks/instances/${instanceId}/reassign`,
    assignee,
    staff ? { identity: 'staff' } : undefined,
  )
}

export function cancelTask(instanceId) {
  return api.post(`/api/tasks/instances/${instanceId}/cancel`)
}

export function acceptTask(instanceId, { staff = false } = {}) {
  return api.post(`/api/tasks/instances/${instanceId}/accept`, null, identityParams(staff))
}

export function rejectTask(instanceId, reason, { staff = false } = {}) {
  return api.post(
    `/api/tasks/instances/${instanceId}/reject`,
    { reason },
    identityParams(staff),
  )
}

export function submitTask(instanceId, captureIds) {
  return api.post(
    `/api/tasks/instances/${instanceId}/submit`,
    { capture_ids: captureIds },
    { identity: 'staff' },
  )
}

/**
 * 上传任务图片。`purpose='submission'` 是完成照（后端要求 live=true，页面相机
 * 拍完才带），`'reference'` 是发布时挂的参考图（允许相册）。
 */
export function uploadTaskImage(file, { purpose = 'submission', live = false, staff = false, onProgress } = {}) {
  const form = new FormData()
  form.append('file', file)
  form.append('purpose', purpose)
  form.append('live', live ? 'true' : 'false')
  const path = staff ? '/api/tasks/images?identity=staff' : '/api/tasks/images'
  return api.upload(path, form, onProgress)
}
