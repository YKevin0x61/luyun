import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))

function read(rel) {
  return readFileSync(join(here, rel), 'utf8')
}

const ADMIN_VIEWS = ['HygieneDailyView', 'HygieneFixView', 'HygieneDeepCleanView']

describe('驳回：不可撤销的操作要确认', () => {
  it.each(ADMIN_VIEWS)('%s 的驳回按钮走确认弹窗而不是直接提交', (name) => {
    const src = read(`../${name}.vue`)
    expect(src).toMatch(/@click="askReject"/)
    expect(src).not.toMatch(/@click="decide\('reject'\)"/)
    expect(src).toMatch(/function askReject\(\)/)
    expect(src).toMatch(/async function confirmReject\(/)
    expect(src).toMatch(/v-if="rejectOpen"/)
    expect(src).toMatch(/@confirm="confirmReject"/)
  })

  it('确认文案说清后果：员工要重拍，红黑榜会记一次', () => {
    for (const name of ADMIN_VIEWS) {
      const src = read(`../${name}.vue`)
      expect(src).toMatch(/红黑榜会记一次驳回|红黑榜会记一次/)
    }
  })
})

describe('驳回原因：验收人写得出，员工看得到', () => {
  it.each(ADMIN_VIEWS)('%s 的确认框带一个可选原因输入', (name) => {
    const src = read(`../${name}.vue`)
    expect(src).toMatch(/:prompt="\{ label: '哪里不合格（可选，员工能看到）'/)
    expect(src).toMatch(/async function confirmReject\(reason\)/)
    expect(src).toMatch(/action === 'reject' && reason/)
  })

  it('ConfirmDialog 支持可选输入并回传文本', () => {
    const dialog = read('../../../components/admin/ConfirmDialog.vue')
    expect(dialog).toMatch(/prompt: \{ type: Object, default: null \}/)
    expect(dialog).toMatch(/<label v-if="prompt" class="modal-prompt">/)
    expect(dialog).toMatch(/emit\('confirm', value\.trim\(\)\)/)
  })

  it('员工端把原因显示在那一行上', () => {
    const home = read('../HygieneHomeView.vue')
    expect(home).toMatch(/已驳回：\$\{task\.rejectReason\}/)
    const flow = read('../../../utils/hygieneWorkFlow.js')
    expect(flow).toMatch(/rejectReason: rejectReason \|\| ''/)
    expect(flow).toMatch(/rejectReason: row\.reject_reason/)
  })

  it('手机端管理员的驳回同样要确认与原因', () => {
    // 管理员也会在 /hygiene 里验收：这一屏的"驳回"紧挨"通过"，误触代价与后台一致。
    const home = read('../HygieneHomeView.vue')
    expect(home).toMatch(/const rejectConfirmOpen = ref\(false\)/)
    expect(home).toMatch(/function askReject\(\)/)
    expect(home).toMatch(/async function confirmReject\(reason\)/)
    expect(home).toMatch(/v-if="rejectConfirmOpen"/)
    expect(home).not.toMatch(/@click="decide\('reject'\)"/)
    // 三种验收（日常/专项/整改）都要把原因带给后端
    expect(home).toMatch(/const rejectBody = action === 'reject' && reason \? \{ reason \} : undefined/)
    expect(home).toMatch(/body: rejectBody/)
    expect(home).toMatch(/body: \{ shift: row\.shift, \.\.\.\(rejectBody \|\| \{\}\) \}/)
  })
})

describe('员工端要看出「这一项被打回过」', () => {
  it('flags a rejected queue row instead of looking never-touched', () => {
    const home = read('../HygieneHomeView.vue')
    expect(home).toMatch(/v-if="task\.rejected"/)
    expect(home).toMatch(/已驳回，请重拍/)
  })

  it('carries the server flag through buildWorkQueue', () => {
    const flow = read('../../../utils/hygieneWorkFlow.js')
    expect(flow).toMatch(/rejected: Boolean\(rejected\)/)
    expect(flow).toMatch(/rejected: row\.rejected/)
  })
})

describe('登出前要护住没传完的照片', () => {
  it('asks before signing out while the upload queue is not empty', () => {
    const home = read('../HygieneHomeView.vue')
    expect(home).toMatch(/@click="askLogout"/)
    expect(home).not.toMatch(/@click="logout"/)
    expect(home).toMatch(/function askLogout\(\)/)
    expect(home).toMatch(/imageUploads\.activeTasks\.length \|\| imageUploads\.failedTasks\.length/)
    expect(home).toMatch(/v-if="logoutConfirmOpen"/)
    expect(home).toMatch(/还有照片没传完/)
  })
})
