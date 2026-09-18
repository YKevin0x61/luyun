/**
 * 备份点（Backup Point）纯展示助手：内容类别词表与清理预览摘要。
 *
 * 备份中心把三种介质收拢成一份列表，页面只负责渲染；这里的函数刻意不做任何
 * 请求，也不持有状态，方便在测试里直接固定备份点的形状。
 * 术语固定为：备份点 / 本机回滚快照 / 导出备份 / 冷备。
 */

import { formatBytes, formatTs } from './backupProgress'

export const CONTENT_CREDENTIALS = 'credentials'
export const CONTENT_RUNTIME = 'runtime'
export const CONTENT_APP_DB = 'app_db'
export const CONTENT_RECIPES = 'recipes_db'
export const CONTENT_STANDARD_PHOTOS = 'standard_photos'
export const CONTENT_OTHER_PHOTOS = 'other_photos'

/** 两类卫生照片：标准图（含历史版本）与其它照片，可独立恢复。 */
export const PHOTO_CONTENTS = [CONTENT_STANDARD_PHOTOS, CONTENT_OTHER_PHOTOS]

export const CONTENT_LABELS = {
  [CONTENT_CREDENTIALS]: '凭据',
  [CONTENT_RUNTIME]: '运行配置',
  [CONTENT_APP_DB]: '业务数据',
  [CONTENT_RECIPES]: '配方数据',
  [CONTENT_STANDARD_PHOTOS]: '标准图',
  [CONTENT_OTHER_PHOTOS]: '其它照片',
}

/** 清理预览里一组（本机回滚快照 / 导出备份 / 冷备）的可删项与受保护项。 */
export function cleanupGroupSummary(preview, bucket) {
  const group = preview?.[bucket] || {}
  return {
    keep: group.keep ?? null,
    delete: Array.isArray(group.delete) ? group.delete : [],
    protected: Array.isArray(group.protected) ? group.protected : [],
    kept: Array.isArray(group.kept) ? group.kept : [],
  }
}

/** 清理预览的一句话摘要：三类备份点各将删除多少、共受保护多少。 */
export function cleanupDeleteSummary(preview) {
  const snapshot = cleanupGroupSummary(preview, 'snapshot')
  const exported = cleanupGroupSummary(preview, 'export')
  const cold = cleanupGroupSummary(preview, 'cold')
  return {
    snapshotDelete: snapshot.delete.length,
    exportDelete: exported.delete.length,
    coldDelete: cold.delete.length,
    protected: snapshot.protected.length + exported.protected.length + cold.protected.length,
  }
}

/** 清理预览里「将删除」的名称列表，用于两步确认的具体明细。 */
export function cleanupDeleteNames(preview, bucket) {
  return cleanupGroupSummary(preview, bucket).delete.map(
    (entry) => entry?.ts || entry?.name || entry?.id || '—',
  )
}

/** 照片两条（标准图 / 其它照片）的固定顺序：列表与摘要都按这个顺序渲染。
 *  注意备份点 payload 的 photos 用的是短键 standard / other（不是内容代码）。 */
const PHOTO_KINDS = [
  { kind: 'standard', label: '标准图' },
  { kind: 'other', label: '其它照片' },
]

/**
 * 备份点 → 一行展示模型（纯函数，便于固定形状做单测）。
 *
 * 手动校验结果（validateResult）优先于列表自带的基础校验：刚跑出来的结论更新。
 * 校验列用 tone 表达三态：ok / error / null（未校验由调用方渲染成说明文字）。
 */
export function backupPointRow(point, { mediumLabels = {}, mediumPurposes = {}, validateResult = null } = {}) {
  const check = point?.basic_check || null
  const photos = point?.photos || null
  const checkOk = validateResult ? !!validateResult.ok : (check ? !!check.ok : null)
  const isCold = point?.medium === 'cold_backup'
  const reported = point?.detail?.reported
  // 冷备在页面上只读、不能直接恢复，所以它的结论用「校验」措辞而不是「可恢复」；
  // 目录扫描兜底（reported === false）连校验结论都没有，只能标成未报告。
  let checkTone = null
  let checkLabel = '未校验'
  if (isCold) {
    if (reported === false) {
      checkTone = 'neutral'
      checkLabel = '未报告'
    } else if (checkOk === true) {
      checkTone = 'ok'
      checkLabel = '校验通过'
    } else if (checkOk === false) {
      checkTone = 'error'
      checkLabel = '校验未通过'
    } else {
      checkTone = 'neutral'
    }
  } else if (checkOk === true) {
    checkTone = 'ok'
    checkLabel = '可恢复'
  } else if (checkOk === false) {
    checkTone = 'error'
    checkLabel = '不可恢复'
  }
  return {
    id: point?.id || '',
    medium: point?.medium || '',
    ts: point?.detail?.ts || point?.id || '',
    mediumLabel: point?.medium_label || mediumLabels[point?.medium] || point?.medium || '—',
    purpose: point?.purpose || mediumPurposes[point?.medium] || '—',
    provenanceLabel: point?.provenance_label || '—',
    sizeLabel: formatBytes(point?.size_bytes),
    createdLabel: point?.created_at ? formatTs(point.created_at) : '（时间未知）',
    contentsLabels: Array.isArray(point?.contents_labels) ? point.contents_labels : [],
    missingLabels: (point?.missing || []).map((m) => m.label || m.content).filter(Boolean),
    photoLines: PHOTO_KINDS
      .filter(({ kind }) => photos && photos[kind])
      .map(({ kind, label }) => {
        const info = photos[kind]
        const missing = Number(info.missing) || 0
        return {
          kind,
          label: info.label || label,
          text: `${info.count || 0} 张${missing ? `（缺失引用 ${missing}）` : ''}`,
        }
      }),
    checkOk,
    checkTone,
    checkLabel,
    checkMessages: validateResult?.messages || check?.messages || [],
    checkAt: validateResult?.checked_at || null,
    recoverable: validateResult ? !!validateResult.recoverable : point?.recoverable !== false,
  }
}

/**
 * 按介质把备份点分成三组：本机回滚快照 / 导出备份 / 冷备。
 * 排序沿用后端给出的顺序（新的在前），前端不再排一次。
 */
export function groupBackupPoints(points) {
  const list = Array.isArray(points) ? points : []
  return {
    snapshots: list.filter((p) => p?.medium === 'local_snapshot'),
    exports: list.filter((p) => p?.medium === 'export_backup'),
    cold: list.filter((p) => p?.medium === 'cold_backup'),
    others: list.filter(
      (p) => !['local_snapshot', 'export_backup', 'cold_backup'].includes(p?.medium),
    ),
  }
}

/**
 * 备份点列表的空状态文案。
 *
 * 只有冷备时不能说「还没有备份」——冷备在页面上只读、不能直接恢复，
 * 所以文案要指出「能直接恢复的备份点还没有」。
 */
export function backupPointsEmptyHint(points) {
  const { snapshots, exports, cold, others } = groupBackupPoints(points)
  if (snapshots.length || exports.length || others.length) return ''
  if (cold.length) {
    return '还没有可用于直接恢复的备份点（只有冷备；冷备在页面只读，需在宿主机上恢复）'
  }
  return '还没有可用于恢复的备份，先做一次导出备份或数据回滚'
}
