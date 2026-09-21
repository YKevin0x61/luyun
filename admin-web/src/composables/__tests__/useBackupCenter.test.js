import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiGet = vi.fn()
const apiPost = vi.fn()
const apiPut = vi.fn()
const apiUpload = vi.fn()
const downloadPost = vi.fn()

vi.mock('../../api/client', () => ({
  api: {
    get: (...args) => apiGet(...args),
    post: (...args) => apiPost(...args),
    put: (...args) => apiPut(...args),
    upload: (...args) => apiUpload(...args),
    downloadPost: (...args) => downloadPost(...args),
  },
}))

const { useBackupCenter } = await import('../useBackupCenter.js')

function makeHarness() {
  return useBackupCenter({ showAlert: vi.fn(), clearAlert: vi.fn() })
}

function healthPayload(overrides = {}) {
  return {
    success: true,
    health: {
      status: 'ok',
      summary: '最近备份点校验通过，可恢复',
      next_step: '保持定期导出并复制到别处；需要时可在本页恢复',
      last_success_at: '2026-09-01T10:00:00+08:00',
      last_success_medium: 'local_snapshot',
      last_success_medium_label: '本机回滚快照',
      coverage: ['credentials', 'app_db'],
      coverage_labels: ['凭据', '业务数据'],
      checks: [
        { code: 'has_backup', ok: true, message: '存在备份点' },
        { code: 'has_usable_backup', ok: true, message: '1 个备份点校验通过' },
        { code: 'photos_covered', ok: false, message: '可用备份点缺少卫生照片' },
      ],
      counts: { total: 2, usable: 1, unusable: 1, by_medium: { local_snapshot: 1, export_backup: 1, cold_backup: 0 } },
      total_bytes: 2048,
      computed_at: '2026-09-01T10:05:00+08:00',
      ...overrides,
    },
  }
}

function pointsPayload(overrides = {}) {
  return {
    success: true,
    points: [
      {
        id: 'snapshot:20260901_100000',
        medium: 'local_snapshot',
        medium_label: '本机回滚快照',
        purpose: '快速回滚',
        location: 'data/backups/snapshots/20260901_100000',
        created_at: '2026-09-01T10:00:00+08:00',
        provenance: 'pre_update',
        provenance_label: '更新作业前',
        size_bytes: 1024,
        contents: ['credentials', 'app_db'],
        contents_labels: ['凭据', '业务数据'],
        photos: { standard: { count: 0, bytes: 0, included: false, missing: 2 }, other: { count: 0, bytes: 0, included: false, missing: 0 } },
        restorable: true,
        missing: [{ content: 'standard_photos', label: '标准图' }],
        basic_check: { ok: true, messages: [] },
        recoverable: true,
        detail: { ts: '20260901_100000', files: ['app.db'] },
      },
      {
        id: 'export:luyun_backup_20260901_090000.luyunbak',
        medium: 'export_backup',
        medium_label: '导出备份',
        purpose: '离机保存',
        location: 'data/backups/exports/luyun_backup_20260901_090000.luyunbak',
        created_at: '2026-09-01T09:00:00+08:00',
        provenance: 'manual',
        provenance_label: '手动',
        size_bytes: 4096,
        contents: ['credentials', 'app_db'],
        contents_labels: ['凭据', '业务数据'],
        photos: {},
        restorable: true,
        missing: [],
        basic_check: { ok: false, messages: ['归档校验和不一致，文件可能被改动'] },
        recoverable: false,
        detail: { name: 'luyun_backup_20260901_090000.luyunbak' },
      },
      {
        id: 'cold:20260901_080000',
        medium: 'cold_backup',
        medium_label: '冷备',
        purpose: '宿主机定时产出',
        location: '/srv/luyun/cold/20260901_080000.tar',
        created_at: null,
        provenance: 'manual',
        provenance_label: '冷备目录（任务未报告）',
        size_bytes: 8192,
        contents: ['app_db'],
        contents_labels: ['业务数据'],
        photos: {},
        restorable: false,
        missing: [],
        basic_check: { ok: false, messages: ['冷备任务未报告校验结论'] },
        recoverable: false,
        detail: { cold_scan: { ts: '20260901_080000' }, read_only: true, reported: false },
      },
    ],
    health: healthPayload().health,
    not_backed_up: [
      { name: '日志库', reason: '写入量大且非业务数据，回滚它没有意义' },
    ],
    medium_labels: { local_snapshot: '本机回滚快照', export_backup: '导出备份', cold_backup: '冷备' },
    medium_purposes: { local_snapshot: '快速回滚', export_backup: '离机保存', cold_backup: '宿主机定时产出' },
    ...overrides,
  }
}

