import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

// 采集节（原「POS 凭据」+「运行配置」两节合并）的**源码级契约**。锁的是另外几件事：
//
//   1. 文案预算：默认态（不含折叠内容）可见的说明性文字合计 ≤ 150 字，单条 ≤ 20 字，
//      且不含机制解释（文件路径 / 内部实现 / 设计缘由）—— 机制解释进默认折叠的 <details>；
//   2. 同一事实只出现一处：facts 只放只读元信息，值（手机号 / shop_id / company_id /
//      门店名称 / delivery_shop_id / 营业时段 / 轮询间隔 / 无头模式）只在表单里；
//   3. 反馈就近：七个写操作各有自己那条就地提示条，且渲染在触发它的按钮旁边；
//   4. 390px：窄屏单列、按钮不折字、长消息不撑破容器。
//
// 与 views/__tests__/setupPanels.test.js 同源：本套前端测试的 vitest 环境是 `node`
// （见 vite.config.js），全站既有测试都是读组件源码验契约，不挂载组件。

const here = dirname(fileURLToPath(import.meta.url))
const FILE = join(here, '../CollectSection.vue')
const src = readFileSync(FILE, 'utf8')
const template = src.slice(src.indexOf('<template>'), src.indexOf('<style'))
const style = src.slice(src.indexOf('<style'), src.indexOf('</style>'))
const script = src.slice(0, src.indexOf('</script>'))

/** 去掉标签与空白：模板里的换行/缩进/内联 <code> 都不该影响文案长度。 */
function stripTags(text) {
  return text.replace(/<[^>]+>/g, '').replace(/\s+/g, '')
}

/** 折叠区整段去掉（连 summary 一起）——"默认态整段模板里都不许出现"的判据。 */
function withoutDetails(source) {
  return source.replace(/<details[\s\S]*?<\/details>/g, '')
}

/** 默认态（折叠区只算 summary 那一行）可见的说明性文字：description / note / .hint / <summary>。 */
function defaultVisibleProse(source) {
  const unfolded = source.replace(/<details[\s\S]*?<\/details>/g, (block) => {
    const summary = block.match(/<summary[^>]*>[\s\S]*?<\/summary>/)
    return summary ? summary[0] : ''
  })
  const lines = []
  for (const m of unfolded.matchAll(/(?:description|note)="([^"]*)"/g)) lines.push(m[1])
  for (const m of unfolded.matchAll(/<p class="hint"[^>]*>([\s\S]*?)<\/p>/g)) lines.push(m[1])
  for (const m of unfolded.matchAll(/<div class="hint"[^>]*>([\s\S]*?)<\/div>/g)) lines.push(m[1])
  for (const m of unfolded.matchAll(/<summary[^>]*>([\s\S]*?)<\/summary>/g)) lines.push(m[1])
  return lines.map(stripTags).filter(Boolean)
}

/** 从 `open` 处的 `(` 或 `{` 起取配对的括号正文，用于断言某个 computed / 函数的实现。 */
function balanced(text, start, close) {
  expect(start, `找不到起点`).toBeGreaterThan(-1)
  const open = close === ')' ? '(' : '{'
  let depth = 0
  for (let i = start; i < text.length; i += 1) {
    if (text[i] === open) depth += 1
    else if (text[i] === close) {
      depth -= 1
      if (depth === 0) return text.slice(start, i + 1)
    }
  }
  return ''
}

/** 取一个 computed 的源码主体（`const x = computed(` 到配对的 `)`）。 */
function computedBody(name) {
  const start = src.indexOf(`const ${name} = computed(`)
  expect(start, `${name} 应当是 computed`).toBeGreaterThan(-1)
  return balanced(src, src.indexOf('(', start), ')')
}

/** 取一个函数的源码主体（`function x(` 到配对的 `}`）。 */
function functionBody(name) {
  const start = src.indexOf(`function ${name}(`)
  expect(start, `${name} 应当是一个函数`).toBeGreaterThan(-1)
  return balanced(src, src.indexOf('{', start), '}')
}

/** 源码里该字符串出现的位置（模板内）。 */
function at(needle) {
  const i = template.indexOf(needle)
  expect(i, `模板里找不到 ${needle}`).toBeGreaterThan(-1)
  return i
}

