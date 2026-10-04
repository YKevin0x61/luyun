/** 周表上那支「笔」（2026-10-04 用户要的交互）。
 *
 *  要它解决什么：给一个人改一天，原来是「点格子 → 抽屉 → 选班次」三步；排一周就是
 *  二十几次来回。现在抽屉**折叠成一行**就是笔刷条 —— 先在条上把班次 / 工作区选好，
 *  再点表上的格子，点一下落一格；想看/改单独某一格就**长按**那一格，抽屉展开到那一格。
 *
 *  这里只放能脱离视图单测的那点逻辑：笔 → 落库 payload、以及"过去的日期不许刷"。
 *  写盘那条路跟抽屉里点班次**完全一样**（`PUT /api/scheduling/overrides/{人}/{天}`），
 *  不新开接口 —— 笔刷只是把手势换成"先选笔再落格子"。
 */

export const BRUSH_SHIFT = 'shift'
export const BRUSH_REST = 'rest'

/** 笔还没选班次时不许落笔：写一个"没有班次的覆盖"既不是休、也不是某个班，
 *  服务端只能拒或者记成脏数据 —— 宁可在客户端先说一句。 */
export function brushReady(brush) {
  const { kind, shiftId } = brush || {}
  if (kind === BRUSH_REST) return true
  return shiftId !== null && shiftId !== undefined && shiftId !== ''
}

/** 笔 → 写接口的 payload；笔不完整时返回 null（调用方据此提示，不发请求）。
 *
 *  `zoneId` 是 `''`（跟固定区）时给 `null`：后端会把那个班次的固定区回填进来，
 *  这正是抽屉里「跟固定区」那一项的意思。"休"这笔不带班次也不带区。
 */
export function brushPayload(brush) {
  if (!brushReady(brush)) return null
  if (brush.kind === BRUSH_REST) return { is_rest: true }
  const zone = brush.zoneId === '' || brush.zoneId === null || brush.zoneId === undefined
    ? null
    : Number(brush.zoneId)
  return { shift_id: Number(brush.shiftId), zone_id: zone }
}

/** 过去的日子不改写（口径 5）：服务端也会拒（`past_day`），客户端先拦一次 ——
 *  省一次必然失败的请求，也不用让店长读完那句话才知道刷错了地方。 */
export function canPaintOn(businessDate, today) {
  if (!businessDate || !today) return true
  return String(businessDate) >= String(today)
}
