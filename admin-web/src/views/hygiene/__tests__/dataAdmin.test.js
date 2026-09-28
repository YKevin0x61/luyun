import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))

function read(rel) {
  return readFileSync(join(here, rel), 'utf8')
}

const view = read('../HygieneDataView.vue')

describe('hygiene data & photo admin (ADR-0087)', () => {
  it('导航里有第 7 项，并且挂在自己的路由上', () => {
    const copy = read('../../../utils/hygieneCopy.js')
    expect(copy).toMatch(/path: '\/hygiene\/data'/)
    expect(copy).toMatch(/title: '数据与照片'/)
    const router = read('../../../router/index.js')
    expect(router).toMatch(
      /hygieneAdminPage\('\/hygiene\/data', 'hygiene-data', \(\) => import\('\.\.\/views\/hygiene\/HygieneDataView\.vue'\)\)/,
    )
  })

  it('列表按营业日区间、类型与责任区查询，并带分页', () => {
    expect(view).toMatch(/api\.get\('\/api\/hygiene\/admin\/data\/records'/)
    expect(view).toMatch(/date_from: dateFrom\.value \|\| undefined/)
    expect(view).toMatch(/date_to: dateTo\.value \|\| undefined/)
    expect(view).toMatch(/kinds: selectedKinds\.value\.join\(','\)/)
    expect(view).toMatch(/page_size: PAGE_SIZE/)
  })

  it('默认查最近一周，不让首屏扫全表', () => {
    expect(view).toMatch(/const PAGE_SIZE = 24/)
    expect(view).toMatch(/to\.getTime\(\) - 6 \* 86400000/)
  })

  it('取图走按 capture_id 的管理端接口，缩略图用 thumb 变体', () => {
    expect(view).toMatch(
      /\/api\/hygiene\/admin\/data\/photo\/\$\{encodeURIComponent\(captureId\)\}\?variant=\$\{variant\}/,
    )
    expect(view).toMatch(/photoUrl\(photo\.capture_id\)/)
    expect(view).toMatch(/HygieneImageLightbox/)
  })

  it('删单条记录走 DELETE，并且要二次确认', () => {
    expect(view).toMatch(
      /api\.delete\(\s*`\/api\/hygiene\/admin\/data\/records\/\$\{target\.kind\}\/\$\{target\.record_id\}`/,
    )
    expect(view).toMatch(/title="删除这条记录"/)
    expect(view).toMatch(/danger/)
  })

  it('按区间清理不含整改原图：整单删除仍是整改页的能力', () => {
    expect(view).toMatch(/KINDS\.filter\(\(kind\) => kind\.value !== 'fix'\)/)
    expect(view).toMatch(/api\.post\('\/api\/hygiene\/admin\/data\/purge'/)
    expect(view).toMatch(/kinds: purgeKinds\.value/)
    expect(view).toMatch(/title="按区间清理"/)
  })

  it('导出是异步任务：起任务 → 轮询进度 → 下载 zip', () => {
    expect(view).toMatch(/api\.post\(\s*`\/api\/hygiene\/admin\/data\/export\/jobs\?/)
    expect(view).toMatch(/api\.get\(`\/api\/hygiene\/admin\/data\/export\/jobs\/\$\{jobId\}`\)/)
    expect(view).toMatch(
      /api\.download\(\s*`\/api\/hygiene\/admin\/data\/export\/jobs\/\$\{jobId\}\/download`/,
    )
    expect(view).toMatch(/正在打包/)
    // 轮询必须在组件卸载时停掉，否则离开页面还在打接口。
    expect(view).toMatch(/onBeforeUnmount/)
    expect(view).toMatch(/clearTimeout\(pollTimer\)/)
  })

  it('存储概况显示各类型张数与占用', () => {
    expect(view).toMatch(/api\.get\('\/api\/hygiene\/admin\/data\/storage'\)/)
    expect(view).toMatch(/storage\.total\.count/)
    expect(view).toMatch(/formatBytes\(bucket\.bytes\)/)
  })

  it('删除与清理都会提示不可恢复', () => {
    expect(view).toMatch(/硬删除/)
    expect(view).toMatch(/红黑榜的历史计数不回滚/)
  })
})

describe('一键保存今天的照片（票 12 收尾）', () => {
  it('按钮在数据页上，起的是 scope=today 的任务', () => {
    expect(view).toContain('一键保存今天的照片')
    expect(view).toMatch(
      /api\.post\('\/api\/hygiene\/admin\/data\/export\/jobs\?scope=today'\)/,
    )
  })

  it('不自己算「今天」：营业日 06:00 切日，只有服务端那个口径跟照片上的日期一致', () => {
    const fn = view.slice(
      view.indexOf('async function saveTodayPhotos'),
      view.indexOf('function gotoPage'),
    )
    expect(fn).toMatch(/scope=today/)
    expect(fn).not.toMatch(/date_from/)
    expect(fn).not.toMatch(/toISOString\(\)/)
    expect(fn).not.toMatch(/chinaNow/)
  })

  it('类型里有仪容仪表 —— 一键不带 kinds，所以日常与仪容都在包里', () => {
    expect(view).toMatch(/\{ value: 'attire', label: '仪容仪表' \}/)
  })

  it('下载名听后端给的（今天的包不叫「卫生数据.zip」）', () => {
    expect(view).toMatch(/job\.filename \|\| filename/)
  })

  it('打包中不让重复点', () => {
    expect(view).toMatch(/exportToday \|\| \(exportJob && exportJob\.state === 'running'\)/)
  })
})
