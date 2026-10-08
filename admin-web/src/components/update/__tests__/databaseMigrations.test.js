import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const panel = readFileSync(join(here, '../DatabaseMigrations.vue'), 'utf8')
const systemSection = readFileSync(join(here, '../../../views/settings/SystemSection.vue'), 'utf8')

describe('数据库迁移面板', () => {
  it('reads the status endpoint and posts to apply', () => {
    expect(panel).toMatch(/api\.get\('\/api\/db-migrations'\)/)
    expect(panel).toMatch(/api\.post\('\/api\/db-migrations\/apply'\)/)
  })

  it('tells a SQLite deployment it needs nothing', () => {
    expect(panel).toMatch(/v-if="!supported" class="hint section-lead">\{\{ note \}\}/)
  })

  it('lists pending migrations and gates the button behind a confirm', () => {
    expect(panel).toMatch(/v-if="pending\.length" class="migration-pending"/)
    expect(panel).toMatch(/@click="askApply"/)
    expect(panel).toMatch(/<ConfirmDialog[\s\S]*v-if="confirmOpen"/)
    expect(panel).toMatch(/@confirm="applyPending"/)
  })

  it('never offers to run the bootstrap script', () => {
    // 0001 是 DROP + CREATE：它只能出现在"不会自动执行"的折叠区里说明，
    // 绝不能进 pending 列表或 apply 请求。
    expect(panel).toMatch(/v-if="bootstrapOnly\.length" class="migration-bootstrap"/)
    expect(panel).toMatch(/含 DROP TABLE/)
    expect(panel).not.toMatch(/bootstrapOnly[\s\S]{0,120}applyPending/)
  })

  it('surfaces migrations whose file changed after being applied', () => {
    expect(panel).toMatch(/v-if="changed\.length" class="hint is-warn"/)
    expect(panel).toMatch(/不会自动重跑/)
  })

  it('distinguishes "no scripts in this bundle" from "already up to date"', () => {
    // 发行包按 git archive HEAD 打包：迁移脚本没提交就不在包里。那时面板必须说清
    // 「本包内没有增量脚本」，而不是把「什么都没检查到」说成「已是最新」。
    expect(panel).toMatch(/incremental_total/)
    expect(panel).toMatch(/data-role="no-migration-scripts"/)
    expect(panel).toMatch(/git archive HEAD/)
    expect(panel).toMatch(/v-else-if="upToDate"/)
  })

  it('keeps an apply failure on screen instead of wiping it on reload', () => {
    // applyPending 的 finally 会重新拉状态；load() 若无条件清 errorText，
    // 刚写进去的失败原因会在同一 tick 被清掉 → 运维看到的是"点了一下，没反应"。
    expect(panel).toMatch(/async function load\(\{ keepError = false \} = \{\}\)/)
    expect(panel).toMatch(/if \(!keepError\) errorText\.value = ''/)
    expect(panel).toMatch(/load\(\{ keepError: Boolean\(errorText\.value\) \}\)/)
  })

  it('keeps its own scoped styles for what the parent scoped block cannot reach', () => {
    // 所属分节（views/settings/SystemSection.vue）的 <style scoped> 只覆盖到子组件的**根元素**（那个 fieldset）：
    // legend / p / ul / details 一个都吃不到。样式写回父组件，面板就会退回浏览器默认排版
    // （16px 白字正文、原生三角、默认圆点列表），和同一页相邻分节明显不是一套。
    expect(panel).toMatch(/<style scoped>/)
    expect(panel).toMatch(/\.db-migrations legend\s*\{/)
    expect(panel).toMatch(/\.db-migrations \.hint\s*\{/)
    expect(panel).toMatch(/\.migration-applied > summary/)
  })

  it('lists each applied migration once instead of 0002 0002_xxx.sql', () => {
    // 文件名本身带版本前缀，再单独排一列版本号就是重复。
    expect(panel).toMatch(/\{\{ item\.filename \|\| item\.version \}\}/)
    expect(panel).not.toMatch(/<code>\{\{ item\.version \}\}<\/code>/)
  })

  it('is mounted in the update section of the setup page', () => {
    expect(systemSection).toMatch(/import DatabaseMigrations from '\.\.\/\.\.\/components\/update\/DatabaseMigrations\.vue'/)
    expect(systemSection).toMatch(/<DatabaseMigrations \/>/)
    expect(systemSection.indexOf('<DatabaseMigrations />')).toBeGreaterThan(-1)
    // 迁移面板排在发行版目录之前：先看"结构要不要动"，再看"能不能换版本"。
    expect(systemSection.indexOf('<DatabaseMigrations />')).toBeLessThan(
      systemSection.indexOf('正式发行版目录'),
    )
  })
})

describe('版本检测顺带提示待应用迁移', () => {
  const overview = readFileSync(join(here, '../UpdateOverview.vue'), 'utf8')

  it('reads the field the version-check endpoint adds', () => {
    expect(overview).toMatch(/props\.versionCheck\?\.pending_migrations/)
    expect(overview).toMatch(/pendingMigrations\.value\?\.count/)
  })

  it('shows the hint right in the version status card', () => {
    expect(overview).toMatch(/data-role="pending-migrations"/)
    expect(overview).toMatch(/条迁移待应用/)
    expect(overview).toMatch(/应用待执行迁移/)
  })

  it('turns the status pill yellow so it is not missed', () => {
    expect(overview).toMatch(/if \(pendingMigrationCount\.value\) return 'warn'/)
  })
})
