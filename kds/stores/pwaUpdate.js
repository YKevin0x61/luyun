import { defineStore } from 'pinia'
import { PrintQueueManager } from '../utils/storage.js'
import { createPwaUpdateController } from '../utils/pwaUpdate.js'

let sharedController = null

export function confirmPendingPrintUpdate() {
  const queue = PrintQueueManager.getQueue()
  const failed = queue.filter((job) => job?.status === 'failed').length
  const pending = queue.length - failed
  if (pending === 0 && failed === 0) return Promise.resolve(true)

  if (typeof uni === 'undefined' || typeof uni.showModal !== 'function') {
    return Promise.resolve(false)
  }

  return new Promise((resolve) => {
    uni.showModal({
      title: '更新前确认',
      content: `当前还有 ${pending} 个待打印任务、${failed} 个失败任务。更新会重启页面，任务会保留并在恢复后继续处理。`,
      confirmText: '更新并重启',
      cancelText: '稍后',
      success: (result) => resolve(Boolean(result.confirm)),
      fail: () => resolve(false),
    })
  })
}

function reloadKds() {
  if (globalThis.location?.reload) {
    globalThis.location.reload()
    return
  }
  if (typeof uni !== 'undefined' && typeof uni.reLaunch === 'function') {
    uni.reLaunch({ url: '/pages/index/index' })
  }
}

function getController() {
  if (!sharedController) {
    sharedController = createPwaUpdateController({
      confirmApply: confirmPendingPrintUpdate,
      reload: reloadKds,
    })
  }
  return sharedController
}

export function resetPwaUpdateControllerForTests() {
  sharedController = null
}

export const usePwaUpdateStore = defineStore('pwaUpdate', {
  state: () => ({
    visible: false,
    applying: false,
    error: '',
    initialized: false,
  }),
  actions: {
    initialize() {
      if (this.initialized) return
      this.initialized = true
      const controller = getController()
      controller.subscribe((state) => {
        this.visible = state.visible
        this.applying = state.applying
        this.error = state.error
      })
      void controller.initialize()
    },
    dismiss() {
      getController().dismiss()
    },
    apply() {
      return getController().applyUpdate()
    },
  },
})
