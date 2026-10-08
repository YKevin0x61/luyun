// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import BackupPointList from '../BackupPointList.vue'

// 备份面板是纯展示（分组与行模型在 utils/backupPoints.js 里已单测），这里按仓库
// 既有做法做「源码契约」检查：结论必须有文字、冷备不重复出现、窄屏不横向滚动。
// 校验说明的**分组级去重**（F-01）是算出来的，源码断言答不上来，所以那几条用真挂载。
const here = dirname(fileURLToPath(import.meta.url))
const BACKUP_DIR = join(here, '..')
const VIEWS_DIR = join(here, '../../../views')

const overview = readFileSync(join(BACKUP_DIR, 'BackupOverview.vue'), 'utf8')
const list = readFileSync(join(BACKUP_DIR, 'BackupPointList.vue'), 'utf8')
// 7 节并成 5 节之后（ADR 0099），备份中心的正文在「数据」节组件里；
// 壳（SetupView.vue）只留导航 / 页头 / 切换与四个弹窗的框。
const dataSection = readFileSync(join(VIEWS_DIR, 'settings/DataSection.vue'), 'utf8')

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

describe('数据节（DataSection）备份中心接线契约', () => {
  it('备份面板改用两个组件，且不再自己算行展示模型', () => {
    const src = compact(dataSection)
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
    const src = compact(dataSection)
    expect(src).toContain('本机回滚快照请在页面顶部')
    // 旧快照表用的列头已从这里消失，回滚入口只剩备份点列表一处
    expect(src).not.toContain('<th>时间戳</th>')
  })

  it('PG 门店导出面板说明业务数据走整库快照，并指路冷备命令', () => {
    const src = compact(dataSection)
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

  it('导出面板显示服务端打包进度（阶段文案 + 进度条）', () => {
    const src = compact(dataSection)
    expect(src).toContain('exportStageText')
    expect(src).toContain('exportPercent')
    expect(src).toContain('exportIndeterminate')
    expect(src).toContain('upload-progress')
  })

  it('PG 备份的恢复模式只给覆盖，并说明不能合并', () => {
    const src = compact(dataSection)
    expect(src).toContain(':options="importModeOptions"')
    expect(src).toContain('这份备份的业务数据是PostgreSQL整库快照')
    expect(src).toContain('不能与现有数据合并')
    // 导入面板的业务数据勾选对两种成员都放行（app_db / app_pg）
    expect(src).toContain('!importIncludes.app_db&&!importIncludes.app_pg')
  })
})

// ============================================================================
// 校验说明的分组级去重（t8 验收 F-01）
//
// 后端对每个备份点各返回一份基础校验说明，其中"每份备份都成立"的机制解释会在每个点上
// 各来一遍（真机实测：4 个本机回滚快照各一份 48 字说明 = 同一段话说 4 遍）。去重是算
// 出来的，源码断言答不上来，所以这一组用真挂载。
// ============================================================================

const MEDIUM_LABELS = { local_snapshot: '本机回滚快照', export_backup: '导出备份', cold_backup: '冷备' }

/** 后端逐点重复的那段机制解释（原文取自真机 /api/backup/points）。 */
const PG_SHARED = '业务数据是 PostgreSQL 整库备份（app.pgdump）：页面内「恢复整库数据」用 pg_restore --clean 重建数据库对象，恢复期间采集会中断一轮，完成后需要重新登录后台'

/** 一个备份点的最小形状（行模型只读这些字段）。 */
function backupPoint(overrides = {}) {
  return {
    id: 'snapshot:20260901_100000',
    medium: 'local_snapshot',
    created_at: '2026-09-01T10:00:00+08:00',
    provenance_label: '更新作业前',
    size_bytes: 1024,
    contents: ['app_pg'],
    contents_labels: ['业务数据'],
    recoverable: true,
    detail: { ts: '20260901_100000', files: [] },
    basic_check: { ok: true, messages: [] },
    ...overrides,
  }
}

function snapshot(ts, basicCheck = {}, overrides = {}) {
  return backupPoint({
    id: `snapshot:${ts}`,
    detail: { ts, files: [] },
    basic_check: { ok: true, messages: [], ...basicCheck },
    ...overrides,
  })
}

function mountList(props = {}) {
  return mount(BackupPointList, { props: { mediumLabels: MEDIUM_LABELS, ...props } })
}

function groupEl(wrapper, title) {
  const group = wrapper.findAll('.points__group')
    .find((g) => g.find('.points__group-title').text() === title)
  expect(group, `没有找到分组「${title}」`).toBeTruthy()
  return group
}

function rowsOf(wrapper, title) {
  return groupEl(wrapper, title).findAll('.point')
}

function rowByTs(wrapper, title, ts) {
  const row = rowsOf(wrapper, title).find((r) => r.find('.point__ts').text() === ts)
  expect(row, `分组「${title}」里没有 ${ts} 这一行`).toBeTruthy()
  return row
}

function occurrences(text, needle) {
  return text.split(needle).length - 1
}

describe('校验说明去重（F-01）：本组共有的只在分组级说一次', () => {
  it('四个点带同一段说明 → 整个列表里只出现一次，收在该组默认收起的折叠里', () => {
    const wrapper = mountList({
      points: [
        snapshot('20260928_105924', { messages: [PG_SHARED] }),
        snapshot('20260923_120630', { messages: [PG_SHARED] }),
        snapshot('20260922_100311', { messages: [PG_SHARED] }),
        snapshot('20260922_091756', { messages: [PG_SHARED] }),
      ],
    })

    const group = groupEl(wrapper, '本机回滚快照')
    const shared = group.find('.points__shared')
    expect(shared.exists()).toBe(true)
    expect(shared.find('summary').text()).toBe('共同的校验说明（1 条）')
    expect(shared.element.open, '分组级说明应当默认收起').toBe(false)
    expect(shared.text()).toContain(PG_SHARED)

    // 同一段话全组件只出现一次（真机改版前是 4 次）
    expect(occurrences(wrapper.text(), PG_SHARED)).toBe(1)
    // 逐点那一行不再复述
    for (const row of rowsOf(wrapper, '本机回滚快照')) {
      expect(row.text()).not.toContain(PG_SHARED)
    }
  })

  it('单点独有的消息仍显示在它自己那一行（不丢信息）', () => {
    const UNIQUE = '备份点内没有任何可恢复内容'
    const wrapper = mountList({
      points: [
        snapshot('20260928_105924', { messages: [PG_SHARED] }),
        snapshot('20260923_120630', { messages: [PG_SHARED] }),
        snapshot('20260918_164127', { ok: false, messages: [UNIQUE] }),
      ],
    })

    const row = rowByTs(wrapper, '本机回滚快照', '20260918_164127')
    expect(row.text()).toContain(UNIQUE)
    expect(row.text()).not.toContain(PG_SHARED)
    // 共有那段仍只在分组级；独有那条没有被吸进折叠
    expect(occurrences(wrapper.text(), PG_SHARED)).toBe(1)
    expect(groupEl(wrapper, '本机回滚快照').find('.points__shared').text()).not.toContain(UNIQUE)
  })

  it('只有空白差异的消息算同一条（按空白规范化后比较）', () => {
    const A = '业务数据是 PostgreSQL 整库备份：恢复期间采集会中断一轮'
    const B = '业务数据是   PostgreSQL 整库备份：恢复期间采集会中断一轮\n'
    const wrapper = mountList({
      points: [
        snapshot('20260928_105924', { messages: [A] }),
        snapshot('20260923_120630', { messages: [B] }),
      ],
    })

    const shared = groupEl(wrapper, '本机回滚快照').find('.points__shared')
    expect(shared.exists()).toBe(true)
    expect(shared.find('summary').text()).toBe('共同的校验说明（1 条）')
    expect(occurrences(wrapper.text(), '恢复期间采集会中断一轮')).toBe(1)
  })

  it('组里只有一个点时不合并：消息留在它自己那一行', () => {
    const wrapper = mountList({
      points: [snapshot('20260928_105924', { messages: [PG_SHARED] })],
    })

    expect(groupEl(wrapper, '本机回滚快照').find('.points__shared').exists()).toBe(false)
    expect(occurrences(wrapper.text(), PG_SHARED)).toBe(1)
    expect(rowByTs(wrapper, '本机回滚快照', '20260928_105924').text()).toContain(PG_SHARED)
  })

  it('手动校验返回的同一段说明同样去重，校验时间与状态胶囊不受影响', () => {
    const wrapper = mountList({
      points: [
        snapshot('20260928_105924', { messages: [PG_SHARED] }),
        snapshot('20260923_120630', { messages: [PG_SHARED] }),
      ],
      // 手动校验结果：消息与基础校验里那段相同，另有自己的校验时间
      validateResults: {
        'snapshot:20260923_120630': {
          ok: true, recoverable: true, messages: [PG_SHARED], checked_at: '2026-09-23T12:06:30+08:00',
        },
      },
    })

    expect(occurrences(wrapper.text(), PG_SHARED)).toBe(1)
    const row = rowByTs(wrapper, '本机回滚快照', '20260923_120630')
    expect(row.text()).toContain('可恢复')      // 状态胶囊文字
    expect(row.text()).toContain('校验于')       // 校验时间
    expect(row.text()).toContain('业务数据')     // 内容行
    expect(row.text()).not.toContain(PG_SHARED)
  })

  it('warning 行、其它字段与两枚操作按钮都不受去重影响', () => {
    const wrapper = mountList({
      points: [
        snapshot('20260928_105924', {
          ok: true,
          messages: [PG_SHARED],
          warnings: ['其它照片有 6 个文件在源磁盘上已缺失（备份里没有，恢复后仍缺）'],
        }),
        snapshot('20260923_120630', { messages: [PG_SHARED] }),
        backupPoint({
          id: 'cold:20260901_080000',
          medium: 'cold_backup',
          created_at: null,
          detail: { ts: '20260901_080000', files: [], read_only: true, reported: false },
          contents: ['app_db'],
          contents_labels: ['业务数据'],
          recoverable: false,
          size_bytes: 8192,
        }),
      ],
    })

    const row = rowByTs(wrapper, '本机回滚快照', '20260928_105924')
    expect(row.text()).toContain('提示')                                     // warning 行标签
    expect(row.text()).toContain('其它照片有 6 个文件在源磁盘上已缺失')          // warning 原文
    expect(row.findAll('button').map((b) => b.text())).toEqual(['校验', '恢复整库数据'])

    // 冷备那组只读：没有校验 / 回滚按钮，状态走「未报告」措辞
    const coldRow = rowByTs(wrapper, '冷备', '20260901_080000')
    expect(coldRow.text()).toContain('未报告')
    expect(coldRow.text()).toContain('（时间未知）')
    expect(coldRow.findAll('button')).toHaveLength(0)
    expect(groupEl(wrapper, '冷备').text()).toContain('只读')
  })
})