function previewPayload(overrides = {}) {
  return {
    success: true,
    import_token: 'tok-1',
    meta: {
      exported_at: '2026-09-01T09:00:00+08:00',
      app_version: '0.2.0',
      includes: { runtime: false, app_db: true, recipes_db: true, standard_photos: false, other_photos: true },
    },
    includes: { runtime: false, app_db: true, recipes_db: true, standard_photos: false, other_photos: true },
    credentials_preview: { phone_masked: '138****0000', shop_id: '100001', company_id: '200002', shop_name: 'LuckIn' },
    has_runtime: false,
    has_app_db: true,
    has_recipes: true,
    photos: {
      standard: { label: '标准图', included: false, count: 0, bytes: 0, declared_count: 0, missing_references: 0 },
      other: { label: '其它照片', included: true, count: 3, bytes: 123, declared_count: 3, missing_references: 0 },
    },
    missing: [{ content: 'runtime', label: '运行配置' }],
    validation: {
      in_backup: { ok: true, errors: [] },
      cross_point: {
        has_difference: true,
        missing: { standard: ['a.jpg', 'b.jpg'] },
        standard_missing: 2,
        other_missing: 0,
      },
    },
    restore_allowed: true,
    requires_force: true,
    default_apply: {
      credentials: true,
      runtime: false,
      app_db: true,
      recipes_db: true,
      standard_photos: false,
      other_photos: true,
    },
    pre_snapshot_provenance: 'pre_import',
    ...overrides,
  }
}

const exportPoint = {
  id: 'snapshot:20260901_100000',
  medium: 'local_snapshot',
  medium_label: '本机回滚快照',
  provenance_label: '更新作业前',
  recoverable: true,
  contents: ['credentials', 'app_db', 'recipes_db', 'standard_photos', 'other_photos'],
  contents_labels: ['凭据', '业务数据', '配方数据', '标准图', '其它照片'],
  detail: { ts: '20260901_100000' },
  size_bytes: 1024,
  created_at: '2026-09-01T10:00:00+08:00',
}