describe('采集节 · 默认态文案', () => {
  it('可见的说明性文字合计 ≤ 150 字，单条 ≤ 20 字', () => {
    const lines = defaultVisibleProse(template)
    expect(lines.length).toBeGreaterThan(0)
    for (const line of lines) {
      // 每条都是"操作必需"或"风险/后果"一句话，长的机制解释一律进折叠区。
      expect([...line].length, `这条太长了：${line}`).toBeLessThanOrEqual(20)
    }
    const total = lines.reduce((sum, line) => sum + [...line].length, 0)
    expect(total, `默认态文案合计 ${total} 字：\n${lines.join('\n')}`).toBeLessThanOrEqual(150)
  })

  it('机制解释（文件路径 / 内部实现 / 设计缘由）只在默认折叠的 <details> 里', () => {
    const unfolded = withoutDetails(template)
    // 说明性文字里一个机制词都不留（cy7mm 只允许作为 URL 输入框的示例出现，见下一条）。
    const proseMechanism = ['data/credentials.enc', 'Fernet', 'cy7mm', 'WebView', '{shopId}', 'centerId', '无需重启', '落盘', '接口']
    const prose = defaultVisibleProse(template)
    for (const marker of proseMechanism) {
      for (const line of prose) expect(line, `${marker} 不该出现在默认态文案里`).not.toContain(marker)
    }
    // 这些机制词在默认态整段模板里都不该出现，只能待在折叠区。
    const templateMechanism = ['data/credentials.enc', 'Fernet', 'WebView', '{shopId}', 'centerId', '无需重启', '落盘']
    for (const marker of templateMechanism) {
      expect(unfolded, `${marker} 不该出现在默认态`).not.toContain(marker)
      expect(src, `${marker} 该被折叠区收着，不是删掉`).toContain(marker)
    }
    // 默认折叠：两处 <details> 都不带 open；折叠块有看得见的开合标记（chevron + 展开旋转）。
    expect(template.match(/<details[^>]*\sopen/)).toBeNull()
    expect(template.match(/<details/g)?.length).toBe(2)
    expect(template.match(/<details class="section-help">/g)?.length).toBe(2)
    expect(template.match(/name="chevron-right"/g)?.length).toBe(2)
    expect(style.replace(/\s+/g, '')).toContain('.section-help[open].section-help__icon{transform:rotate(90deg);}')
  })

  it('门店信息的两个入口各留一句操作提示，长说明已经收进折叠区', () => {
    const hints = defaultVisibleProse(template)
    expect(hints).toContain('未配置凭据时，需先填手机号与密码。')
    expect(hints).toContain('粘贴App内「实时桌态」页的完整地址。')
    // 原来那两段机制说明（自动获取字段、WebView 地址栏怎么取）在折叠区里。
    expect(template).toContain('自动获取 <code>shop_id</code>')
    expect(template).toContain('WebView 地址栏的完整 URL')
  })
})

describe('采集节 · 同一事实只出现一处', () => {
  it('POS facts 只留只读元信息（更新时间），值只在表单', () => {
    const allowed = src.slice(src.indexOf('const READONLY_FACT_KEYS'), src.indexOf('\n', src.indexOf('const READONLY_FACT_KEYS')))
    const body = computedBody('posFacts')
    expect(stripTags(allowed)).toContain("'更新时间'")
    expect(body).toContain('READONLY_FACT_KEYS.has(k)')
    // 手机号 / shop_id / company_id / 门店名称 / delivery_shop_id 都不再进 facts。
    for (const duplicated of ['账号', 'shop_id', 'company_id', '门店名称', 'delivery_shop_id']) {
      expect(allowed, `${duplicated} 该只在表单里`).not.toContain(duplicated)
      expect(body, `${duplicated} 该只在表单里`).not.toContain(duplicated)
    }
  })

  it('运行配置 facts 不复述表单值', () => {
    const body = computedBody('runtimeFacts')
    expect(stripTags(body)).toContain("'上次保存'")
    // 营业时段 / 轮询间隔 / 浏览器模式都是表单里的值。
    expect(body).not.toContain('runtimeForm')
    expect(body).not.toContain('营业时段')
    expect(body).not.toContain('轮询间隔')
    expect(body).not.toContain('浏览器模式')
  })

  it('字段标签回到裸名，字段来源只在折叠区里解释', () => {
    const unfolded = template.replace(/<details[\s\S]*?<\/details>/g, '')
    expect(unfolded).toContain('<label for="shopId">shop_id</label>')
    expect(unfolded).toContain('<label for="companyId">company_id</label>')
    expect(unfolded).toContain('<label for="shopName">门店名称</label>')
    expect(unfolded).toContain('<label for="deliveryShopId">delivery_shop_id</label>')
    for (const tail of ['第 1 段', '第 2 段', 'centerId', 'shopName 参数', '已结账单接口']) {
      expect(unfolded, `${tail} 不该挂在标签上`).not.toContain(tail)
    }
  })
})

