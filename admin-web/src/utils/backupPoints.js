/**
 * 备份点（Backup Point）纯展示助手：内容类别词表与清理预览摘要。
 *
 * 备份中心把三种介质收拢成一份列表，页面只负责渲染；这里的函数刻意不做任何
 * 请求，也不持有状态，方便在测试里直接固定备份点的形状。
 * 术语固定为：备份点 / 本机回滚快照 / 导出备份 / 冷备。
 */

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
