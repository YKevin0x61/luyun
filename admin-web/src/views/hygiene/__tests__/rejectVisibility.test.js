import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))

function read(rel) {
  return readFileSync(join(here, rel), 'utf8')
}

const ADMIN_VIEWS = ['HygieneDailyView', 'HygieneFixView', 'HygieneDeepCleanView']

// 驳回入口的全集（2026-10-05）。**加了新的驳回入口就要加进这张表** —— 后端七个驳回
// 接口都强制要原因，前端漏一处，那条路上的管理员就会点出一个"没反应"的按钮。
// 仪容那一页不在 ADMIN_VIEWS 里（它用的是行内输入框，不是共用弹窗），单独压。
const REJECT_DIALOG_VIEWS = [...ADMIN_VIEWS, 'HygieneHomeView']
// 弹窗那几处 placeholder 写在 `:prompt` 的对象里，仪容那处是行内 textarea 的普通属性。
const REJECT_PLACEHOLDER = /placeholder: '写一句让他知道改什么/
const ATTIRE_PLACEHOLDER = /placeholder="写一句让他知道改什么/

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
  // 2026-10-05 用户裁定：**驳回必须写原因**（不能选填）。这条用例原来的断言是
  // 「哪里不合格（可选，员工能看到）」，按新规则改成必填 + `required: true`：
  // 判据变了，不是断言被放松了 —— 下面那条 `required: true` 就是新规矩的钉子。
  it.each(REJECT_DIALOG_VIEWS)('%s 的确认框带一个必填原因输入', (name) => {
    const src = read(`../${name}.vue`)
    expect(src).toMatch(/:prompt="\{ label: '哪里不合格（必填，员工能看到）'/)
    expect(src).toMatch(REJECT_PLACEHOLDER)
    expect(src).toMatch(/required: true/)
    expect(src).toMatch(/async function confirmReject\(reason\)/)
    expect(src).toMatch(/action === 'reject' && reason/)
  })

  it('七个驳回入口的内核都拿得到原因（员工端三处共用同一个弹窗）', () => {
    // 员工端那条路（`/workbench/me/clean` 上管理员验收的日常 / 专项 / 整改）走的是
    // 同一个 `rejectConfirmOpen` 弹窗：一个 `required: true` 管三条路。
    const home = read('../HygieneHomeView.vue')
    expect(home).toMatch(/const rejectConfirmOpen = ref\(false\)/)
    expect(home.match(/required: true/g) || []).toHaveLength(1)
    // 后台那三页各一处；仪容那一页是行内输入框（见下一条）。
    for (const name of ADMIN_VIEWS) {
      expect((read(`../${name}.vue`).match(/required: true/g) || []), name).toHaveLength(1)
    }
  })

  it('仪容那一页的行内输入框同样必填，且提示就在输入框旁', () => {
    const src = read('../HygieneAttireView.vue')
    expect(src).toMatch(/哪里不合格（必填，员工照着这句重拍）/)
    expect(src).toMatch(/class="reject-hint"/)
    expect(src).toMatch(ATTIRE_PLACEHOLDER)
    // 空着点不动「确认驳回」（以前只有页面顶上一行报错，人在输入框这格看不到）。
    expect(src).toMatch(/:disabled="busyId === row\.employee_id \|\| !rejectNote\.trim\(\)"/)
    // 兜底那道判据还在（脚本里直接调 `submitReject` 也拦得住）。
    expect(src).toMatch(/if \(!rejectNote\.value\.trim\(\)\)/)
  })

  it('ConfirmDialog 支持可选输入并回传文本', () => {
    const dialog = read('../../../components/admin/ConfirmDialog.vue')
    expect(dialog).toMatch(/prompt: \{ type: Object, default: null \}/)
    expect(dialog).toMatch(/<label v-if="prompt" class="modal-prompt">/)
    expect(dialog).toMatch(/emit\('confirm', value\.trim\(\)\)/)
  })

  it('ConfirmDialog 的必填档：没写就不让提交，旁边给一句为什么', () => {
    const dialog = read('../../../components/admin/ConfirmDialog.vue')
    expect(dialog).toMatch(/const promptRequired = computed\(\(\) => Boolean\(props\.prompt && props\.prompt\.required\)\)/)
    expect(dialog).toMatch(/const canConfirm = computed\(\(\) => !promptRequired\.value \|\| value\.value\.trim\(\)\.length > 0\)/)
    expect(dialog).toMatch(/:disabled="!canConfirm"/)
    expect(dialog).toMatch(/<em v-if="prompt\.hint">/)
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
    // 票 10：退出收到共用的一颗按钮 + `useStaffLogout` 里，卫生首页不再自己实现一遍
    // （原来这段判据是钉在首页源码上的）。
    // D5：员工三页页内那三颗撤掉了 —— 这三页都套工作台外壳，外壳顶栏那一颗（同样走
    // `useStaffLogout`）是唯一的一颗，同一个动作不再在同一屏里出现两次。
    const button = read('../../../components/staff/StaffExitButton.vue')
    const logout = read('../../../composables/useStaffLogout.js')
    const home = read('../HygieneHomeView.vue')
    // ③-2：唯一那颗工作台退出按钮在统一壳里。
    const shell = read('../../workbench/WorkbenchShell.vue')

    expect(button).toMatch(/@click="ask"/)
    expect(button).not.toMatch(/@click="logout"/)
    expect(button).toMatch(/v-if="confirmOpen"/)
    expect(button).toMatch(/还有照片没传完/)
    expect(logout).toMatch(/function ask\(\)/)
    expect(logout).toMatch(/queuedCount\.value/)
    expect(logout).toMatch(/activeTasks\.length \+ imageUploads\.failedTasks\.length/)
    // 那一颗在外壳里，员工那一档也走同一条出口（`useWorkbenchLogout` → `useStaffLogout`）。
    expect(shell).toMatch(/<WorkbenchExitButton/)
    expect(shell).toMatch(/useWorkbenchLogout/)
    expect(home).not.toMatch(/<StaffExitButton/)
    expect(home).not.toMatch(/@click="askLogout"/)
  })
})
