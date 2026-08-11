import { computed, reactive, ref } from 'vue'
import { api } from '../api/client'
import {
  createProgressController,
  createProgressState,
  formatBytes,
  formatTs,
  progressLabel,
} from '../utils/backupProgress'

const BACKUP_PASSPHRASE_MIN_LENGTH = 6

/**
 * Setup page — backup export / import / snapshots.
 * @param {{ showAlert: Function, clearAlert: Function, onAfterRollback?: () => Promise<void> }} opts
 */
export function useBackupCenter({ showAlert, clearAlert, onAfterRollback }) {
  const exportForm = reactive({
    passphrase: '',
    passphrase2: '',
    include_runtime: false,
    include_app_db: true,
    include_recipes: true,
  })
  const exporting = ref(false)
  const exportBtnLabel = computed(() => (exporting.value ? '导出中…' : '生成并下载备份'))
  const exportHasLargePayload = computed(() => exportForm.include_app_db || exportForm.include_recipes)

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
      await api.downloadPost(
        '/api/backup/export',
        {
          passphrase: exportForm.passphrase,
          include_runtime: exportForm.include_runtime,
          include_app_db: exportForm.include_app_db,
          include_recipes: exportForm.include_recipes,
        },
        'luyun_backup.luyunbak',
      )
      showAlert('success', '已生成加密备份文件，请务必牢记口令（遗失将无法解密恢复）')
      exportForm.passphrase = ''
      exportForm.passphrase2 = ''
    } catch (err) {
      showAlert('error', '导出失败：' + err.message)
    } finally {
      exporting.value = false
    }
  }

  const importState = reactive({
    file: null,
    fileName: '',
    passphrase: '',
    mode: 'merge',
    apply_credentials: true,
    apply_runtime: false,
    apply_app_db: false,
    apply_recipes: false,
  })
  const importPreview = ref(null)
  const importToken = ref('')
  const previewing = ref(false)
  const importing = ref(false)
  const previewBtnLabel = computed(() => (previewing.value ? '预览中…' : '预览'))
  const importApplyLabel = computed(() => (importing.value ? '导入中…' : '确认导入'))

  const previewProgress = reactive(createProgressState())
  const importProgress = reactive(createProgressState())
  const { startProgress, makeProgressHandler, finishProgress } = createProgressController()

  const importPreviewMeta = computed(() => importPreview.value?.meta || null)
  const importPreviewCredentials = computed(() => importPreview.value?.credentials_preview || null)
  const importIncludes = computed(() => importPreviewMeta.value?.includes || {})

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
      ['导出时间', meta.exported_at ? String(meta.exported_at).replace('T', ' ').slice(0, 19) : '—'],
      ['应用版本', meta.app_version || '—'],
    ]
  })

  function syncImportApplyOptions() {
    const includes = importIncludes.value
    importState.apply_credentials = true
    importState.apply_runtime = !!includes.runtime
    importState.apply_app_db = !!includes.app_db
    importState.apply_recipes = !!includes.recipes_db
  }

  function onImportFileChange(file) {
    importPreview.value = null
    importToken.value = ''
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
      syncImportApplyOptions()
      finishProgress(previewProgress, 'preview', true)
      showAlert('info', '解密成功，请核对下方内容后确认导入')
    } catch (err) {
      importPreview.value = null
      importToken.value = ''
      finishProgress(previewProgress, 'preview', false)
      showAlert('error', '预览失败：' + err.message)
    } finally {
      previewing.value = false
    }
  }

  const importSuccessModal = reactive({ show: false, message: '' })

  async function onApplyImport() {
    if (!importPreview.value || !importToken.value) {
      showAlert('error', '请先预览确认备份内容')
      return
    }
    if (importState.mode === 'overwrite') {
      if (!window.confirm('将替换整库并覆盖当前数据，系统会先自动生成回滚快照，是否继续？')) return
    }
    importing.value = true
    startProgress(importProgress, 'import')
    try {
      const formData = new FormData()
      formData.append('import_token', importToken.value)
      formData.append('mode', importState.mode)
      formData.append('apply_credentials', importState.apply_credentials ? 'true' : 'false')
      formData.append('apply_runtime', importState.apply_runtime ? 'true' : 'false')
      formData.append('apply_app_db', importState.apply_app_db ? 'true' : 'false')
      formData.append('apply_recipes', importState.apply_recipes ? 'true' : 'false')
      const data = await api.upload(
        '/api/backup/import/apply',
        formData,
        makeProgressHandler(importProgress),
      )
      finishProgress(importProgress, 'import', true)
      importPreview.value = null
      importToken.value = ''
      importState.passphrase = ''
      let msg = '导入成功'
      if (data.snapshot_ts) msg += `，已生成回滚点 ${data.snapshot_ts}`
      msg += '。当前登录会话已失效，请点击确认后重新登录。'
      importSuccessModal.message = msg
      importSuccessModal.show = true
    } catch (err) {
      finishProgress(importProgress, 'import', false)
      showAlert('error', '导入失败：' + err.message)
    } finally {
      importing.value = false
    }
  }

  async function confirmImportSuccessRedirect() {
    importSuccessModal.show = false
    try {
      await api.post('/api/auth/logout')
    } catch (_) {
      // session may already be invalid after app_db import
    }
    window.location.href = '/login'
  }

  const snapshots = ref([])
  const snapshotsLoading = ref(false)
  const snapshotsError = ref('')
  const rollingBackTs = ref('')

  async function loadSnapshots() {
    snapshotsLoading.value = true
    snapshotsError.value = ''
    try {
      const data = await api.get('/api/backup/snapshots', null, null, 'no-store')
      snapshots.value = data.snapshots || []
    } catch (err) {
      snapshotsError.value = err.message || '加载失败'
      snapshots.value = []
    } finally {
      snapshotsLoading.value = false
    }
  }

  async function onRollbackSnapshot(ts) {
    if (!window.confirm(`确认回滚到快照 ${ts}？当前数据将被替换。`)) return
    rollingBackTs.value = ts
    clearAlert()
    try {
      await api.post(`/api/backup/snapshots/${encodeURIComponent(ts)}/rollback`, {})
      showAlert('success', `已回滚到快照 ${ts}`)
      if (typeof onAfterRollback === 'function') await onAfterRollback()
      await loadSnapshots()
    } catch (err) {
      showAlert('error', '回滚失败：' + err.message)
    } finally {
      rollingBackTs.value = ''
    }
  }

  return {
    exportForm,
    exporting,
    exportBtnLabel,
    exportHasLargePayload,
    onExportBackup,
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
    importPreviewItems,
    onImportFileChange,
    onPreviewImport,
    onApplyImport,
    importSuccessModal,
    confirmImportSuccessRedirect,
    snapshots,
    snapshotsLoading,
    snapshotsError,
    rollingBackTs,
    formatBytes,
    formatTs,
    loadSnapshots,
    onRollbackSnapshot,
  }
}
