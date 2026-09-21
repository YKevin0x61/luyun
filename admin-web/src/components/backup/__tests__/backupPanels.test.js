import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

// 备份面板是纯展示（分组与行模型在 utils/backupPoints.js 里已单测），这里按仓库
// 既有做法做「源码契约」检查：结论必须有文字、冷备不重复出现、窄屏不横向滚动。
const here = dirname(fileURLToPath(import.meta.url))
const BACKUP_DIR = join(here, '..')
const VIEWS_DIR = join(here, '../../../views')

const overview = readFileSync(join(BACKUP_DIR, 'BackupOverview.vue'), 'utf8')
const list = readFileSync(join(BACKUP_DIR, 'BackupPointList.vue'), 'utf8')
const setupView = readFileSync(join(VIEWS_DIR, 'SetupView.vue'), 'utf8')

function compact(source) {
  return source.replace(/\s+/g, '')
}

describe('BackupOverview 契约', () => {
  it('结论以文字给出，颜色只是辅助', () => {
    const src = compact(overview)
    expect(src).toContain('health?health.summary:')
    expect(src).toContain('backup-overview__next')
    expect(src).toContain('{{health.next_step}}')
    // 状态胶囊用共享组件，而不是各写一套 pill 样式
    expect(src).toContain("importStatusPillfrom'../ui/StatusPill.vue'")
    expect(src).not.toContain('.status-pill')
  })

  it('四个关键事实与「可恢复备份点」分子分母都渲染', () => {
    const src = compact(overview)
    for (const label of ['最近一次可用备份', '介质', '可恢复备份点', '总体积', '覆盖内容']) {
      expect(src).toContain(`<dt>${label}</dt>`)
    }
    expect(src).toContain('counts.usable')
    expect(src).toContain('counts.total')
  })

  it('重跑按钮是原生 button，带忙碌态与焦点描边', () => {
    const src = compact(overview)
    expect(src).toContain(':disabled="loading"')
    expect(src).toContain('@click="$emit(\'refresh\')"')
    expect(src).toContain('.backup-overview__refresh:focus-visible')
    expect(src).toContain('min-height:40px')
  })
})

describe('BackupPointList 契约', () => {
  it('三种介质各渲染一次，冷备不再重复出现在第二个列表里', () => {
    const src = compact(list)
    expect(src).toContain('groupBackupPoints(props.points)')
    // 分组标题来自单一 GROUP_META，模板里只有一处冷备列表
    expect(src).toContain("title:'本机回滚快照'")
    expect(src).toContain("title:'导出备份'")
    expect(src).toContain("title:'冷备'")
    expect(src.match(/v-for="\{point,row\}ing\.items"/g)).toHaveLength(1)
  })

  it('每行同时给出符号/文字结论与回滚入口', () => {
    const src = compact(list)
    expect(src).toContain(':label="row.checkLabel"')
    expect(src).toContain(':tone="row.checkTone||\'neutral\'"')
    expect(src).toContain("point.medium==='local_snapshot'&&row.recoverable")
    expect(src).toContain('@click="$emit(\'rollback\',point)"')
    expect(src).toContain('@click="$emit(\'validate\',point.id)"')
    // PG 快照搬不动业务数据，按钮文案要说准
    expect(src).toContain('row.rollbackLabel')
  })

  it('源磁盘缺失只作警告展示，并标出照片未分类', () => {
    const src = compact(list)
    expect(src).toContain('v-for="(warning,wi)inrow.checkWarnings"')
    expect(src).toContain('row.photosUnclassified')
    expect(src).toContain('未能按库引用分类')
  })

  it('冷备只读：不给校验/回滚按钮，并显示只读胶囊', () => {
    const src = compact(list)
    expect(src).toContain('v-if="!g.readonly"')
    expect(src).toContain('tone="neutral"label="只读"')
  })

  it('只有冷备时明确说明页面不能直接恢复', () => {
    const src = compact(list)
    expect(src).toContain('backupPointsEmptyHint(props.points)')
    expect(src).toContain('recoverableCount===0&&emptyHint')
  })

  it('不备份清单折叠在 details 里，窄屏按钮整行且触控目标够大', () => {
    const src = compact(list)
    expect(src).toContain('<detailsv-if="notBackedUp.length"')
    expect(src).toContain('@media(max-width:560px)')
    expect(src).toContain('min-height:36px')
  })
})

describe('SetupView 备份中心接线契约', () => {
  it('备份面板改用两个组件，且不再自己算行展示模型', () => {
    const src = compact(setupView)
    expect(src).toContain('<BackupOverview')
    expect(src).toContain(':health="healthView"')
    expect(src).toContain('<BackupPointList')
    expect(src).toContain(':rolling-back-ts="rollingBackTs"')
    expect(src).toContain(':not-backed-up="notBackedUp"')
    // 旧的 8 列表格写法（pointDisplay + 冷备二次渲染）已全部移除
    expect(src).not.toContain('pointDisplay')
    expect(src).not.toContain('pointsEmptyHint')
  })

  it('恢复面板不再重复列表里的快照表', () => {
    const src = compact(setupView)
    expect(src).toContain('本机回滚快照请在页面顶部')
    // 旧快照表用的列头已从这里消失，回滚入口只剩备份点列表一处
    expect(src).not.toContain('<th>时间戳</th>')
  })

  it('PG 门店导出面板说明业务数据走整库快照，并指路冷备命令', () => {
    const src = compact(setupView)
    // 业务数据两种后端都能勾（能力位保留给未来限制），形态差异靠 appDbExportFormat 表达
    expect(src).toContain(':disabled="!exportAppDbSupported"')
    expect(src).toContain("appDbExportFormat==='pgdump'")
    expect(src).toContain('PostgreSQL门店的业务数据以整库快照')
    expect(src).toContain('整库覆盖')
    expect(src).toContain('pg_dump')
    expect(src).toContain('deploy/README.md')
    expect(src).toContain('第10.4节')
    // 面板顶部说明按形态切换：PG 下要写明业务数据是整库快照
    expect(src).toContain('业务数据与两类卫生照片')
    expect(src).toContain('业务数据（PostgreSQL整库快照）')
  })

  it('PG 备份的恢复模式只给覆盖，并说明不能合并', () => {
    const src = compact(setupView)
    expect(src).toContain(':options="importModeOptions"')
    expect(src).toContain('这份备份的业务数据是PostgreSQL整库快照')
    expect(src).toContain('不能与现有数据合并')
    // 导入面板的业务数据勾选对两种成员都放行（app_db / app_pg）
    expect(src).toContain('!importIncludes.app_db&&!importIncludes.app_pg')
  })
})
