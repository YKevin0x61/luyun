import { computed, reactive, ref } from 'vue'
import { api } from '../api/client'
import {
  createProgressController,
  createProgressState,
  formatBytes,
  formatTs,
  progressLabel,
} from '../utils/backupProgress'
import {
  CONTENT_APP_PG,
  CONTENT_LABELS,
  CONTENT_OTHER_PHOTOS,
  CONTENT_STANDARD_PHOTOS,
  PHOTO_CONTENTS,
  backupPointRow,
  backupPointsEmptyHint,
  cleanupDeleteSummary,
} from '../utils/backupPoints'

const BACKUP_PASSPHRASE_MIN_LENGTH = 6

/**
 * Setup page — 备份中心：备份健康 / 备份点列表 / 导出备份 / 恢复 / 保留与清理。
 *
 * 恢复动作一律走本 composable 内的两步确认（不使用浏览器原生 confirm）：
 * 先 requestConfirm 展示将要发生什么与风险勾选项，再由页面上的确认按钮调用
 * onConfirmClick；覆盖导入、数据回滚、清理与保存保留配置都复用这一条通道。
 *
 * @param {{ showAlert: Function, clearAlert: Function, onAfterRollback?: () => Promise<void> }} opts
 */
export function useBackupCenter({ showAlert, clearAlert, onAfterRollback }) {
  // ==================== 导出备份 ====================
  /** 后端数据库形态（"sqlite" | "postgres"）与「业务数据能否打进导出包」的能力位。 */
  const dbBackend = ref('')
  /**
   * 初始与未知（老后端没有该字段、请求失败）时都按 true 处理：只有后端明确说
   * 不支持，才关掉业务数据，避免误伤 SQLite 门店的既有行为。
   */
  const exportAppDbSupported = ref(true)
  /**
   * 业务数据的打包形态：'sqlite'（app.db 文件，可合并导入）或 'pgdump'
   * （PostgreSQL 整库快照，只能整库覆盖）。未知时按 sqlite 处理。
   */
  const appDbExportFormat = ref('sqlite')
  const exportForm = reactive({
    passphrase: '',
    passphrase2: '',
    include_runtime: false,
    include_app_db: true,
    include_recipes: true,
    include_standard_photos: true,
    include_other_photos: true,
  })
  const exporting = ref(false)
  const exportBtnLabel = computed(() => (exporting.value ? '导出中…' : '生成并下载备份'))
  /** 业务数据不可导出时不再算大负载，配方仍然是。 */
  const exportHasLargePayload = computed(
    () => (exportAppDbSupported.value && exportForm.include_app_db) || exportForm.include_recipes,
  )

  /** PG 门店不允许把业务数据打进导出包：每次拿到能力位后都强制关掉该项。 */
  function applyExportAppDbCapability() {
    if (!exportAppDbSupported.value) exportForm.include_app_db = false
  }

  async function onExportBackup() {
    clearAlert()
    if (exportForm.passphrase.length < BACKUP_PASSPHRASE_MIN_LENGTH) {
      showAlert('error', `导出口令至少 ${BACKUP_PASSPHRASE_MIN_LENGTH} 位`)
      return
    }
    if (exportForm.passphrase !== exportForm.passphrase2) {
      showAlert('error', '两次输入的口令不一致')
      return
    }
    exporting.value = true
    try {
      // 兜底：表单被改回 true 也不允许在 PG 下带 include_app_db: true。
      const includeAppDb = exportAppDbSupported.value && exportForm.include_app_db
      if (!exportAppDbSupported.value) exportForm.include_app_db = false
      await api.downloadPost(
        '/api/backup/export',
        {
          passphrase: exportForm.passphrase,
          include_runtime: exportForm.include_runtime,
          include_app_db: includeAppDb,
          include_recipes: exportForm.include_recipes,
          include_standard_photos: exportForm.include_standard_photos,
          include_other_photos: exportForm.include_other_photos,
        },
        'luyun_backup.luyunbak',
      )
      showAlert('success', '已生成加密导出备份，请务必牢记口令（遗失将无法解密恢复）')
      exportForm.passphrase = ''
      exportForm.passphrase2 = ''
      // 导出同时在本机登记一条导出备份点，列表与健康结论都需要刷新。
      await Promise.all([loadPoints(), loadHealth()])
    } catch (err) {
      showAlert('error', '导出失败：' + err.message)
    } finally {
      exporting.value = false
    }
  }

  // ==================== 两步确认 ====================
  const confirmState = reactive({
    open: false,
    title: '',
    message: '',
    details: [],
    danger: false,
    confirmLabel: '确认',
    checkboxes: [],
    onConfirm: null,
  })
  const confirmChecked = reactive({})

  function requestConfirm(opts = {}, onConfirm = null) {
    confirmState.open = true
    confirmState.title = opts.title || '请确认'
    confirmState.message = opts.message || ''
    confirmState.details = Array.isArray(opts.details) ? opts.details : []
    confirmState.danger = !!opts.danger
    confirmState.confirmLabel = opts.confirmLabel || '确认'
    confirmState.checkboxes = (opts.checkboxes || []).map((box) => ({
      key: String(box.key),
      label: box.label || '',
      required: box.required !== false,
    }))
    confirmState.onConfirm = typeof onConfirm === 'function' ? onConfirm : null
    for (const key of Object.keys(confirmChecked)) delete confirmChecked[key]
    for (const box of confirmState.checkboxes) confirmChecked[box.key] = false
  }

  function closeConfirm() {
    confirmState.open = false
    confirmState.onConfirm = null
    confirmState.checkboxes = []
    confirmState.details = []
  }

  const confirmReady = computed(() =>
    confirmState.checkboxes.every((box) => !box.required || !!confirmChecked[box.key]),
  )

  async function onConfirmClick() {
    if (!confirmReady.value) return
    const action = confirmState.onConfirm
    closeConfirm()
    if (action) await action()
  }

  // ==================== 备份健康 ====================
  const health = ref(null)
  const healthLoading = ref(false)
  const healthError = ref('')

  function applyHealth(next) {
    if (next) health.value = next
  }

  async function loadHealth() {
    healthLoading.value = true
    healthError.value = ''
    try {
      const data = await api.get('/api/backup/health', null, null, 'no-store')
      applyHealth(data?.health)
    } catch (err) {
      healthError.value = err.message || '加载失败'
      health.value = null
    } finally {
      healthLoading.value = false
    }
  }

  async function refreshHealth() {
    healthLoading.value = true
    healthError.value = ''
    try {
      const data = await api.post('/api/backup/health/refresh', {})
      applyHealth(data?.health)
    } catch (err) {
      healthError.value = err.message || '重跑备份健康失败'
      showAlert('error', '重跑备份健康失败：' + (err.message || '未知错误'))
    } finally {
      healthLoading.value = false
    }
  }

  const healthView = computed(() => {
    const h = health.value
    if (!h) return null
    return {
      status: h.status || '',
      summary: h.summary || '',
      next_step: h.next_step || '',
      last_success_at: h.last_success_at || null,
      last_success_medium_label: h.last_success_medium_label || '',
      checks: Array.isArray(h.checks) ? h.checks : [],
      counts: h.counts || { total: 0, usable: 0, unusable: 0, by_medium: {} },
      coverage: Array.isArray(h.coverage) ? h.coverage : [],
      coverage_labels: Array.isArray(h.coverage_labels) ? h.coverage_labels : [],
      total_bytes: h.total_bytes || 0,
      computed_at: h.computed_at || null,
    }
  })

  // ==================== 备份点列表 ====================
  const points = ref([])
  const pointsLoading = ref(false)
  const pointsError = ref('')
  const validatingId = ref('')
  const validateResults = reactive({})
  const notBackedUp = ref([])
  const mediumLabels = ref({})
  const mediumPurposes = ref({})

  const snapshotPoints = computed(() =>
    points.value.filter((p) => p.medium === 'local_snapshot'))
  const exportPoints = computed(() =>
    points.value.filter((p) => p.medium === 'export_backup'))
  const coldPoints = computed(() =>
    points.value.filter((p) => p.medium === 'cold_backup'))

  /** 空列表时给一句可操作的话，而不是一张空表。 */
  const pointsEmptyHint = computed(() => backupPointsEmptyHint(points.value))

  async function loadPoints() {
    pointsLoading.value = true
    pointsError.value = ''
    try {
      const data = await api.get('/api/backup/points', null, null, 'no-store')
      points.value = Array.isArray(data?.points) ? data.points : []
      notBackedUp.value = Array.isArray(data?.not_backed_up) ? data.not_backed_up : []
      mediumLabels.value = data?.medium_labels || {}
      mediumPurposes.value = data?.medium_purposes || {}
      dbBackend.value = typeof data?.backend === 'string' ? data.backend : ''
      exportAppDbSupported.value = data?.export_app_db_supported !== false
      appDbExportFormat.value = data?.app_db_export_format === 'pgdump' ? 'pgdump' : 'sqlite'
      // 刷新后必须继续保持 PG 下的关闭状态，不能把已禁用的项又打开。
      applyExportAppDbCapability()
      applyHealth(data?.health)
    } catch (err) {
      pointsError.value = err.message || '加载失败'
      points.value = []
      notBackedUp.value = []
    } finally {
      pointsLoading.value = false
    }
  }

  async function validatePoint(id) {
    if (!id) return null
    validatingId.value = id
    clearAlert()
    try {
      const data = await api.post(
        `/api/backup/points/${encodeURIComponent(id)}/validate`,
        {},
      )
      const result = data?.result || null
      if (result) validateResults[id] = result
      if (result?.ok) {
        showAlert('success', `备份点基础校验通过：${id}`)
      } else {
        showAlert(
          'error',
          `备份点基础校验未通过：${(result?.messages || []).join('；') || '未知原因'}`,
        )
      }
      return result
    } catch (err) {
      showAlert('error', '备份点校验失败：' + err.message)
      return null
    } finally {
      validatingId.value = ''
    }
  }

  function pointDisplay(point) {
    return backupPointRow(point, {
      mediumLabels: mediumLabels.value,
      mediumPurposes: mediumPurposes.value,
      validateResult: validateResults[point?.id] || null,
    })
  }

  // ==================== 导入 / 恢复预览 ====================
  const importState = reactive({
    file: null,
    fileName: '',
    passphrase: '',
    mode: 'merge',
    apply_credentials: true,
    apply_runtime: false,
    apply_app_db: false,
    apply_recipes: false,
    apply_standard_photos: false,
    apply_other_photos: false,
  })
  const importPreview = ref(null)
  const importToken = ref('')
  const previewing = ref(false)
  const importing = ref(false)
  const previewBtnLabel = computed(() => (previewing.value ? '预览中…' : '预览'))
  const importApplyLabel = computed(() => (importing.value ? '导入中…' : '确认恢复'))
  /** 强制继续（跨备份点差异）必须由操作者显式勾选，默认关闭。 */
  const forceContinue = ref(false)

  const previewProgress = reactive(createProgressState())
  const importProgress = reactive(createProgressState())
  const { startProgress, makeProgressHandler, finishProgress } = createProgressController()

  const importPreviewMeta = computed(() => importPreview.value?.meta || null)
  const importPreviewCredentials = computed(() => importPreview.value?.credentials_preview || null)
  const importIncludes = computed(() => importPreviewMeta.value?.includes || {})

  const importPhotos = computed(() => importPreview.value?.photos || null)
  const importMissing = computed(() =>
    Array.isArray(importPreview.value?.missing) ? importPreview.value.missing : [])
  const importValidation = computed(() => importPreview.value?.validation || null)
  const restoreAllowed = computed(() => {
    if (importPreview.value?.restore_allowed !== undefined) {
      return !!importPreview.value.restore_allowed
    }
    return importValidation.value?.in_backup?.ok !== false
  })
  const requiresForce = computed(() => {
    if (importPreview.value?.requires_force !== undefined) {
      return !!importPreview.value.requires_force
    }
    return !!importValidation.value?.cross_point?.has_difference
  })
  const importErrors = computed(() =>
    importValidation.value?.in_backup?.errors || [])
  const importHasErrors = computed(() =>
    importValidation.value?.in_backup?.ok === false)
  /** 这份包的「业务数据」是 PG 整库 dump：只能整库覆盖，没有合并语义。 */
  const importHasPgAppDb = computed(() => importPreview.value?.has_app_pg === true)
  /** 恢复模式选项：PG 整库快照下直接不给「合并去重追加」，避免选了必然 400。 */
  const importModeOptions = computed(() =>
    importHasPgAppDb.value
      ? [{ value: 'overwrite', label: '覆盖恢复 · 整库替换' }]
      : [
          { value: 'merge', label: '合并去重追加' },
          { value: 'overwrite', label: '覆盖恢复 · 整库替换' },
        ],
  )

  const importCrossPoint = computed(() => importValidation.value?.cross_point || null)
  const crossPointStandardMissing = computed(() =>
    importCrossPoint.value?.standard_missing
    ?? (importCrossPoint.value?.missing?.standard || []).length)
  const crossPointOtherMissing = computed(() =>
    importCrossPoint.value?.other_missing
    ?? (importCrossPoint.value?.missing?.other || []).length)
  const crossPointMissingLabels = computed(() => {
    const labels = []
    if (crossPointStandardMissing.value) labels.push(CONTENT_LABELS.standard_photos)
    if (crossPointOtherMissing.value) labels.push(CONTENT_LABELS.other_photos)
    return labels
  })
  const importPhotoItems = computed(() => {
    const photos = importPhotos.value
    if (!photos) return []
    return PHOTO_CONTENTS
      .map((kind) => {
        const entry = photos[kind]
        if (!entry) return null
        const label = entry.label || CONTENT_LABELS[kind]
        if (!entry.included) return `${label}：未包含`
        const missingRefs = Number(entry.missing_references) || 0
        return `${label}：${entry.count ?? 0} 张（清单 ${entry.declared_count ?? 0}${
          missingRefs ? `，缺失引用 ${missingRefs}` : ''
        }）`
      })
      .filter(Boolean)
  })

  const importPreviewItems = computed(() => {
    const meta = importPreviewMeta.value
    const cred = importPreviewCredentials.value
    if (!meta) return []
    return [
      ['账号', cred?.phone_masked || '—'],
      ['shop_id', cred?.shop_id || '—'],
      ['company_id', cred?.company_id || '—'],
      ['门店名', cred?.shop_name || '—'],
      ['含运行配置', meta.includes?.runtime ? '是' : '否'],
      ['含业务数据', meta.includes?.app_db ? '是' : '否'],
      ['含配方', meta.includes?.recipes_db ? '是' : '否'],
      ['含标准图', meta.includes?.standard_photos ? '是' : '否'],
      ['含其它照片', meta.includes?.other_photos ? '是' : '否'],
      ['导出时间', meta.exported_at ? formatTs(meta.exported_at) : '—'],
      ['应用版本', meta.app_version || '—'],
    ]
  })

  /**
   * 应用项的默认勾选：以服务端 default_apply 为准（跨备份点差异会让受影响
   * 的照片类默认不勾选）。旧版预览没有 default_apply 时退化为按 includes 推导。
   */
  function syncImportApplyOptions() {
    const defaults = importPreview.value?.default_apply
    if (defaults) {
      importState.apply_credentials = !!defaults.credentials
      importState.apply_runtime = !!defaults.runtime
      // 两种后端的业务数据共用这一个勾选（成员分别是 app.db / app.pgdump）
      importState.apply_app_db = !!(defaults.app_db || defaults.app_pg)
      importState.apply_recipes = !!defaults.recipes_db
      importState.apply_standard_photos = !!defaults.standard_photos
      importState.apply_other_photos = !!defaults.other_photos
      return
    }
    const includes = importIncludes.value
    importState.apply_credentials = true
    importState.apply_runtime = !!includes.runtime
    importState.apply_app_db = !!(includes.app_db || includes.app_pg)
    importState.apply_recipes = !!includes.recipes_db
    importState.apply_standard_photos = !!includes.standard_photos
    importState.apply_other_photos = !!includes.other_photos
  }

  /**
   * 被跨备份点差异影响、且操作者又勾回来的照片类。只有这种情况下才需要
   * 「我已知晓并强制继续」，避免影响无关的合并导入。
   */
  const importRecheckedMissing = computed(() => {
    const rechecked = []
    if (importState.apply_standard_photos && crossPointStandardMissing.value) {
      rechecked.push(CONTENT_LABELS.standard_photos)
    }
    if (importState.apply_other_photos && crossPointOtherMissing.value) {
      rechecked.push(CONTENT_LABELS.other_photos)
    }
    return rechecked
  })

  const forceRequired = computed(
    () => requiresForce.value && importRecheckedMissing.value.length > 0,
  )
  const forceSatisfied = computed(() => !forceRequired.value || forceContinue.value)

  /** 恢复按钮是否可用：备份自身损坏时不可覆盖；照片差异需要显式强制勾选。 */
  const canApplyImport = computed(
    () => !!importPreview.value && !!importToken.value && !importing.value
      && restoreAllowed.value && forceSatisfied.value,
  )

  const importInvalidReason = computed(() => {
    if (!importPreview.value) return ''
    if (!restoreAllowed.value) return '这份备份自身不一致，已阻止恢复'
    if (!forceSatisfied.value) {
      return `已勾选备份中缺失的${importRecheckedMissing.value.join('、')}，需先勾选强制继续`
    }
    return ''
  })

  function onImportFileChange(file) {
    importPreview.value = null
    importToken.value = ''
    forceContinue.value = false
    if (!file) {
      importState.file = null
      importState.fileName = ''
      return
    }
    importState.file = file
    importState.fileName = file.name
  }

  async function onPreviewImport() {
    clearAlert()
    if (!importState.file) {
      showAlert('error', '请先选择备份文件')
      return
    }
    if (!importState.passphrase) {
      showAlert('error', '请输入解密口令')
      return
    }
    previewing.value = true
    startProgress(previewProgress, 'preview')
    try {
      const formData = new FormData()
      formData.append('file', importState.file)
      formData.append('passphrase', importState.passphrase)
      const data = await api.upload(
        '/api/backup/import/preview',
        formData,
        makeProgressHandler(previewProgress),
      )
      importPreview.value = data
      importToken.value = data.import_token || ''
      forceContinue.value = false
      // PG 整库快照只有覆盖一种恢复方式：预览一出来就把模式摆正
      if (data?.has_app_pg) importState.mode = 'overwrite'
      syncImportApplyOptions()
      finishProgress(previewProgress, 'preview', true)
      if (data?.validation?.in_backup?.ok === false) {
        showAlert('error', '这份备份自身不一致，已阻止恢复；请改用其它备份点')
      } else if (data?.validation?.cross_point?.has_difference) {
        showAlert('info', '解密成功；这份备份缺少当前数据引用的部分照片，请核对后确认恢复')
      } else {
        showAlert('info', '解密成功，请核对下方内容后确认恢复')
      }
    } catch (err) {
      importPreview.value = null
      importToken.value = ''
      forceContinue.value = false
      finishProgress(previewProgress, 'preview', false)
      showAlert('error', '预览失败：' + err.message)
    } finally {
      previewing.value = false
    }
  }

  const importSuccessModal = reactive({ show: false, message: '', sessionInvalidated: false })

  function importSuccessMessage(data, verb) {
    const parts = []
    if (data?.applied_labels?.length) parts.push(`已恢复：${data.applied_labels.join('、')}`)
    else parts.push('没有任何内容被恢复')
    const photos = data?.photos_restored || {}
    if (photos.standard || photos.other) {
      parts.push(`照片：标准图 ${photos.standard || 0} 张、其它照片 ${photos.other || 0} 张`)
    }
    if (data?.snapshot_ts) parts.push(`已生成新的本机回滚快照 ${data.snapshot_ts}`)
    let msg = `${verb}成功。${parts.join('；')}。`
    const consistency = data?.photo_consistency
    if (consistency && consistency.ok === false) {
      msg += `⚠️ 但库里引用的照片有 ${consistency.standard_missing || 0} 张标准图、`
        + `${consistency.other_missing || 0} 张其它照片在磁盘上找不到，标准图清单会缺这些项；`
        + '请重新上传这些标准图，或用含照片的冷备归档补齐。'
    }
    if (data?.session_invalidated) msg += '当前登录会话已失效，请点击确认后重新登录。'
    // 合并模式可能有个别行写不进去（约束冲突 / 类型不匹配 / 磁盘错误）：
    // 后端会带回逐表报告，这里如实说明，不能只说「恢复成功」。
    const failedRows = Number(data?.merge_failed_rows) || 0
    if (failedRows) {
      const samples = []
      for (const report of Object.values(data?.merge_reports || {})) {
        for (const table of report?.results || []) {
          for (const err of table?.errors || []) {
            samples.push(`${table.table}${err.key ? `（${err.key}）` : ''}：${err.error}`)
          }
        }
      }
      msg += `⚠️ 有 ${failedRows} 行没能写入，这些行没有恢复成功`
        + (samples.length ? `。例如 ${samples.slice(0, 3).join('；')}` : '')
        + '。'
    }
    return msg
  }

  async function applyImportNow() {
    importing.value = true
    startProgress(importProgress, 'import')
    try {
      const formData = new FormData()
      formData.append('import_token', importToken.value)
      formData.append('mode', importHasPgAppDb.value ? 'overwrite' : importState.mode)
      formData.append('apply_credentials', importState.apply_credentials ? 'true' : 'false')
      formData.append('apply_runtime', importState.apply_runtime ? 'true' : 'false')
      formData.append('apply_app_db', importState.apply_app_db ? 'true' : 'false')
      formData.append('apply_recipes', importState.apply_recipes ? 'true' : 'false')
      formData.append('apply_standard_photos', importState.apply_standard_photos ? 'true' : 'false')
      formData.append('apply_other_photos', importState.apply_other_photos ? 'true' : 'false')
      formData.append('force', forceRequired.value && forceContinue.value ? 'true' : 'false')
      const data = await api.upload(
        '/api/backup/import/apply',
        formData,
        makeProgressHandler(importProgress),
      )
      finishProgress(importProgress, 'import', true)
      importPreview.value = null
      importToken.value = ''
      importState.passphrase = ''
      forceContinue.value = false
      importSuccessModal.message = importSuccessMessage(data, '恢复')
      importSuccessModal.sessionInvalidated = !!data?.session_invalidated
      importSuccessModal.show = true
      await Promise.all([loadPoints(), loadHealth()])
    } catch (err) {
      finishProgress(importProgress, 'import', false)
      const detail = err.detail
      if (err.status === 409 && detail && detail.reason === 'backup_corrupt') {
        showAlert('error', detail.message || '这份备份自身不一致，已阻止恢复')
        return
      }
      if (err.status === 409 && detail && detail.reason === 'photo_mismatch') {
        showAlert('error', detail.message || '备份缺少当前数据引用的照片，请确认后强制继续')
        return
      }
      showAlert('error', '恢复失败：' + err.message)
    } finally {
      importing.value = false
    }
  }

  const appliedSelectionLabels = computed(() => {
    const labels = []
    if (importState.apply_credentials) labels.push('凭据')
    if (importState.apply_runtime) labels.push('运行配置')
    if (importState.apply_app_db) labels.push('业务数据')
    if (importState.apply_recipes) labels.push('配方数据')
    if (importState.apply_standard_photos) labels.push('标准图')
    if (importState.apply_other_photos) labels.push('其它照片')
    return labels
  })

  /** 覆盖导入会替换整库，必须先过两步确认；其它模式只做一次风险确认。 */
  function onApplyImport() {
    if (!canApplyImport.value) {
      if (importInvalidReason.value) showAlert('error', importInvalidReason.value)
      else showAlert('error', '请先预览确认备份内容')
      return
    }
    const overwrite = importState.mode === 'overwrite'
    const details = [
      `恢复模式：${overwrite ? '覆盖恢复 · 整库替换' : '合并去重追加'}`,
      `应用项：${appliedSelectionLabels.value.join('、') || '（无）'}`,
    ]
    if (forceRequired.value) {
      details.push(`强制继续：备份中缺失${importRecheckedMissing.value.join('、')}`)
    }
    requestConfirm(
      {
        title: overwrite ? '确认覆盖恢复' : '确认恢复备份',
        message: overwrite
          ? '将替换整库并覆盖当前数据，系统会先自动生成一份本机回滚快照。'
          : '将把所选内容合并进当前数据，系统不会删除现有记录。',
        details,
        danger: overwrite,
        confirmLabel: overwrite ? '确认覆盖恢复' : '确认恢复',
        checkboxes: overwrite
          ? [{
            key: 'overwrite',
            label: '我确认覆盖当前数据（会先自动生成本机回滚快照）',
            required: true,
          }]
          : [],
      },
      applyImportNow,
    )
  }

  /**
   * 只恢复了照片 / 配方这类不碰 app.db 的内容时，会话并没有失效：这时关掉弹窗
   * 留在本页即可。只有后端明确说会话已失效（还原了 app.db）才登出并跳登录页。
   */
  async function confirmImportSuccessRedirect() {
    importSuccessModal.show = false
    if (!importSuccessModal.sessionInvalidated) return
    try {
      await api.post('/api/auth/logout')
    } catch (_) {
      // session may already be invalid after app_db import
    }
    window.location.href = '/login'
  }

  // ==================== 本机回滚快照 ====================
  const rollingBackTs = ref('')
  const snapshotListLoading = ref(false)
  const snapshotListError = ref('')
  const snapshots = computed(() =>
    points.value
      .filter((p) => p.medium === 'local_snapshot')
      .map((p) => ({
        ts: p.detail?.ts || String(p.id || '').replace(/^snapshot:/, ''),
        created_at: p.created_at,
        size_bytes: p.size_bytes,
        files: p.detail?.files || [],
        provenance_label: p.provenance_label,
        contents: p.contents || [],
        contents_labels: p.contents_labels || [],
        recoverable: p.recoverable !== false,
        basic_check: p.basic_check || null,
      })))
  const snapshotsLoading = computed(() => pointsLoading.value || snapshotListLoading.value)
  const snapshotsError = computed(() => pointsError.value || snapshotListError.value)

  /** 兼容旧调用名：本机回滚快照现在来自统一备份点清单。 */
  async function loadSnapshots() {
    snapshotListLoading.value = true
    snapshotListError.value = ''
    try {
      await loadPoints()
    } catch (err) {
      snapshotListError.value = err.message || '加载失败'
    } finally {
      snapshotListLoading.value = false
    }
  }

  async function rollbackSnapshotNow(ts, photoFlags) {
    rollingBackTs.value = ts
    clearAlert()
    try {
      const data = await api.post(
        `/api/backup/snapshots/${encodeURIComponent(ts)}/rollback`,
        {},
        photoFlags,
      )
      showAlert(
        data?.photo_consistency && data.photo_consistency.ok === false ? 'error' : 'success',
        importSuccessMessage(data, '数据回滚'),
      )
      if (typeof onAfterRollback === 'function') await onAfterRollback()
      await Promise.all([loadPoints(), loadHealth()])
    } catch (err) {
      const detail = err.detail
      showAlert('error', '数据回滚失败：'
        + (detail?.message || err.message || '未知错误'))
    } finally {
      rollingBackTs.value = ''
    }
  }

  /**
   * 数据回滚走两步确认。文案与照片选项随这份快照实际包含的内容变化：
   * 没有照片成员的旧快照不会声称能恢复照片。
   */
  function onRollbackSnapshot(tsOrPoint) {
    const point = typeof tsOrPoint === 'string'
      ? snapshots.value.find((s) => s.ts === tsOrPoint) || { ts: tsOrPoint }
      : (tsOrPoint || {})
    const ts = point.ts
    const contents = Array.isArray(point.contents_labels) ? point.contents_labels : []
    const contentCodes = Array.isArray(point.contents) ? point.contents : []
    const hasStandard = contentCodes.includes(CONTENT_STANDARD_PHOTOS)
    const hasOther = contentCodes.includes(CONTENT_OTHER_PHOTOS)
    const photoFlags = {
      apply_standard_photos: hasStandard,
      apply_other_photos: hasOther,
    }
    const details = [
      `目标本机回滚快照：${ts}`,
      `恢复内容：${contents.length ? contents.join('、') : '（该快照没有覆盖内容清单）'}`,
      '会先自动生成一份新的本机回滚快照',
    ]
    // PG 快照是整库 pg_restore：会 drop 并重建对象，恢复期间采集会中断一轮，
    // 后台会话表也被一起替换，所以要把代价说清楚，而不是只说「数据回滚」。
    const hasPgDump = contentCodes.includes(CONTENT_APP_PG)
    if (hasPgDump) {
      details.push('恢复方式：pg_restore --clean，会先删除并重建数据库对象')
      details.push('恢复期间采集会中断，恢复完成后需要重新登录后台')
    }
    if (!hasStandard && !hasOther) {
      details.push('该快照不含卫生照片，恢复后标准图与其它照片保持现状')
    }
    requestConfirm(
      {
        title: hasPgDump ? '确认恢复整库数据' : '确认数据回滚',
        message: hasPgDump
          ? `将用 pg_restore 把 PostgreSQL 整库替换为本机回滚快照 ${ts} 的内容，并先为当前状态新建一份本机回滚快照。恢复期间服务会短暂无法写库。`
          : `将把当前业务数据替换为本机回滚快照 ${ts} 的内容，并先为当前状态新建一份本机回滚快照。`,
        details,
        danger: true,
        confirmLabel: hasPgDump ? '确认恢复整库数据' : '确认数据回滚',
        checkboxes: [{
          key: 'rollback',
          label: hasPgDump
            ? '我确认用这份快照替换 PostgreSQL 整库（会重建数据库对象）'
            : '我确认用这份本机回滚快照替换当前数据',
          required: true,
        }],
      },
      () => rollbackSnapshotNow(ts, photoFlags),
    )
  }

  // ==================== 保留与清理 ====================
  const retention = ref(null)
  const retentionLimits = ref(null)
  const retentionLoading = ref(false)
  const retentionSaving = ref(false)
  const retentionForm = reactive({ snapshot_keep: 5, export_keep: 5, cold_keep: 14 })
  const cleanupPreview = ref(null)
  const cleanupPreviewLoading = ref(false)
  const cleaningUp = ref(false)

  function applyRetention(data) {
    if (data?.config) {
      retention.value = data.config
      retentionForm.snapshot_keep = data.config.snapshot_keep
      retentionForm.export_keep = data.config.export_keep
      retentionForm.cold_keep = data.config.cold_keep
    }
    if (data?.limits) retentionLimits.value = data.limits
    if (data?.preview) cleanupPreview.value = data.preview
  }

  async function loadRetention() {
    retentionLoading.value = true
    try {
      const data = await api.get('/api/backup/retention', null, null, 'no-store')
      applyRetention(data)
    } catch (err) {
      showAlert('error', '加载保留配置失败：' + err.message)
    } finally {
      retentionLoading.value = false
    }
  }

  async function previewCleanup(config) {
    cleanupPreviewLoading.value = true
    try {
      const data = await api.post('/api/backup/cleanup/preview', {
        snapshot_keep: Number(config?.snapshot_keep ?? retentionForm.snapshot_keep),
        export_keep: Number(config?.export_keep ?? retentionForm.export_keep),
        cold_keep: Number(config?.cold_keep ?? retentionForm.cold_keep),
      })
      if (data?.preview) cleanupPreview.value = data.preview
      return data?.preview || null
    } catch (err) {
      showAlert('error', '预览清理失败：' + err.message)
      return null
    } finally {
      cleanupPreviewLoading.value = false
    }
  }

  const retentionPreviewSummary = computed(() =>
    cleanupDeleteSummary(cleanupPreview.value))

  /** 保存保留配置前必须先展示「将删除哪些备份点」，再写入。 */
  async function saveRetention() {
    if (retentionSaving.value) return
    clearAlert()
    const snapshotKeep = Number(retentionForm.snapshot_keep)
    const exportKeep = Number(retentionForm.export_keep)
    const coldKeep = Number(retentionForm.cold_keep)
    if (![snapshotKeep, exportKeep, coldKeep].every(Number.isFinite)) {
      showAlert('error', '请填写有效的保留份数')
      return
    }
    const preview = await previewCleanup({
      snapshot_keep: snapshotKeep,
      export_keep: exportKeep,
      cold_keep: coldKeep,
    })
    if (!preview) return
    const summary = cleanupDeleteSummary(preview)
    const totalDelete = summary.snapshotDelete + summary.exportDelete + summary.coldDelete
    requestConfirm(
      {
        title: '确认保存保留配置',
        message:
          `保存后将按新配置立即清理：本机回滚快照保留 ${snapshotKeep} 份、`
          + `导出备份保留 ${exportKeep} 份、冷备保留 ${coldKeep} 份。`,
        details: [
          `将删除本机回滚快照 ${summary.snapshotDelete} 份`,
          `将删除导出备份 ${summary.exportDelete} 份`,
          `将删除冷备 ${summary.coldDelete} 份`,
          `受保护、不会自动清理的条目：${summary.protected} 份`,
        ],
        danger: totalDelete > 0,
        confirmLabel: '确认保存并清理',
        checkboxes: totalDelete > 0
          ? [{ key: 'cleanup', label: '我已知晓将删除上述备份点', required: true }]
          : [],
      },
      saveRetentionNow,
    )
  }

  async function saveRetentionNow() {
    retentionSaving.value = true
    try {
      const data = await api.put('/api/backup/retention', {
        snapshot_keep: Number(retentionForm.snapshot_keep),
        export_keep: Number(retentionForm.export_keep),
        cold_keep: Number(retentionForm.cold_keep),
      })
      applyRetention(data)
      const freed = formatBytes(data?.cleanup?.freed_bytes)
      const deleted = (data?.cleanup?.deleted || []).length
      showAlert('success', `保留配置已保存，已清理 ${deleted} 个备份点（释放 ${freed}）`)
      await Promise.all([loadPoints(), loadHealth()])
    } catch (err) {
      showAlert('error', '保存保留配置失败：' + err.message)
    } finally {
      retentionSaving.value = false
    }
  }

  async function runCleanupNow() {
    cleaningUp.value = true
    clearAlert()
    try {
      const data = await api.post('/api/backup/cleanup', {})
      if (data?.preview) cleanupPreview.value = data.preview
      const freed = formatBytes(data?.freed_bytes)
      const deleted = (data?.deleted || []).length
      showAlert('success', `已清理 ${deleted} 个备份点（释放 ${freed}）`)
      await Promise.all([loadPoints(), loadHealth()])
    } catch (err) {
      showAlert('error', '清理失败：' + err.message)
    } finally {
      cleaningUp.value = false
    }
  }

  /** 清理是不可逆动作，走两步确认并展示预览里的删除集合。 */
  function runCleanup() {
    if (cleaningUp.value) return
    const summary = cleanupDeleteSummary(cleanupPreview.value)
    const totalDelete = summary.snapshotDelete + summary.exportDelete + summary.coldDelete
    const details = [
      `本机回滚快照：删除 ${summary.snapshotDelete} 份`,
      `导出备份：删除 ${summary.exportDelete} 份`,
      `冷备：删除 ${summary.coldDelete} 份`,
      `受保护的条目不会删除：${summary.protected} 份`,
    ]
    if (totalDelete === 0) {
      details.push('按当前保留配置没有可删除的备份点')
    }
    requestConfirm(
      {
        title: '确认清理备份点',
        message: '将按当前保留配置删除超出份数的备份点，此操作不可撤销。',
        details,
        danger: totalDelete > 0,
        confirmLabel: '确认清理',
        checkboxes: totalDelete > 0
          ? [{ key: 'cleanup', label: '我已知晓清理不可撤销', required: true }]
          : [],
      },
      runCleanupNow,
    )
  }

  return {
    // 导出备份
    dbBackend,
    exportAppDbSupported,
    appDbExportFormat,
    exportForm,
    exporting,
    exportBtnLabel,
    exportHasLargePayload,
    onExportBackup,
    // 备份健康
    health,
    healthLoading,
    healthError,
    healthView,
    loadHealth,
    refreshHealth,
    // 备份点列表
    points,
    pointsLoading,
    pointsError,
    loadPoints,
    notBackedUp,
    mediumLabels,
    mediumPurposes,
    snapshotPoints,
    exportPoints,
    coldPoints,
    pointsEmptyHint,
    validatingId,
    validateResults,
    validatePoint,
    pointDisplay,
    // 两步确认
    confirmState,
    confirmChecked,
    confirmReady,
    requestConfirm,
    closeConfirm,
    onConfirmClick,
    // 导入 / 恢复
    importState,
    importPreview,
    importToken,
    previewing,
    importing,
    previewBtnLabel,
    importApplyLabel,
    previewProgress,
    importProgress,
    progressLabel,
    importPreviewMeta,
    importPreviewCredentials,
    importIncludes,
    importHasPgAppDb,
    importModeOptions,
    importPreviewItems,
    importPhotos,
    importPhotoItems,
    importMissing,
    importValidation,
    importErrors,
    importHasErrors,
    restoreAllowed,
    requiresForce,
    forceRequired,
    forceContinue,
    importRecheckedMissing,
    importInvalidReason,
    canApplyImport,
    onImportFileChange,
    onPreviewImport,
    onApplyImport,
    importSuccessModal,
    confirmImportSuccessRedirect,
    // 本机回滚快照
    snapshots,
    snapshotsLoading,
    snapshotsError,
    rollingBackTs,
    loadSnapshots,
    onRollbackSnapshot,
    // 保留与清理
    retention,
    retentionLimits,
    retentionForm,
    retentionLoading,
    retentionSaving,
    cleanupPreview,
    cleanupPreviewLoading,
    cleaningUp,
    retentionPreviewSummary,
    loadRetention,
    previewCleanup,
    saveRetention,
    runCleanup,
    // 格式化
    formatBytes,
    formatTs,
  }
}
