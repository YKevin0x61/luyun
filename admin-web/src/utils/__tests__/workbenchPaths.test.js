import { describe, expect, it } from 'vitest'
import { WORKBENCH_ROOT, isWorkbenchPath } from '../workbenchPaths.js'

describe('workbenchPaths', () => {
  it('工作台前缀：等于根与带斜杠两种都算，别的都不算', () => {
    for (const path of [
      '/workbench',
      '/workbench/',
      '/workbench/hr/calendar',
      '/workbench/me/today',
      '/workbench/kitchen/prep-plan',
      '/workbench/forbidden',
    ]) {
      expect(isWorkbenchPath(path), path).toBe(true)
    }
    // 裸 startswith 会把同前缀的邻居也放进来 —— 成对的前缀写法是刻意的
    // （服务端 `main.py` 的 `_is_workbench_page` 是同一口径）。
    for (const path of [
      '/',
      '/login',
      '/register',
      '/workbenchx',
      '/workbench-old',
      '/staff/today',
      '/recipe',
      '',
      null,
      undefined,
    ]) {
      expect(isWorkbenchPath(path), String(path)).toBe(false)
    }
  })

  it('根路径是唯一字面量：别处引用这个常量，不另抄一遍', () => {
    expect(WORKBENCH_ROOT).toBe('/workbench')
  })
})
