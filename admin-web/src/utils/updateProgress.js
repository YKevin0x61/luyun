/**
 * 更新作业阶段 → 步骤条模型（纯函数，便于固定形状做单测）。
 *
 * 后端的 stage 是「某一时刻的状态」，不是一个进度百分比；这里把它还原成
 * 一条固定顺序的步骤链：备份 → 下载发行包 → 安装发行包 → 同步依赖 →
 * 切换并重启 → 健康确认。
 *
 * 失败是终态（stage 直接变 failed，不带失败发生在哪一步），因此失败时只用
 * 作业里确实落下的时间戳（snapshot_ts / restart_requested_at）判断哪些步骤
 * 真的完成过；判断不出来的步骤一律标 pending，不假装知道。
 */

export const UPDATE_STEPS = [
  { key: 'backing_up', label: '备份', stages: ['backing_up'] },
  { key: 'fetching_bundle', label: '下载发行包', stages: ['fetching_bundle', 'fetching'] },
  { key: 'installing', label: '安装发行包', stages: ['installing', 'installing_assets'] },
  { key: 'syncing_deps', label: '同步依赖', stages: ['syncing_deps'] },
  { key: 'restarting', label: '切换并重启', stages: ['restarting'] },
  { key: 'health', label: '健康确认', stages: [] },
]

const HEALTH_STEP = 'health'

function stageIndex(stage) {
  if (stage === 'queued') return 0
  for (let i = 0; i < UPDATE_STEPS.length; i += 1) {
    if (UPDATE_STEPS[i].stages.includes(stage)) return i
  }
  return -1
}

function baseItems() {
  return UPDATE_STEPS.map((step) => ({
    key: step.key,
    label: step.label,
    state: 'pending',
    at: '',
  }))
}

/**
 * @returns {{
 *   outcome: 'idle'|'running'|'succeeded'|'unhealthy'|'failed',
 *   items: Array<{ key: string, label: string, state: 'done'|'active'|'pending'|'failed', at: string }>,
 *   summary: string,
 *   currentLabel: string,
 * }}
 */
export function updateStageSteps(job) {
  const stage = job?.stage || 'idle'
  const items = baseItems()
  const at = (key) => items.find((item) => item.key === key)

  if (stage === 'idle') {
    return { outcome: 'idle', items, summary: '暂无进行中的更新作业', currentLabel: '' }
  }

  // 终态：成功 = 全部步骤完成；已切换但未健康 = 只有健康确认失败。
  if (stage === 'succeeded') {
    for (const item of items) item.state = 'done'
    at('backing_up').at = job?.snapshot_ts || ''
    at('restarting').at = job?.restart_requested_at || ''
    at(HEALTH_STEP).at = job?.health_confirmed_at || ''
    return {
      outcome: 'succeeded',
      items,
      summary: '已切换到目标版本，健康确认通过',
      currentLabel: '已完成',
    }
  }
  if (stage === 'succeeded_but_unhealthy') {
    for (const item of items) item.state = 'done'
    at(HEALTH_STEP).state = 'failed'
    at('backing_up').at = job?.snapshot_ts || ''
    at('restarting').at = job?.restart_requested_at || ''
    return {
      outcome: 'unhealthy',
      items,
      summary: '已切换并重启，但健康确认未通过',
      currentLabel: '健康确认未通过',
    }
  }

  // 失败：只用真正落下时间戳的步骤当「完成过」，其余保持未完成。
  if (stage === 'failed') {
    if (job?.snapshot_ts) {
      at('backing_up').state = 'done'
      at('backing_up').at = job.snapshot_ts
    }
    if (job?.restart_requested_at) {
      for (const item of items) {
        if (item.key !== HEALTH_STEP && item.state === 'pending') item.state = 'done'
      }
      at('restarting').at = job.restart_requested_at
    }
    return {
      outcome: 'failed',
      items,
      summary: '更新作业失败，已完成步骤保留如下；具体原因见日志',
      currentLabel: '已失败',
    }
  }

  // 进行中：当前阶段之前都是完成，当前阶段高亮。
  const index = stageIndex(stage)
  if (index < 0) {
    return { outcome: 'idle', items, summary: '暂无进行中的更新作业', currentLabel: '' }
  }
  items.forEach((item, i) => {
    if (i < index) item.state = 'done'
    else if (i === index) item.state = 'active'
  })
  // 备份已完成时才标出快照时间戳，避免把上一份快照误读成本次作业的产出。
  if (at('backing_up').state === 'done') at('backing_up').at = job?.snapshot_ts || ''

  const current = items[index]
  return {
    outcome: 'running',
    items,
    summary: `正在${current.label}（第 ${index + 1}/${items.length} 步）`,
    currentLabel: current.label,
  }
}

/** 步骤符号：符号 + 文字双通道，颜色只是辅助。 */
export function stepSymbol(state) {
  if (state === 'done') return '✓'
  if (state === 'failed') return '✗'
  if (state === 'active') return '●'
  return '○'
}
