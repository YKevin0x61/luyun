/**
 * 厨房出餐小票打印（一菜一纸，独立切纸）
 */

import { PrinterSettingsManager } from './storage.js'
import { TimeCalculator } from './timeCalculator.js'
import {
  isPrinterPlatformSupported,
  ensurePrinterConnected,
  printText,
  printNewLine,
  cutPaper,
  getPrintAlign,
  getFontSize
} from './bluetoothPrinter.js'

function formatTicketTime(time) {
  if (!time) {
    return '-'
  }
  return TimeCalculator.formatTime(time, 'HH:mm')
}

function normalizeTableNumber(tableNumber) {
  if (tableNumber === null || tableNumber === undefined || tableNumber === '') {
    return '-'
  }
  const text = String(tableNumber).trim()
  return text.endsWith('桌') ? text : `${text}桌`
}

function normalizeNotes(notes) {
  if (notes === null || notes === undefined) {
    return ''
  }
  return String(notes).trim()
}

/**
 * 打印单道菜出餐小票
 * @param {Object} ticket
 * @param {string|number} ticket.tableNumber 桌号
 * @param {string} ticket.dishName 菜品名
 * @param {string|Date} ticket.orderTime 下单时间
 * @param {string|Date} ticket.readyTime 出餐时间
 * @param {string} ticket.notes 备注
 */
export async function printDishTicket(ticket) {
  if (!isPrinterPlatformSupported()) {
    return { success: false, skipped: true, message: 'H5 不支持蓝牙打印' }
  }

  if (!PrinterSettingsManager.isPrintEnabled()) {
    return { success: false, skipped: true, message: '未启用蓝牙打印' }
  }

  const PrintAlign = getPrintAlign()
  const FontSize = getFontSize()
  if (!PrintAlign || !FontSize) {
    throw new Error('蓝牙打印插件未安装')
  }

  await ensurePrinterConnected()

  const tableText = normalizeTableNumber(ticket.tableNumber)
  const dishName = ticket.dishName || '未知菜品'
  const orderTimeText = formatTicketTime(ticket.orderTime)
  const readyTimeText = formatTicketTime(ticket.readyTime)
  const notesText = normalizeNotes(ticket.notes)

  try {
    printText(tableText, PrintAlign.CENTER, FontSize.EXTRA_LARGE)
    printNewLine(1)
    printText(dishName, PrintAlign.CENTER, FontSize.EXTRA_LARGE)
    printNewLine(2)
    printText(`下单: ${orderTimeText}`, PrintAlign.LEFT, FontSize.NORMAL)
    printNewLine(1)
    printText(`出餐: ${readyTimeText}`, PrintAlign.LEFT, FontSize.NORMAL)
    if (notesText) {
      printNewLine(1)
      printText(`备注: ${notesText}`, PrintAlign.LEFT, FontSize.NORMAL)
    }
    printNewLine(3)
    cutPaper()
  } catch (error) {
    throw new Error(error?.message || String(error) || '发送打印数据失败')
  }

  return { success: true, skipped: false, message: '打印成功' }
}

/**
 * 设置页测试打印
 */
export async function printTestTicket() {
  const now = new Date()
  return printDishTicket({
    tableNumber: '测试',
    dishName: '测试菜品',
    orderTime: now,
    readyTime: now,
    notes: '少辣，不要葱'
  })
}

export { isPrinterPlatformSupported }