describe('采集节 · 写操作反馈就地、紧贴按钮', () => {
  const BARS = ['alert', 'verifyAlert', 'discoverAlert', 'parseAlert', 'runtimeAlert']

  it('五个动作区各一条提示条，各渲染一次', () => {
    for (const bar of BARS) {
      expect(template.match(new RegExp(`v-if="${bar}\\.show"`, 'g')), `${bar} 的提示条`).toHaveLength(1)
      expect(template.match(new RegExp(`:class="${bar}\\.type"`, 'g')), `${bar} 的色调`).toHaveLength(1)
      expect(script).toContain(`const ${bar} = reactive(createAlertBar())`)
    }
  })

  it('七个写操作各自声明自己的 spot（反馈才落得到自己那条提示条上）', () => {
    const bindings = [
      "runSpotted('verify', onVerify)", // 验证登录
      "runSpotted('discover', onDiscoverShops)", // 从账号拉取门店
      "runSpotted('discover', () => onPickDiscoveredShop($event))", // 多门店切换
      "runSpotted('parse', onParseUrl)", // 解析
      "runSpotted('pos', onSubmitCred)", // 保存并启用
      "runSpotted('pos', fetchCurrent)", // 刷新（凭据）
      "runSpotted('runtime', loadRuntimeSettings)", // 刷新（运行配置）
      "runSpotted('runtime', saveRuntimeSettings)", // 保存并生效
    ]
    for (const binding of bindings) expect(template).toContain(binding)
    // 清空凭据走两步确认，确认后 onClearCredentials 的反馈落到 pos 那条（见下一条用例）。
    expect(template).toContain('@click="onClearCredentialsConfirm"')
  })

  it('提示条就渲染在触发它的按钮旁边（源码距离；粗到 600 字符）', () => {
    // 实测距离（改版当时的模板）：验证登录 114、从账号拉取门店 136、解析 91、
    // 保存并启用 225、刷新 164、清空凭据 455、保存并生效 436、恢复默认 304。
    // 阈值留了余量：把提示条挪到分节顶部或页头（原页面那套全局 alert）会立刻超。
    const pairs = [
      ["runSpotted('verify', onVerify)", 'verifyAlert'],
      ["runSpotted('discover', onDiscoverShops)", 'discoverAlert'],
      ["runSpotted('parse', onParseUrl)", 'parseAlert'],
      ['type="submit" class="btn btn-primary"', 'alert'],
      ['@click="onClearCredentialsConfirm"', 'alert'],
      ["runSpotted('runtime', saveRuntimeSettings)", 'runtimeAlert'],
      ['@click="onResetRuntimeDefaults"', 'runtimeAlert'],
    ]
    for (const [button, bar] of pairs) {
      const distance = Math.abs(at(button) - at(`${bar}.show`))
      expect(distance, `${bar} 离触发它的按钮 ${distance} 字符远`).toBeLessThanOrEqual(600)
    }
  })

  it('「恢复默认」只填表单、不落库：就近说明"还没保存"', () => {
    expect(script).toContain('function onResetRuntimeDefaults()')
    expect(script).toContain('resetRuntimeDefaults()')
    expect(script).toContain("writeAlert('runtime', 'info', '已填入默认值，保存后生效。')")
  })

  it('保存成功的提示不会被紧接着的 clearAlert 抹掉（原页面那条绿条只闪一下）', () => {
    const body = functionBody('clearAlert')
    expect(body).toContain("bar.type === 'success'")
    expect(body).toContain('continue')
    expect(script).toContain('fetchCurrent 开头就 clearAlert')
  })

  it('反馈条不接全局 alert：分节自持，壳里没有它的渲染点', () => {
    expect(script).toContain('function showAlert(')
    expect(script).toContain('function clearAlert(')
    expect(script).toContain('usePosCredentials({ showAlert, clearAlert })')
    expect(script).toContain('useRuntimeSettings({ showAlert, clearAlert })')
    expect(template).toMatch(/v-if="alert\.show"/)
    expect(template).toMatch(/:class="alert\.type"/)
  })
})

describe('采集节 · 390px 与契约保持', () => {
  const css = style.replace(/\s+/g, '')

  it('窄屏单列、按钮不折字、长消息不撑破容器', () => {
    expect(css).toContain('@media(max-width:700px){.grid{grid-template-columns:1fr;}')
    // 三个按钮（刷新 / 保存并启用 / 清空凭据）在 390px 下宁可换行，也不把文字挤成两行。
    expect(css).toContain('.actions.btn,.field-block__head.btn{white-space:nowrap;}')
    // 失败反馈里带 URL / 路径这类长串，390px 下要能断行。
    expect(css).toContain('.alert{')
    expect(css).toContain('overflow-wrap:anywhere;')
    expect(css).toContain('.section-help__listli{margin-bottom:4px;overflow-wrap:anywhere;}')
    // 本节自己不造横向滚动（分节内容不许有固定宽度）。
    expect(css).not.toContain('overflow-x:auto')
    expect(css.match(/min-width:\d{3,}px/)).toBeNull()
  })

  it('壳的分节契约一条不落（props / inject / 自取数据 / 样式范围）', () => {
    expect(script).toContain('原「POS 凭据」+「运行配置」')
    expect(script).toContain('active: { type: Boolean, default: false }')
    expect(src).toContain("refreshKey: { type: Number, default: 0 }")
    expect(script).toContain('() => props.active')
    expect(script).toContain('{ immediate: true }')
    expect(script).toContain('() => props.refreshKey')
    expect(script).toContain('inject(SETTINGS_MODAL_HOST, null)')
    expect(script).toContain('modalHost?.requestConfirm(')
    // 请求逻辑仍在 composable 里：分节不直接 api.*。
    expect(src).not.toMatch(/\bapi\.(get|post|put|patch|delete)\(/)
    expect(src).toContain('<style scoped>')
    expect(src.match(/<style(?! scoped)/)).toBeNull()
  })

  it('没有引出壳的其它入口（不 emit、不注册别的弹窗）', () => {
    expect(src).not.toContain('defineEmits')
    expect(src).not.toContain('$emit')
    expect(script).not.toContain("register('")
  })
})
