// 从 public/sales-report.html 移植的文字版报表构建逻辑（模板C：按档口→子分类分组）。

export function resolveSubCategory(semiItem) {
  const direct = String(semiItem?.sub_category || '').trim()
  if (direct) return direct
  return String(semiItem?.category || '').trim()
}

export function normalizeDishName(name) {
  // 前缀可以叠加（(外卖)(普通)xxx），循环剥到稳定，与后端 normalize_dish_name 一致（R39）。
  let text = String(name ?? '')
  for (let i = 0; i < 8; i += 1) {
    const before = text
    text = text
      .replace(/\(-\)$/, '')
      .replace(/^\(外卖\)/, '')
      .replace(/^\(普通\)/, '')
      .replace(/\(\d+只\)$/, '')
      .replace(/\(\d+个\)$/, '')
      .replace(/\(\d+条\)$/, '')
      .replace(/^\(小份\)/, '')
      .replace(/^\(中份\)/, '')
      .replace(/^\(大份\)/, '')
      .replace(/^\(半份\)/, '')
    if (text === before) break
  }
  return text.trim()
}

export function buildSemiUsageLinesTemplateC(semiFinished) {
  const lines = []
  if (!semiFinished || !semiFinished.length) return lines
  semiFinished.forEach((positionGroup) => {
    lines.push(`【${positionGroup.position}】`)
    const grouped = {}
    positionGroup.items.forEach((item) => {
      const label = resolveSubCategory(item) || '未分类'
      if (!grouped[label]) grouped[label] = []
      grouped[label].push(item)
    })
    const weight = { 半成品: 1, 原料: 2, 未分类: 99 }
    const sortedCats = Object.keys(grouped).sort((a, b) => {
      const wa = weight[a] || 50
      const wb = weight[b] || 50
      if (wa !== wb) return wa - wb
      return a.localeCompare(b, 'zh-Hans-CN')
    })
    sortedCats.forEach((cat, ci) => {
      const items = [...grouped[cat]].sort((a, b) => Number(b.qty || 0) - Number(a.qty || 0))
      const isLastCat = ci === sortedCats.length - 1
      const catPrefix = isLastCat ? '└─' : '├─'
      const indent = isLastCat ? '   ' : '│  '
      lines.push(` ${catPrefix} ${cat}`)
      items.forEach((item, ii) => {
        const isLastItem = ii === items.length - 1
        const itemPrefix = isLastItem ? '└─' : '├─'
        lines.push(` ${indent}${itemPrefix} ${item.semi_name} × ${item.qty}${item.unit}`)
      })
    })
    lines.push('')
  })
  if (lines[lines.length - 1] === '') lines.pop()
  return lines
}

export function buildTextReport(reportData, fixedDishes) {
  if (!reportData) return ''
  const d = reportData
  const s = d.summary
  const lines = []
  const rangeText = d.date_range.start === d.date_range.end ? d.date_range.start : `${d.date_range.start} ~ ${d.date_range.end}`
  lines.push(`【销售报表】${rangeText}`)
  if ((!d.dish_sales || !d.dish_sales.length) && (!d.semi_finished || !d.semi_finished.length)) {
    return `【销售报表】${rangeText}\n本期无销售数据`
  }
  // 与页面同口径：订单数用账单数；规则覆盖显示 87/109（80%）而不是单个数字（R12/R44）。
  const bills = s.total_bills ?? s.total_orders
  const coverage = s.unique_dishes ? Math.round((s.covered_rules / s.unique_dishes) * 100) : 0
  lines.push(`订单数：${bills}  菜件总数：${s.total_dishes}  菜品数：${s.unique_dishes}  规则覆盖：${s.covered_rules}/${s.unique_dishes}（${coverage}%）`)
  lines.push('')
  lines.push('【菜品销量】')
  if (fixedDishes && fixedDishes.length) {
    const salesMap = {}
    let total = 0
    ;(d.dish_sales || []).forEach((item) => {
      const key = normalizeDishName(item.dish_name)
      salesMap[key] = (salesMap[key] || 0) + Number(item.qty || 0)
    })
    fixedDishes.forEach((fd, i) => {
      const qty = salesMap[normalizeDishName(fd.dish_name)] || 0
      total += Number(qty || 0)
      lines.push(`${i + 1}. ${fd.dish_name} ${qty}份`)
    })
    lines.push(`总计 ${total}份`)
  } else {
    ;(d.dish_sales || []).forEach((item, i) => {
      lines.push(`${i + 1}. ${item.dish_name} ${item.qty}份`)
    })
  }
  if (d.semi_finished && d.semi_finished.length) {
    lines.push('')
    lines.push('【半成品用量】')
    lines.push(...buildSemiUsageLinesTemplateC(d.semi_finished))
  }
  return lines.join('\n')
}

export function buildSemiReportText(reportData) {
  if (!reportData) return ''
  const rangeText =
    reportData.date_range.start === reportData.date_range.end
      ? reportData.date_range.start
      : `${reportData.date_range.start} ~ ${reportData.date_range.end}`
  const lines = [`【半成品用量】${rangeText}`]
  const semiLines = buildSemiUsageLinesTemplateC(reportData.semi_finished || [])
  if (semiLines.length) lines.push(...semiLines)
  else lines.push('暂无半成品用量')
  return lines.join('\n')
}

export function downloadTextFile(filename, content) {
  const blob = new Blob(['\ufeff' + content], { type: 'text/plain;charset=utf-8' })
  const link = document.createElement('a')
  link.href = URL.createObjectURL(blob)
  link.download = filename
  link.click()
  URL.revokeObjectURL(link.href)
}