describe('useBackupCenter', () => {
  beforeEach(() => {
    apiGet.mockReset()
    apiPost.mockReset()
    apiPut.mockReset()
    apiUpload.mockReset()
    downloadPost.mockReset()
  })

  it('loads 备份健康 and exposes render-ready summary / checks / counts', async () => {
    apiGet.mockResolvedValue(healthPayload())
    const showAlert = vi.fn()
    const { health, healthLoading, healthError, healthView, loadHealth } = useBackupCenter({
      showAlert,
      clearAlert: vi.fn(),
    })

    await loadHealth()

    expect(apiGet).toHaveBeenCalledWith('/api/backup/health', null, null, 'no-store')
    expect(healthLoading.value).toBe(false)
    expect(healthError.value).toBe('')
    expect(health.value.status).toBe('ok')
    expect(healthView.value.summary).toContain('校验通过')
    expect(healthView.value.next_step).toContain('定期导出')
    expect(healthView.value.checks).toHaveLength(3)
    expect(healthView.value.counts.usable).toBe(1)
    expect(showAlert).not.toHaveBeenCalled()
  })

  it('refreshes 备份健康 through POST /health/refresh and reports failures', async () => {
    apiPost.mockResolvedValueOnce(healthPayload({ status: 'no_backup', summary: '还没有可用于恢复的备份' }))
    const showAlert = vi.fn()
    const { health, refreshHealth, loadHealth, healthError } = useBackupCenter({
      showAlert,
      clearAlert: vi.fn(),
    })

    await refreshHealth()
    expect(apiPost).toHaveBeenCalledWith('/api/backup/health/refresh', {})
    expect(health.value.status).toBe('no_backup')

    apiGet.mockRejectedValueOnce(new Error('网络错误'))
    await loadHealth()
    expect(healthError.value).toContain('网络错误')
  })

  it('splits 备份点 list by medium and marks 冷备 as read-only / unreported', async () => {
    apiGet.mockResolvedValue(pointsPayload())
    const { points, snapshotPoints, exportPoints, coldPoints, notBackedUp, pointDisplay, loadPoints } =
      makeHarness()

    await loadPoints()

    expect(points.value).toHaveLength(3)
    expect(snapshotPoints.value.map((p) => p.id)).toEqual(['snapshot:20260901_100000'])
    expect(exportPoints.value.map((p) => p.id)).toEqual(['export:luyun_backup_20260901_090000.luyunbak'])
    expect(coldPoints.value.map((p) => p.id)).toEqual(['cold:20260901_080000'])
    expect(notBackedUp.value[0].name).toBe('日志库')
    expect(notBackedUp.value[0].reason).toContain('回滚它没有意义')

    const snapshot = pointDisplay(points.value[0])
    expect(snapshot.mediumLabel).toBe('本机回滚快照')
    expect(snapshot.provenanceLabel).toBe('更新作业前')
    expect(snapshot.contentsLabels).toEqual(['凭据', '业务数据'])
    expect(snapshot.missingLabels).toEqual(['标准图'])
    expect(snapshot.recoverable).toBe(true)
    expect(snapshot.checkOk).toBe(true)

    const cold = pointDisplay(points.value[2])
    expect(cold.mediumLabel).toBe('冷备')
    expect(cold.recoverable).toBe(false)
    expect(points.value[2].restorable).toBe(false)
    expect(points.value[2].detail.reported).toBe(false)
  })

  it('validates a single 备份点 and keeps the last result on it', async () => {
    apiPost.mockResolvedValue({
      success: true,
      result: {
        ok: false,
        messages: ['归档校验和不一致，文件可能被改动'],
        id: 'export:broken.luyunbak',
        medium: 'export_backup',
        checked_at: '2026-09-01T10:06:00+08:00',
        recoverable: false,
      },
    })
    const showAlert = vi.fn()
    const { validatingId, validatePoint, validateResults, pointDisplay } = useBackupCenter({
      showAlert,
      clearAlert: vi.fn(),
    })

    const result = await validatePoint('export:broken.luyunbak')

    expect(apiPost).toHaveBeenCalledWith(
      '/api/backup/points/export%3Abroken.luyunbak/validate',
      {},
    )
    expect(validatingId.value).toBe('')
    expect(validateResults['export:broken.luyunbak'].ok).toBe(false)
    expect(pointDisplay({ id: 'export:broken.luyunbak', recoverable: true }).recoverable).toBe(false)
    expect(showAlert).toHaveBeenCalledWith('error', expect.stringContaining('校验未通过'))
  })

  it('exports with the two new photo switches and reloads points + health', async () => {
    downloadPost.mockResolvedValue('luyun_backup_x.luyunbak')
    apiGet.mockImplementation((path) => {
      if (path === '/api/backup/points') return Promise.resolve(pointsPayload())
      if (path === '/api/backup/health') return Promise.resolve(healthPayload())
      return Promise.resolve({})
    })
    const showAlert = vi.fn()
    const { exportForm, onExportBackup } = useBackupCenter({ showAlert, clearAlert: vi.fn() })

    expect(exportForm.include_standard_photos).toBe(true)
    expect(exportForm.include_other_photos).toBe(true)

    exportForm.passphrase = 'secret1'
    exportForm.passphrase2 = 'secret1'
    exportForm.include_runtime = true
    exportForm.include_standard_photos = false

    await onExportBackup()

    expect(downloadPost).toHaveBeenCalledWith(
      '/api/backup/export',
      {
        passphrase: 'secret1',
        include_runtime: true,
        include_app_db: true,
        include_recipes: true,
        include_standard_photos: false,
        include_other_photos: true,
      },
      'luyun_backup.luyunbak',
    )
    expect(apiGet).toHaveBeenCalledWith('/api/backup/points', null, null, 'no-store')
    expect(apiGet).toHaveBeenCalledWith('/api/backup/health', null, null, 'no-store')
    expect(exportForm.passphrase).toBe('')
  })

  it('PG 后端下业务数据被禁用：提交始终不带 include_app_db，刷新也不会重新打开', async () => {
    downloadPost.mockResolvedValue('luyun_backup_x.luyunbak')
    apiGet.mockImplementation((path) => {
      if (path === '/api/backup/points') {
        return Promise.resolve(pointsPayload({ backend: 'postgres', export_app_db_supported: false }))
      }
      if (path === '/api/backup/health') return Promise.resolve(healthPayload())
      return Promise.resolve({})
    })
    const {
      dbBackend, exportAppDbSupported, exportForm, exportHasLargePayload, loadPoints, onExportBackup,
    } = makeHarness()

    await loadPoints()
    expect(dbBackend.value).toBe('postgres')
    expect(exportAppDbSupported.value).toBe(false)
    expect(exportForm.include_app_db).toBe(false)
    // 业务数据不再算大负载，配方仍然算
    expect(exportHasLargePayload.value).toBe(true)
    exportForm.include_recipes = false
    expect(exportHasLargePayload.value).toBe(false)
    exportForm.include_recipes = true

    // 就算有人手动把表单项改回 true，提交时也必须是 false
    exportForm.include_app_db = true
    exportForm.passphrase = 'secret1'
    exportForm.passphrase2 = 'secret1'
    await onExportBackup()

    expect(downloadPost).toHaveBeenCalledTimes(1)
    expect(downloadPost.mock.calls[0][1].include_app_db).toBe(false)
    // 导出成功后的 loadPoints 刷新不会把 PG 下已禁用的项又打开
    expect(exportForm.include_app_db).toBe(false)
    expect(exportAppDbSupported.value).toBe(false)

    // 刷新失败同样不能把已禁用的项重新打开
    apiGet.mockRejectedValueOnce(new Error('网络错误'))
    await loadPoints()
    expect(exportAppDbSupported.value).toBe(false)
    expect(exportForm.include_app_db).toBe(false)
  })

  it('后端明确说支持（sqlite）时按用户勾选提交业务数据', async () => {
    downloadPost.mockResolvedValue('luyun_backup_x.luyunbak')
    apiGet.mockImplementation((path) => {
      if (path === '/api/backup/points') {
        return Promise.resolve(pointsPayload({ backend: 'sqlite', export_app_db_supported: true }))
      }
      if (path === '/api/backup/health') return Promise.resolve(healthPayload())
      return Promise.resolve({})
    })
    const { dbBackend, exportAppDbSupported, exportForm, loadPoints, onExportBackup } = makeHarness()

    await loadPoints()
    expect(dbBackend.value).toBe('sqlite')
    expect(exportAppDbSupported.value).toBe(true)
    expect(exportForm.include_app_db).toBe(true)

    exportForm.passphrase = 'secret1'
    exportForm.passphrase2 = 'secret1'
    await onExportBackup()
    expect(downloadPost.mock.calls[0][1].include_app_db).toBe(true)
    expect(exportForm.include_app_db).toBe(true)
  })

  it('能力字段缺失（老后端）时行为不变：业务数据默认勾选并照常提交', async () => {
    downloadPost.mockResolvedValue('luyun_backup_x.luyunbak')
    apiGet.mockImplementation((path) => {
      if (path === '/api/backup/points') return Promise.resolve(pointsPayload())
      if (path === '/api/backup/health') return Promise.resolve(healthPayload())
      return Promise.resolve({})
    })
    const { dbBackend, exportAppDbSupported, exportForm, loadPoints, onExportBackup } = makeHarness()

    expect(exportAppDbSupported.value).toBe(true)
    await loadPoints()
    expect(dbBackend.value).toBe('')
    expect(exportAppDbSupported.value).toBe(true)
    expect(exportForm.include_app_db).toBe(true)

    exportForm.passphrase = 'secret1'
    exportForm.passphrase2 = 'secret1'
    await onExportBackup()
    expect(downloadPost.mock.calls[0][1].include_app_db).toBe(true)
  })

  it('previews 恢复 and seeds apply switches from default_apply', async () => {
    apiUpload.mockResolvedValue(previewPayload())
    const { importState, onImportFileChange, onPreviewImport, importPhotos, importMissing, importValidation, restoreAllowed, requiresForce, canApplyImport, forceRequired, forceContinue } =
      makeHarness()

    onImportFileChange({ name: 'backup.luyunbak' })
    importState.passphrase = 'secret1'
    await onPreviewImport()

    expect(apiUpload).toHaveBeenCalledWith(
      '/api/backup/import/preview',
      expect.any(FormData),
      expect.any(Function),
    )
    // default_apply 已经把缺失标照片的类默认置为不勾选。
    expect(importState.apply_standard_photos).toBe(false)
    expect(importState.apply_other_photos).toBe(true)
    expect(importState.apply_credentials).toBe(true)
    expect(importState.apply_app_db).toBe(true)
    expect(importState.apply_recipes).toBe(true)
    expect(importState.apply_runtime).toBe(false)

    expect(importPhotos.value.other.count).toBe(3)
    expect(importMissing.value.map((m) => m.label)).toEqual(['运行配置'])
    expect(importValidation.value.cross_point.standard_missing).toBe(2)
    expect(restoreAllowed.value).toBe(true)
    expect(requiresForce.value).toBe(true)
    // 未勾回被影响的类时无需强制，可直接恢复。
    expect(forceRequired.value).toBe(false)
    expect(canApplyImport.value).toBe(true)

    // 勾回备份里缺失的标准图 → 必须显式勾选强制继续。
    importState.apply_standard_photos = true
    expect(forceRequired.value).toBe(true)
    expect(canApplyImport.value).toBe(false)
    forceContinue.value = true
    expect(canApplyImport.value).toBe(true)
  })

  it('blocks apply when the 备份 itself is corrupt (cannot be overridden)', async () => {
    apiUpload.mockResolvedValue(previewPayload({
      validation: {
        in_backup: { ok: false, errors: ['标准图数量与清单不符（清单 3，归档 1）'] },
        cross_point: { has_difference: false, missing: {}, standard_missing: 0, other_missing: 0 },
      },
      restore_allowed: false,
      requires_force: false,
      default_apply: {
        credentials: true, runtime: false, app_db: true, recipes_db: true,
        standard_photos: false, other_photos: true,
      },
    }))
    const showAlert = vi.fn()
    const { importState, onImportFileChange, onPreviewImport, importErrors, importHasErrors, restoreAllowed, canApplyImport, onApplyImport, confirmState } =
      useBackupCenter({ showAlert, clearAlert: vi.fn() })

    onImportFileChange({ name: 'corrupt.luyunbak' })
    importState.passphrase = 'secret1'
    await onPreviewImport()

    expect(importHasErrors.value).toBe(true)
    expect(importErrors.value).toEqual(['标准图数量与清单不符（清单 3，归档 1）'])
    expect(restoreAllowed.value).toBe(false)
    expect(canApplyImport.value).toBe(false)

    onApplyImport()
    expect(confirmState.open).toBe(false)
    expect(showAlert).toHaveBeenCalledWith('error', expect.stringContaining('阻止恢复'))
  })

  it('applies 恢复 through the two-step confirm with photo flags + force', async () => {
    apiUpload.mockResolvedValueOnce(previewPayload()).mockResolvedValueOnce({
      success: true,
      mode: 'merge',
      applied: { credentials: true, runtime: false, app_db: true, recipes_db: false, standard_photos: true, other_photos: false },
      applied_labels: ['凭据', '业务数据', '标准图'],
      photos_restored: { standard: 3, other: 0 },
      snapshot_ts: '20260901_120000',
      session_invalidated: true,
    })
    apiGet.mockImplementation((path) => {
      if (path === '/api/backup/points') return Promise.resolve(pointsPayload())
      if (path === '/api/backup/health') return Promise.resolve(healthPayload())
      return Promise.resolve({})
    })
    const { importState, onImportFileChange, onPreviewImport, onApplyImport, forceContinue, confirmState, confirmChecked, confirmReady, onConfirmClick, importSuccessModal } =
      makeHarness()

    onImportFileChange({ name: 'backup.luyunbak' })
    importState.passphrase = 'secret1'
    await onPreviewImport()

    importState.apply_standard_photos = true
    forceContinue.value = true
    onApplyImport()
    // 需要强制勾选时，两步确认里的必选项必须先勾上。
    expect(confirmReady.value).toBe(true)

    await onConfirmClick()
    expect(confirmState.open).toBe(false)

    const applyCall = apiUpload.mock.calls[1]
    expect(applyCall[0]).toBe('/api/backup/import/apply')
    const form = applyCall[1]
    expect(form.get('import_token')).toBe('tok-1')
    expect(form.get('mode')).toBe('merge')
    expect(form.get('apply_standard_photos')).toBe('true')
    expect(form.get('apply_other_photos')).toBe('true')
    expect(form.get('force')).toBe('true')

    expect(importSuccessModal.show).toBe(true)
    expect(importSuccessModal.message).toContain('凭据、业务数据、标准图')
    expect(importSuccessModal.message).toContain('标准图 3 张')
    expect(importSuccessModal.message).toContain('20260901_120000')
    expect(importSuccessModal.message).toContain('重新登录')
    expect(confirmChecked.overwrite).toBeUndefined()
  })

  it('只恢复照片这类不动业务库的内容时，不强制登出', async () => {
    apiUpload.mockResolvedValueOnce(previewPayload()).mockResolvedValueOnce({
      success: true,
      mode: 'merge',
      applied: { credentials: false, runtime: false, app_db: false, recipes_db: false, standard_photos: false, other_photos: true },
      applied_labels: ['其它照片'],
      photos_restored: { standard: 0, other: 3 },
      session_invalidated: false,
    })
    apiGet.mockResolvedValue({})
    const location = { href: '/setup?section=backup' }
    vi.stubGlobal('window', { location })
    try {
      const {
        importState,
        onImportFileChange,
        onPreviewImport,
        onApplyImport,
        onConfirmClick,
        importSuccessModal,
        confirmImportSuccessRedirect,
      } = makeHarness()

      onImportFileChange({ name: 'backup.luyunbak' })
      importState.passphrase = 'secret1'
      await onPreviewImport()
      // other_photos 在 default_apply 里默认勾选，且没有跨备份点差异 → 不需要强制继续
      expect(importState.apply_other_photos).toBe(true)
      onApplyImport()
      await onConfirmClick()

      expect(importSuccessModal.show).toBe(true)
      expect(importSuccessModal.sessionInvalidated).toBe(false)

      apiPost.mockClear()
      await confirmImportSuccessRedirect()
      expect(importSuccessModal.show).toBe(false)
      expect(apiPost).not.toHaveBeenCalled()
      expect(location.href).toBe('/setup?section=backup')
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('还原了业务库（会话已失效）时才登出并跳登录页', async () => {
    apiUpload.mockResolvedValueOnce(previewPayload()).mockResolvedValueOnce({
      success: true,
      mode: 'merge',
      applied: { credentials: false, runtime: false, app_db: true, recipes_db: false, standard_photos: false, other_photos: false },
      applied_labels: ['业务数据'],
      photos_restored: { standard: 0, other: 0 },
      session_invalidated: true,
    })
    apiGet.mockResolvedValue({})
    apiPost.mockResolvedValue({ success: true })
    const location = { href: '/setup?section=backup' }
    vi.stubGlobal('window', { location })
    try {
      const {
        importState,
        onImportFileChange,
        onPreviewImport,
        onApplyImport,
        onConfirmClick,
        importSuccessModal,
        confirmImportSuccessRedirect,
      } = makeHarness()

      onImportFileChange({ name: 'backup.luyunbak' })
      importState.passphrase = 'secret1'
      await onPreviewImport()
      onApplyImport()
      await onConfirmClick()

      expect(importSuccessModal.sessionInvalidated).toBe(true)
      await confirmImportSuccessRedirect()

      expect(apiPost).toHaveBeenCalledWith('/api/auth/logout')
      expect(location.href).toBe('/login')
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('合并导入有未写入的行时如实提示，不只说成功', async () => {
    apiUpload.mockResolvedValueOnce(previewPayload()).mockResolvedValueOnce({
      success: true,
      mode: 'merge',
      applied: { credentials: false, runtime: false, app_db: true, recipes_db: false, standard_photos: false, other_photos: false },
      applied_labels: ['业务数据'],
      photos_restored: { standard: 0, other: 0 },
      snapshot_ts: '20260901_120000',
      session_invalidated: false,
      merge_failed_rows: 3,
      merge_reports: {
        app_db: {
          total_imported: 10,
          total_failed: 3,
          results: [
            {
              table: 'orders',
              status: 'PARTIAL',
              imported: 10,
              failed: 3,
              errors: [
                { table: 'orders', key: 'mf-1', error: 'NOT NULL constraint failed' },
              ],
            },
          ],
        },
      },
    })
    apiGet.mockResolvedValue({})
    const {
      importState,
      onImportFileChange,
      onPreviewImport,
      onApplyImport,
      onConfirmClick,
      importSuccessModal,
    } = makeHarness()

    onImportFileChange({ name: 'backup.luyunbak' })
    importState.passphrase = 'secret1'
    await onPreviewImport()
    onApplyImport()
    await onConfirmClick()

    expect(importSuccessModal.show).toBe(true)
    expect(importSuccessModal.message).toContain('3 行没能写入')
    expect(importSuccessModal.message).toContain('orders（mf-1）：NOT NULL constraint failed')
  })

  it('warns when the restored library references photos missing on disk', async () => {
    apiUpload.mockResolvedValueOnce(previewPayload()).mockResolvedValueOnce({
      success: true,
      mode: 'merge',
      applied: { credentials: false, runtime: false, app_db: true, recipes_db: false, standard_photos: false, other_photos: false },
      applied_labels: ['业务数据'],
      photos_restored: { standard: 0, other: 0 },
      photo_consistency: {
        ok: false,
        missing: { standard: ['s1'], other: ['o1', 'o2'] },
        standard_missing: 1,
        other_missing: 2,
        checked_at: '2026-09-01T12:00:00+08:00',
      },
      snapshot_ts: '20260901_120000',
      session_invalidated: false,
    })
    apiGet.mockImplementation((path) => {
      if (path === '/api/backup/points') return Promise.resolve(pointsPayload())
      if (path === '/api/backup/health') return Promise.resolve(healthPayload())
      return Promise.resolve({})
    })
    const {
      importState,
      onImportFileChange,
      onPreviewImport,
      onApplyImport,
      forceContinue,
      onConfirmClick,
      importSuccessModal,
    } = makeHarness()

    onImportFileChange({ name: 'backup.luyunbak' })
    importState.passphrase = 'secret1'
    await onPreviewImport()
    importState.apply_standard_photos = true
    forceContinue.value = true
    onApplyImport()
    await onConfirmClick()

    expect(importSuccessModal.show).toBe(true)
    expect(importSuccessModal.message).toContain('恢复成功')
    expect(importSuccessModal.message).toContain('1 张标准图')
    expect(importSuccessModal.message).toContain('2 张其它照片')
    expect(importSuccessModal.message).toContain('冷备归档')
  })

  it('rolls back a 本机回滚快照 through the two-step confirm with photo query params', async () => {
    apiGet.mockResolvedValue(pointsPayload({ points: [exportPoint] }))
    apiPost.mockResolvedValue({
      success: true,
      ts: '20260901_100000',
      applied: { app_db: true },
      applied_labels: ['业务数据'],
      photos_restored: { standard: 0, other: 0 },
      snapshot_ts: '20260901_130000',
      session_invalidated: true,
    })
    const showAlert = vi.fn()
    const {
      loadSnapshots,
      snapshots,
      snapshotsLoading,
      onRollbackSnapshot,
      confirmState,
      confirmChecked,
      confirmReady,
      onConfirmClick,
      rollingBackTs,
    } = useBackupCenter({ showAlert, clearAlert: vi.fn() })

    await loadSnapshots()
    expect(snapshots.value[0].ts).toBe('20260901_100000')
    expect(snapshots.value[0].provenance_label).toBe('更新作业前')
    expect(snapshotsLoading.value).toBe(false)

    onRollbackSnapshot('20260901_100000')
    expect(confirmState.open).toBe(true)
    expect(confirmState.title).toContain('数据回滚')
    expect(confirmState.danger).toBe(true)
    expect(confirmReady.value).toBe(false)

    // 必选项未勾选时确认按钮不可用，也不会发起回滚。
    await onConfirmClick()
    expect(apiPost).not.toHaveBeenCalled()

    confirmChecked.rollback = true
    expect(confirmReady.value).toBe(true)
    await onConfirmClick()

    expect(apiPost).toHaveBeenCalledWith(
      '/api/backup/snapshots/20260901_100000/rollback',
      {},
      { apply_standard_photos: true, apply_other_photos: true },
    )
    expect(confirmState.open).toBe(false)
    expect(rollingBackTs.value).toBe('')
    expect(showAlert).toHaveBeenCalledWith('success', expect.stringContaining('数据回滚成功'))
  })

  it('PG 快照的回滚确认说明业务数据不在范围内', async () => {
    const pgPoint = {
      id: 'snapshot:20260919_051032',
      medium: 'local_snapshot',
      medium_label: '本机回滚快照',
      provenance_label: '更新作业前',
      size_bytes: 87 * 1024 * 1024,
      contents: ['app_pg', 'runtime', 'credentials', 'other_photos'],
      contents_labels: ['业务数据 (PostgreSQL)', '运行配置', '凭据', '其它照片'],
      detail: { ts: '20260919_051032' },
      created_at: '2026-09-19T05:10:34+08:00',
      recoverable: true,
    }
    apiGet.mockResolvedValue(pointsPayload({ points: [pgPoint] }))
    const { loadPoints, onRollbackSnapshot, confirmState, snapshots } = makeHarness()

    await loadPoints()
    onRollbackSnapshot('20260919_051032')

    expect(snapshots.value[0].contents).toContain('app_pg')
    // PG 是整库 pg_restore：标题、代价说明与勾选项都要说清会重建数据库对象
    expect(confirmState.title).toContain('恢复整库数据')
    expect(confirmState.message).toContain('pg_restore')
    const details = confirmState.details.join('；')
    expect(details).toContain('pg_restore --clean')
    expect(details).toContain('需要重新登录后台')
    expect(confirmState.checkboxes[0].label).toContain('重建数据库对象')
  })

  it('escalates a 回滚 whose library references photos missing on disk', async () => {
    apiGet.mockResolvedValue(pointsPayload({ points: [exportPoint] }))
    apiPost.mockResolvedValue({
      success: true,
      ts: '20260901_100000',
      applied: { app_db: true },
      applied_labels: ['业务数据'],
      photos_restored: { standard: 0, other: 0 },
      photo_consistency: {
        ok: false,
        missing: { standard: ['s1'], other: [] },
        standard_missing: 1,
        other_missing: 0,
      },
      snapshot_ts: '20260901_130000',
      session_invalidated: false,
    })
    const showAlert = vi.fn()
    const {
      loadSnapshots,
      onRollbackSnapshot,
      confirmChecked,
      onConfirmClick,
    } = useBackupCenter({ showAlert, clearAlert: vi.fn() })

    await loadSnapshots()
    onRollbackSnapshot('20260901_100000')
    confirmChecked.rollback = true
    await onConfirmClick()

    expect(showAlert).toHaveBeenCalledWith(
      'error',
      expect.stringContaining('1 张标准图'),
    )
  })

  it('saves 保留配置 only after showing the deletion preview', async () => {
    const preview = {
      config: { snapshot_keep: 2, export_keep: 5, cold_keep: 3 },
      snapshot: {
        keep: 2,
        protected: [{ id: 'snapshot:20260901_100000', ts: '20260901_100000', provenance: 'pre_update', provenance_label: '更新作业前', protected: true, reason: '更新前快照，永不自动清理', size_bytes: 10 }],
        delete: [{ id: 'snapshot:20260801_100000', ts: '20260801_100000', provenance: 'manual', provenance_label: '手动', protected: false, size_bytes: 20 }],
        kept: ['20260901_100000'],
      },
      cold: { keep: 3, protected: [], delete: [], kept: [] },
    }
    apiPost.mockResolvedValueOnce({ success: true, preview })
    const showAlert = vi.fn()
    const {
      retentionForm,
      loadRetention,
      retentionLimits,
      cleanupPreview,
      saveRetention,
      confirmState,
      confirmReady,
      confirmChecked,
      onConfirmClick,
      retentionPreviewSummary,
    } = useBackupCenter({ showAlert, clearAlert: vi.fn() })

    apiGet.mockResolvedValue({
      success: true,
      config: { snapshot_keep: 5, export_keep: 5, cold_keep: 14 },
      limits: { snapshot_keep_default: 5, snapshot_keep_min: 1, snapshot_keep_max: 20, export_keep_default: 5, export_keep_min: 1, export_keep_max: 20, cold_keep_default: 14, cold_keep_min: 1, cold_keep_max: 90 },
      preview,
    })
    await loadRetention()
    expect(retentionForm.snapshot_keep).toBe(5)
    expect(retentionForm.cold_keep).toBe(14)
    expect(retentionLimits.value.snapshot_keep_max).toBe(20)
    expect(cleanupPreview.value.snapshot.delete).toHaveLength(1)

    apiPut.mockResolvedValue({
      success: true,
      config: { snapshot_keep: 2, export_keep: 5, cold_keep: 3 },
      limits: { snapshot_keep_max: 20 },
      cleanup: { preview, deleted: [{ id: 'snapshot:20260801_100000', size_bytes: 20 }], freed_bytes: 20 },
      preview,
    })
    apiGet.mockImplementation((path) => {
      if (path === '/api/backup/points') return Promise.resolve(pointsPayload())
      if (path === '/api/backup/health') return Promise.resolve(healthPayload())
      return Promise.resolve({})
    })

    retentionForm.snapshot_keep = 2
    retentionForm.cold_keep = 3
    await saveRetention()

    expect(apiPost).toHaveBeenLastCalledWith('/api/backup/cleanup/preview', {
      snapshot_keep: 2,
      export_keep: 5,
      cold_keep: 3,
    })
    expect(retentionPreviewSummary.value.snapshotDelete).toBe(1)
    expect(apiPut).not.toHaveBeenCalled()
    expect(confirmState.open).toBe(true)
    expect(confirmState.details.join(' ')).toContain('本机回滚快照 1 份')

    expect(confirmReady.value).toBe(false)
    confirmChecked.cleanup = true
    expect(confirmReady.value).toBe(true)
    await onConfirmClick()

    expect(apiPut).toHaveBeenCalledWith('/api/backup/retention', {
      snapshot_keep: 2,
      export_keep: 5,
      cold_keep: 3,
    })
    expect(retentionForm.snapshot_keep).toBe(2)
    expect(showAlert).toHaveBeenCalledWith('success', expect.stringContaining('保留配置已保存'))
  })

  it('runs 清理 through the two-step confirm and surfaces protected reasons', async () => {
    const preview = {
      config: { snapshot_keep: 5, cold_keep: 14 },
      snapshot: {
        keep: 5,
        protected: [{ id: 'snapshot:20260901_100000', ts: '20260901_100000', protected: true, reason: '更新前快照，永不自动清理', provenance_label: '更新作业前' }],
        delete: [],
        kept: [],
      },
      cold: {
        keep: 14,
        protected: [{ id: 'cold:20260901_080000', ts: '20260901_080000', protected: true, reason: '最近一份备份点，永不自动清理' }],
        delete: [{ id: 'cold:20260701_080000', ts: '20260701_080000', protected: false, size_bytes: 64 }],
        kept: [],
      },
    }
    apiGet.mockResolvedValue({ success: true, config: { snapshot_keep: 5, cold_keep: 14 }, limits: {}, preview })
    apiPost.mockResolvedValueOnce({ success: true, preview, deleted: [{ id: 'cold:20260701_080000', size_bytes: 64 }], freed_bytes: 64 })
    const showAlert = vi.fn()
    const { loadRetention, runCleanup, confirmState, confirmChecked, confirmReady, onConfirmClick, cleaningUp } =
      useBackupCenter({ showAlert, clearAlert: vi.fn() })

    await loadRetention()
    runCleanup()

    expect(confirmState.open).toBe(true)
    expect(confirmState.danger).toBe(true)
    expect(confirmState.details.join(' ')).toContain('冷备：删除 1 份')
    expect(confirmReady.value).toBe(false)
    confirmChecked.cleanup = true
    await onConfirmClick()

    expect(apiPost).toHaveBeenLastCalledWith('/api/backup/cleanup', {})
    expect(cleaningUp.value).toBe(false)
    expect(showAlert).toHaveBeenCalledWith('success', expect.stringContaining('已清理 1 个备份点'))
  })

  it('uses the two-step confirm instead of window.confirm', async () => {
    const fs = await import('node:fs')
    const path = await import('node:path')
    const source = fs.readFileSync(
      path.resolve(process.cwd(), 'src/composables/useBackupCenter.js'),
      'utf8',
    )
    expect(source).not.toMatch(/window\.confirm/)
    expect(source).toMatch(/function requestConfirm/)
  })
})
