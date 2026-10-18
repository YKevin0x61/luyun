<script setup>
import { ref } from 'vue'
import LuyunDatePicker from '../../components/ui/LuyunDatePicker.vue'
import { chinaTodayDate } from '../../utils/hygieneTime'
import { parseApiDetail, staffRequest } from '../../utils/hygieneStaff'

const phone = ref('')
const name = ref('')
const idCardNo = ref('')
const healthCertDate = ref('')
/** 入职日期（2026-10-13 用户裁定）：由员工自己填。**允许将来** —— 人还没到岗先建档，
 *  与健康证办理日期（不许将来）刻意相反，所以这一格没有 `:max`。 */
const hireDate = ref('')

/** 「暂时没有健康证」（2026-10-13 用户裁定）：确实还没办证的人不该被这一格挡在注册
 *  门外。勾上它，办理日期就不是必填了 —— 但**两者必有其一**，空着手交仍然是
 *  「请选择健康证办理日期」（漏填与"明确没有"是两件事，服务端凭这个标记区分）。 */
const noHealthCert = ref(false)

/** 勾上「暂时没有」就把日期格清掉：两个都留着，服务端会拿到一份自相矛盾的请求
 *  （有日期又声明没有），前端不该生产这种组合。 */
function toggleNoHealthCert() {
  if (noHealthCert.value) healthCertDate.value = ''
  clearError()
}
const password = ref('')
const confirmPassword = ref('')
const submitting = ref(false)
const errorText = ref('')
const submitted = ref(false)
/** 北京时的今天：日期框的上限（不得晚于今天），与提交前那条校验同一口径。 */
const todayDate = chinaTodayDate()

/** 中国大陆手机号（与服务端同一口径：`1` + 3-9 + 9 位数字）。 */
const PHONE_RE = /^1[3-9]\d{9}$/
/** 密码长度下限（字符数）。与服务端 `AUTH_MIN_PASSWORD_LENGTH` 的默认值一致。 */
const MIN_PASSWORD_LENGTH = 8
/** 18 位身份证：前 17 位数字 + 末位数字或 X（小写归一成大写）。 */
const ID_CARD_RE = /^\d{17}[\dX]$/
/** 校验位算法（GB 11643-1999）：前 17 位的加权和取模 11，对照下表。 */
const ID_CARD_WEIGHTS = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
const ID_CARD_CHECK = ['1', '0', 'X', '9', '8', '7', '6', '5', '4', '3', '2']

function idCardChecksumOk(value) {
  let sum = 0
  for (let index = 0; index < 17; index += 1) {
    sum += Number(value[index]) * ID_CARD_WEIGHTS[index]
  }
  return ID_CARD_CHECK[sum % 11] === value[17]
}

/** 提交前的校验，**文案与服务端 `api/hygiene.py` 的 `_ERROR_DETAILS` 逐字一致**
 *  （`请输入有效的中国大陆手机号` / `请填写身份证号` / `健康证办理日期不能是将来` /
 *  `请选择入职日期` / `密码至少 8 位` / `该手机号已注册`）。
 *
 *  表单上挂了 `novalidate`：不这么做的话浏览器会先弹**英文原生提示**
 *  （`Please match the requested format.` / `Please lengthen this text to 8 characters
 *  or more`），跟整页中文打架，而且原生校验先拦下来，服务端备好的中文永远走不到
 *  （D12）。校验顺序也照服务端：姓名 → 手机号 → 身份证号 → 健康证办理日期 →
 *  入职日期 → 密码长度 → 两次一致 —— 顺序不同会出现在这一侧先报、到那一侧又换一句的情况。
 *
 *  返回第一条错误，全通过返回空串。单独写成一个纯函数是为了能在单测里逐条压。
 */
