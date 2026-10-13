// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { usePrepPlan } from '../usePrepPlan.js'

// 写路径（登记 / 撤销 / 报废 / 生成计划 / 拉临期批次）随「不登记备货数量、每次计算都按 0
// 库存」的产品模型一起删了。这里盯住的是**返回值本身**：那几个方法不许再挂回来 ——
// 页面上的「不存在」断言挡不住有人把它们重新 export 出来。
describe('usePrepPlan 只剩读路径', () => {
  it('不再导出任何写方法', () => {
    const plan = usePrepPlan()
    for (const name of ['recordItem', 'undoItem', 'discardBatch', 'createPlanRun', 'loadExpiring']) {
      expect(plan[name], `${name} 还在 usePrepPlan 上`).toBeUndefined()
    }
    // 写状态的持有者（登记数量 / 撤销批次 / 报废目标 / 临期批次）也一起走了。
    for (const name of ['registerQty', 'extraRecordOpen', 'discardTarget', 'expiring']) {
      expect(plan[name], `${name} 还在 usePrepPlan 上`).toBeUndefined()
    }
  })

  it('读路径齐全：刷新建议 + 时间窗 + 档口筛选', () => {
    const plan = usePrepPlan()
    for (const name of [
      'refresh',
      'items',
      'missingRules',
      'lowConfidence',
      'windowRange',
      'stationFilter',
      'preset',
      'exportOpen',
      'statusText',
      'errorText',
      'busy',
    ]) {
      expect(plan[name], `${name} 不在 usePrepPlan 上`).toBeDefined()
    }
  })
})
