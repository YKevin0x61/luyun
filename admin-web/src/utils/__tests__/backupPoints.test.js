import { describe, expect, it } from 'vitest'
import {
  CONTENT_LABELS,
  PHOTO_CONTENTS,
  cleanupDeleteNames,
  cleanupDeleteSummary,
  cleanupGroupSummary,
} from '../backupPoints'

describe('backupPoints helpers', () => {
  it('uses the fixed 内容类别 domain words', () => {
    expect(CONTENT_LABELS.credentials).toBe('凭据')
    expect(CONTENT_LABELS.runtime).toBe('运行配置')
    expect(CONTENT_LABELS.app_db).toBe('业务数据')
    expect(CONTENT_LABELS.recipes_db).toBe('配方数据')
    expect(CONTENT_LABELS.standard_photos).toBe('标准图')
    expect(CONTENT_LABELS.other_photos).toBe('其它照片')
  })

  it('treats the two hygiene photo classes as independent', () => {
    expect(PHOTO_CONTENTS).toEqual(['standard_photos', 'other_photos'])
  })

  it('summarises deletions across 本机回滚快照 / 导出备份 / 冷备', () => {
    const preview = {
      snapshot: { keep: 2, delete: [{ ts: '20260101_000001' }], protected: [{ ts: 'x' }] },
      export: { keep: 5, delete: [{ name: 'a.luyunbak' }, { name: 'b.luyunbak' }], protected: [] },
      cold: { keep: 14, delete: [], protected: [{ ts: 'c' }] },
    }
    expect(cleanupDeleteSummary(preview)).toEqual({
      snapshotDelete: 1,
      exportDelete: 2,
      coldDelete: 0,
      protected: 2,
    })
  })

  it('lists the concrete deletions for the two-step confirm', () => {
    const preview = {
      export: { delete: [{ name: 'a.luyunbak' }, { name: 'b.luyunbak' }] },
      snapshot: { delete: [{ ts: '20260101_000001' }] },
    }
    expect(cleanupDeleteNames(preview, 'export')).toEqual(['a.luyunbak', 'b.luyunbak'])
    expect(cleanupDeleteNames(preview, 'snapshot')).toEqual(['20260101_000001'])
  })

  it('handles a missing preview without throwing', () => {
    expect(cleanupDeleteSummary(null)).toEqual({
      snapshotDelete: 0,
      exportDelete: 0,
      coldDelete: 0,
      protected: 0,
    })
    expect(cleanupGroupSummary(undefined, 'cold')).toEqual({
      keep: null,
      delete: [],
      protected: [],
      kept: [],
    })
  })
})