function validateRegistration({
  name,
  phone,
  idCard,
  healthCert,
  noHealthCert,
  hireDate,
  password,
  confirmPassword,
}) {
  if (!String(name || '').trim()) return '请填写员工姓名'
  if (!PHONE_RE.test(String(phone || '').trim())) return '请输入有效的中国大陆手机号'
  const idCardValue = String(idCard || '').trim().toUpperCase()
  if (!idCardValue) return '请填写身份证号'
  if (!ID_CARD_RE.test(idCardValue)) return '身份证号应为 18 位'
  if (!idCardChecksumOk(idCardValue)) return '身份证号校验位不对'
  const certDate = String(healthCert || '').trim()
  // 健康证办理日期与「暂时没有健康证」是**两选一**（2026-10-13）：勾了后者就不是漏填。
  if (!certDate && !noHealthCert) return '请选择健康证办理日期'
  // 北京时的今天：手机时区可能不在东八区，而服务端按北京时判「不能是将来」。
  if (certDate > chinaTodayDate()) return '健康证办理日期不能是将来'
  // 入职日期**不判将来**：提前建档是常态（服务端同一口径，见 `profile.py` 的
  // `normalize_date` 与 `normalize_health_cert_date` 两条注释）。
  if (!String(hireDate || '').trim()) return '请选择入职日期'
  if (String(password || '').length < MIN_PASSWORD_LENGTH) {
    return `密码至少 ${MIN_PASSWORD_LENGTH} 位`
  }
  if (password !== confirmPassword) return '两次输入的密码不一致'
  return ''
}

/** 输入变化时清掉上一次的错误（D18：改好了提示还挂着，用户会以为没生效）。
 *  提交级与接口级的提示都在 `errorText` 一处，改任意一格就作废。 */
function clearError() {
  if (errorText.value) errorText.value = ''
}

