import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

/**
 * 卫生员工首页的 30s 时钟：页面不可见时必须停掉，回来补一次（DOC-08①）。
 *
 * 台账 §3.9（§0 第 18 条）挂账两天没人认领：`setInterval(tickClock, 30_000)` 无条件
 * 每 30 秒把 `nowTick` 换一个新值，而 `workQueue` 依赖它 → 员工手机锁屏 / 切到别的 app
 * 时照样全量重算待办队列。这是员工手机上的常驻开销（电量与弱机流畅度）。
 *
 * 同一条目还要求 `nowTick` 量化到分钟：`buildWorkQueue` 的桶是「超时 / 快到期 / 等待」，
 * 判据是分钟级的 deadline，秒级抖动只会让 computed 白重算。
 *
 * 这个仓库前端没有 @vue/test-utils（只有 vitest），同目录的用例都是源码契约式断言
 * （`adminSection.test.js` 等）——这里沿用同一路数：钉住"门控存在且形状正确"，
 * 而不是挂载组件去数重算次数。
 */
const here = dirname(fileURLToPath(import.meta.url))
const source = readFileSync(join(here, '../HygieneHomeView.vue'), 'utf8')

describe('HygieneHomeView 时钟门控', () => {
  it('注册了 visibilitychange 监听并在卸载时摘掉', () => {
    expect(source).toMatch(/addEventListener\(\s*['"]visibilitychange['"]/)
    expect(source).toMatch(/removeEventListener\(\s*['"]visibilitychange['"]/)
  })

  it('用统一的 startClock/stopClock 控制定时器，而不是裸 setInterval', () => {
    expect(source).toMatch(/function startClock\(\)/)
    expect(source).toMatch(/function stopClock\(\)/)
    expect(source).toMatch(/clockTimer = window\.setInterval/)
    // 门控 = 订阅回调里按 document.hidden 起停
    expect(source).toMatch(/document\.hidden/)
    expect(source).toMatch(/onVisibilityChange/)
  })

  it('nowTick 量化到分钟边界', () => {
    // Date.now() 必须先被 Math.floor 到分钟（60_000）再赋值
    expect(source).toMatch(/Math\.floor\(\s*Date\.now\(\)\s*\/\s*60_?000\s*\)\s*\*\s*60_?000/)
  })

  it('除了量化赋值处，不再有第二处直接写 Date.now()', () => {
    const writes = source.match(/nowTick\.value\s*=/g) || []
    expect(writes.length).toBe(1)
  })

  it('回到前台会补一次时钟（不是等下一个 30s）', () => {
    const handler = source.match(/function onVisibilityChange\(\)\s*\{[\s\S]*?\n\}/)
    expect(handler, '找不到 onVisibilityChange 的实现').toBeTruthy()
    expect(handler[0]).toMatch(/stopClock\(\)/)
    // 补一次：startClock() 内部会先 tickClock()，所以两条路都算（源码里必须能看到
    // 「立刻对时」这一步）。
    const startClock = source.match(/function startClock\(\)\s*\{[\s\S]*?\n\}/)
    expect(startClock, '找不到 startClock 的实现').toBeTruthy()
    expect(startClock[0]).toMatch(/tickClock\(\)/)
    expect(handler[0]).toMatch(/startClock\(\)/)
  })

  it('挂载时用的是 startClock（首帧也要立即对时）', () => {
    const mounted = source.match(/onMounted\(\(\) => \{[\s\S]*?\n\}\)/)
    expect(mounted, '找不到 onMounted 的实现').toBeTruthy()
    expect(mounted[0]).toMatch(/startClock\(\)/)
    expect(mounted[0]).not.toMatch(/setInterval/)
  })
})
