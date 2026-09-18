import { describe, expect, it } from 'vitest'
import {
  CONTENT_LABELS,
  PHOTO_CONTENTS,
  backupPointRow,
  backupPointsEmptyHint,
  cleanupDeleteNames,
  cleanupDeleteSummary,
  cleanupGroupSummary,
  groupBackupPoints,
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

const SNAPSHOT = {
  id: 'snapshot:20260901_100000',
  medium: 'local_snapshot',
  medium_label: '本机回滚快照',
  purpose: '用于快速回滚',
  provenance_label: '更新作业前',
  created_at: '2026-09-01T10:00:00+08:00',
  size_bytes: 2 * 1024 * 1024,
  detail: { ts: '20260901_100000', files: ['app.db'] },
  contents: ['app_db'],
  contents_labels: ['业务数据'],
  missing: [{ content: 'standard_photos', label: '标准图' }],
  photos: {
    standard: { count: 12, missing: 2 },
    other: { count: 3, missing: 0 },
  },
  basic_check: { ok: true, messages: [] },
  recoverable: true,
}

describe('backupPointRow', () => {
  it('把备份点渲染成一行所需的全部字段', () => {
    const row = backupPointRow(SNAPSHOT, { mediumLabels: {}, mediumPurposes: {} })
    expect(row.ts).toBe('20260901_100000')
    expect(row.mediumLabel).toBe('本机回滚快照')
    expect(row.provenanceLabel).toBe('更新作业前')
    expect(row.sizeLabel).toBe('2.00 MB')
    expect(row.createdLabel).toBe('2026-09-01 10:00:00')
    expect(row.contentsLabels).toEqual(['业务数据'])
    expect(row.missingLabels).toEqual(['标准图'])
    expect(row.checkTone).toBe('ok')
    expect(row.checkLabel).toBe('可恢复')
    expect(row.recoverable).toBe(true)
  })

  it('照片按「标准图 / 其它照片」固定顺序，并带出缺失引用数', () => {
    const row = backupPointRow(SNAPSHOT)
    expect(row.photoLines.map((line) => line.label)).toEqual(['标准图', '其它照片'])
    expect(row.photoLines[0].text).toBe('12 张（缺失引用 2）')
    expect(row.photoLines[1].text).toBe('3 张')
  })

  it('未校验时给出中性三态，而不是假装通过', () => {
    const row = backupPointRow({ id: 'export:a.luyunbak', medium: 'export_backup' })
    expect(row.checkOk).toBe(null)
    expect(row.checkTone).toBe(null)
    expect(row.checkLabel).toBe('未校验')
    // 没有 created_at 时明确说时间未知，不显示空串
    expect(row.createdLabel).toBe('（时间未知）')
    expect(row.photoLines).toEqual([])
  })

  it('手动校验结果优先于列表里的基础校验，且能翻转可回滚结论', () => {
    const row = backupPointRow(SNAPSHOT, {
      validateResult: {
        ok: false,
        messages: ['归档校验和不一致'],
        recoverable: false,
        checked_at: '2026-09-01T10:06:00+08:00',
      },
    })
    expect(row.checkTone).toBe('error')
    expect(row.checkLabel).toBe('不可恢复')
    expect(row.checkMessages).toEqual(['归档校验和不一致'])
    expect(row.recoverable).toBe(false)
    expect(row.checkAt).toBe('2026-09-01T10:06:00+08:00')
  })

  it('回退到 medium_labels 词表，拿不到任何标签时回落成 medium 原值', () => {
    expect(
      backupPointRow({ medium: 'export_backup' }, { mediumLabels: { export_backup: '导出备份' } }).mediumLabel,
    ).toBe('导出备份')
    expect(backupPointRow({ medium: 'legacy_x' }).mediumLabel).toBe('legacy_x')
    expect(backupPointRow({}).mediumLabel).toBe('—')
  })

  it('冷备只读：结论说「校验」，不说「可恢复」', () => {
    const cold = {
      medium: 'cold_backup',
      detail: { ts: '20260919_030000', reported: true },
      basic_check: { ok: true, messages: [] },
      recoverable: false,
    }
    const ok = backupPointRow(cold)
    expect(ok.checkLabel).toBe('校验通过')
    expect(ok.recoverable).toBe(false)

    const broken = backupPointRow({ ...cold, basic_check: { ok: false, messages: ['归档校验和不一致'] } })
    expect(broken.checkTone).toBe('error')
    expect(broken.checkLabel).toBe('校验未通过')

    // 目录扫描兜底：连校验结论都没有，不能显示成通过或失败
    const unreported = backupPointRow({
      ...cold,
      detail: { ts: '20260918_030000', reported: false },
      basic_check: { ok: false, messages: ['冷备任务未报告校验结论'] },
    })
    expect(unreported.checkTone).toBe('neutral')
    expect(unreported.checkLabel).toBe('未报告')
  })
})

describe('groupBackupPoints / backupPointsEmptyHint', () => {
  it('按介质分组且保持后端给出的顺序', () => {
    const points = [
      { id: 's2', medium: 'local_snapshot' },
      { id: 'e1', medium: 'export_backup' },
      { id: 's1', medium: 'local_snapshot' },
      { id: 'c1', medium: 'cold_backup' },
      { id: 'x1', medium: 'mystery' },
    ]
    const grouped = groupBackupPoints(points)
    expect(grouped.snapshots.map((p) => p.id)).toEqual(['s2', 's1'])
    expect(grouped.exports.map((p) => p.id)).toEqual(['e1'])
    expect(grouped.cold.map((p) => p.id)).toEqual(['c1'])
    expect(grouped.others.map((p) => p.id)).toEqual(['x1'])
    expect(groupBackupPoints(null)).toEqual({ snapshots: [], exports: [], cold: [], others: [] })
  })

  it('只有冷备时明确指出页面不能直接恢复', () => {
    const hint = backupPointsEmptyHint([{ id: 'c1', medium: 'cold_backup' }])
    expect(hint).toContain('冷备')
    expect(hint).toContain('只读')
  })

  it('有可恢复备份点时不给空状态文案', () => {
    expect(backupPointsEmptyHint([{ id: 's1', medium: 'local_snapshot' }])).toBe('')
    expect(backupPointsEmptyHint([])).toContain('还没有可用于恢复的备份')
  })
})