async function submit() {
  errorText.value = ''
  const invalid = validateRegistration({
    name: name.value,
    phone: phone.value,
    idCard: idCardNo.value,
    healthCert: healthCertDate.value,
    noHealthCert: noHealthCert.value,
    hireDate: hireDate.value,
    password: password.value,
    confirmPassword: confirmPassword.value,
  })
  if (invalid) {
    errorText.value = invalid
    return
  }
  submitting.value = true
  try {
    await staffRequest('/api/hygiene/staff/register', {
      method: 'POST',
      body: {
        name: name.value.trim(),
        phone: phone.value.trim(),
        id_card_no: idCardNo.value.trim().toUpperCase(),
        // 勾了「暂时没有」就送空日期 + 标记位：服务端凭它区分「明确没有」与「漏填」
        // （漏填仍然 400），落库同样是空 —— `none` 状态在库里只有一种表示。
        health_cert_date: noHealthCert.value ? '' : healthCertDate.value,
        health_cert_missing: noHealthCert.value,
        hire_date: hireDate.value,
        password: password.value,
      },
    })
    submitted.value = true
  } catch (err) {
    errorText.value = err.message || parseApiDetail(null)
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="reg">
    <header class="reg-head">
      <p class="reg-kicker">REGISTER · 员工自助注册</p>
      <h1 class="reg-title">员工注册</h1>
      <p class="reg-sub">要填姓名、手机号、身份证号、健康证日期与入职日期；提交后等管理员批准。</p>
    </header>

    <div v-if="submitted" class="reg-panel">
      <header class="reg-ph"><i class="reg-led ok"></i><h2>已提交</h2><span class="reg-hint">等管理员批准</span></header>
      <div class="reg-done">
        <p>已提交。等管理员批准后再登录。</p>
        <router-link class="btn btn-primary btn-block hy-staff-submit" to="/login">去登录</router-link>
      </div>
    </div>

    <template v-else>
      <p v-if="errorText" id="regError" class="hy-staff-alert reg-alert" role="alert">{{ errorText }}</p>
      <!-- `novalidate`：校验与文案都由上面那一份中文判据说了算，不让浏览器先弹英文
           （D12）。字段上原来的 `required` / `pattern` / `minlength` 随之撤掉 —— 留着它们
           只会让读屏与表单控件以为还有一层原生校验，而那一层已经被关掉。
           `autocomplete` 不关：新密码要让密码管理器收存（原来整表 `autocomplete="off"`
           会把刚设的密码丢掉，D17）。 -->
      <section class="reg-panel">
        <header class="reg-ph"><i class="reg-led sig"></i><h2>资料</h2><span class="reg-hint">6 项 · 全部必填</span></header>
        <form novalidate class="reg-form" @submit.prevent="submit">
          <div class="reg-row">
            <label for="regName">姓名</label>
            <input
              id="regName"
              v-model="name"
              class="input"
              type="text"
              maxlength="40"
              autocomplete="name"
              placeholder="请输入真实姓名"
              :aria-invalid="errorText ? 'true' : 'false'"
              :aria-describedby="errorText ? 'regError' : undefined"
              @input="clearError"
            >
          </div>
          <div class="reg-row">
            <label for="regPhone">手机号</label>
            <input
              id="regPhone"
              v-model="phone"
              class="input"
              type="tel"
              inputmode="numeric"
              maxlength="11"
              autocomplete="username"
              placeholder="11 位中国大陆手机号"
              :aria-invalid="errorText ? 'true' : 'false'"
              :aria-describedby="errorText ? 'regError' : undefined"
              @input="clearError"
            >
          </div>
          <div class="reg-row">
            <label for="regIdCard">身份证号</label>
            <input
              id="regIdCard"
              v-model="idCardNo"
              class="input"
              type="text"
              maxlength="18"
              autocomplete="off"
              placeholder="18 位身份证号"
              :aria-invalid="errorText ? 'true' : 'false'"
              :aria-describedby="errorText ? 'regError' : undefined"
              @input="clearError"
            >
          </div>
          <div class="reg-row">
            <!-- 日期两格用 `LuyunDatePicker` 而不是原生 `<input type=date>`（2026-10-13 用户
                 裁定「日期格式要中国本土化」）：**原生的显示格式跟浏览器界面语言走**，中文机
                 是 `yyyy/mm/dd`、英文机是 `mm/dd/yyyy`，页面上的 `lang` 属性改不动它（实测
                 元素级 `lang="zh-CN"` 与 `locale` 都无效，只有浏览器 `--lang` 说了算）。组件
                 把格式定死在 `yyyy-MM-dd`，与站内其它日期控件同一个样子。
                 代价两处：① 字段名改由组件的 `aria-label` 承担（`<label for>` 关联不到组件里
                 那个 input，所以标签退成 `<span>`）；② 误差提示不再挂在这一格上 —— 它是
                 `role="alert"`，读屏本来就会播报。 -->
            <span class="hy-staff-field-label reg-label" title="按健康证上的办理日期填">健康证办理日期</span>
            <LuyunDatePicker
              v-model="healthCertDate"
              :disabled="noHealthCert"
              :max-date="todayDate"
              placeholder="选择办理日期"
              aria-label="健康证办理日期"
              @update:model-value="clearError"
            />
            <!-- 日期之外的另一条合法路径（2026-10-13 用户裁定）：确实还没办证的人不该被
                 这一格挡在注册门外。勾上会清空并锁住上面那格 —— 两个都留着是一份自相
                 矛盾的请求。没勾又没填，仍然是「请选择健康证办理日期」。 -->
            <label class="hy-staff-nocert reg-check" for="regNoHealthCert">
              <input
                id="regNoHealthCert"
                v-model="noHealthCert"
                type="checkbox"
                @change="toggleNoHealthCert"
              >
              <span>暂时没有健康证</span>
            </label>
          </div>
          <!-- 入职日期（2026-10-13）：**没有 `max-date`** —— 这一项允许将来（人还没到岗先
               建档），与上面那格正好相反。`title` 是给不确定的人看的兜底说明。 -->
          <div class="reg-row">
            <span
              class="hy-staff-field-label reg-label"
              title="还没到岗就先填到岗那天，可以是将来的日期"
            >入职日期</span>
            <LuyunDatePicker
              v-model="hireDate"
              placeholder="选择入职日期"
              aria-label="入职日期"
              @update:model-value="clearError"
            />
          </div>
          <div class="reg-row">
            <label for="regPassword">密码</label>
            <input
              id="regPassword"
              v-model="password"
              class="input"
              type="password"
              autocomplete="new-password"
              placeholder="至少 8 位"
              :aria-invalid="errorText ? 'true' : 'false'"
              :aria-describedby="errorText ? 'regError' : undefined"
              @input="clearError"
            >
          </div>
          <div class="reg-row">
            <label for="regConfirm">确认密码</label>
            <input
              id="regConfirm"
              v-model="confirmPassword"
              class="input"
              type="password"
              autocomplete="new-password"
              placeholder="再次输入密码"
              :aria-invalid="errorText ? 'true' : 'false'"
              :aria-describedby="errorText ? 'regError' : undefined"
              @input="clearError"
            >
          </div>
          <div class="reg-actions">
            <button type="submit" class="btn btn-primary btn-block hy-staff-submit" :disabled="submitting">
              {{ submitting ? '正在提交…' : '提交注册' }}
            </button>
          </div>
        </form>
      </section>
      <p class="hy-staff-switch reg-switch">
        已经注册？
        <router-link to="/login">去登录</router-link>
      </p>
    </template>
  </div>
</template>

<style scoped>
/* B 案 v2（2026-10-17）：注册表单收成页头 + 钢面面板 + 行式字段。
   入口壳（`HygieneStaffAuthLayout`）已经把卡平化，这里只管表单内部节奏；
   颜色/字体只走语义令牌。 */
.reg { display: flex; flex-direction: column; }
.reg-head { margin-bottom: 14px; }
.reg-kicker { margin: 0 0 7px; font: 500 9.5px/1 var(--font-sans); letter-spacing: .26em; color: var(--ink-faint); }
.reg-title { margin: 0; font: 600 26px/1.1 var(--font-sans); letter-spacing: .01em; color: var(--ink); }
.reg-sub { margin: 8px 0 0; font-size: 11px; line-height: 1.65; color: var(--ink-dim); }
.reg-panel {
  background: var(--surface-1);
  border: 1px solid var(--line);
  border-radius: var(--r-2);
  box-shadow: 0 1px 2px var(--wb-ink-a08);
  overflow: hidden;
}
.reg-ph { display: flex; align-items: center; gap: 9px; padding: 10px 12px; border-bottom: 1px solid var(--line); }
.reg-ph h2 { margin: 0; font: 600 12.5px/1 var(--font-sans); letter-spacing: .06em; color: var(--ink); }
.reg-hint { margin-left: auto; font: 500 9.5px/1 var(--font-mono); color: var(--ink-faint); }
.reg-led { width: 6px; height: 6px; border-radius: 50%; display: inline-block; flex: 0 0 6px; background: var(--line-strong); }
.reg-led.ok { background: var(--ok); }
.reg-led.sig { background: var(--action); }
.reg-form { display: flex; flex-direction: column; padding: 4px 0 0; }
.reg-row {
  display: flex; flex-direction: column; gap: 6px;
  padding: 10px 12px 0;
}
.reg-row:last-of-type { padding-bottom: 12px; }
.reg-row > label,
.reg-row .reg-label {
  font: 500 9.5px/1 var(--font-sans);
  letter-spacing: .18em;
  color: var(--ink-faint);
  margin: 0;
}
.reg-row :deep(.input),
.reg-row :deep(.luyun-date-picker) { width: 100%; }
.reg-row :deep(.input) { min-height: 44px; border-radius: var(--r-1); }
.reg-check { display: flex; align-items: center; gap: 8px; min-height: 48px; font-size: 11px; color: var(--ink-dim); cursor: pointer; }
.reg-check input { width: 16px; height: 16px; accent-color: var(--action); }
.reg-actions { padding: 14px 12px 12px; border-top: 1px solid var(--wb-ink-a08); }
.reg-done { display: flex; flex-direction: column; gap: 12px; padding: 14px 12px; }
.reg-done p { margin: 0; font-size: 11px; color: var(--ink-dim); }
.reg-alert { margin: 0 0 12px; font-size: 11px; color: var(--danger-bright); }
.reg-switch { margin: 14px 0 0; font-size: 11px; color: var(--ink-dim); }
.reg-switch a { color: var(--action); }
.reg :deep(.btn.btn) { min-height: 48px !important; border-radius: var(--r-1); }
@media (max-width: 720px) {
  .reg-title { font-size: 22px; }
}
</style>
