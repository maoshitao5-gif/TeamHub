/**
 * 版本一致性感知 — 窗口激活时触发回调
 * 防抖：5 分钟内同类操作只触发一次
 */
import { useAppStore } from '@/stores/app'

const SCAN_KEY = 'th_last_scan_ts'
const DEBOUNCE_MS = 5 * 60 * 1000

function canTrigger(key) {
  return Date.now() - parseInt(localStorage.getItem(key) || '0') > DEBOUNCE_MS
}

function mark(key) {
  localStorage.setItem(key, String(Date.now()))
}

export function useVersionAwareness() {
  /**
   * 注册窗口激活事件（visibilitychange + focus）。
   * 返回清理函数，在组件 onUnmounted 时调用。
   * @param {Object} callbacks
   * @param {Function} [callbacks.onLocalChanged] - 窗口激活时调用（防抖 5 分钟）
   */
  function registerWindowEvents({ onLocalChanged } = {}) {
    const appStore = useAppStore()

    const handler = async () => {
      if (document.visibilityState !== 'visible') return

      const wsId = appStore.syncStatus?.workspace_id

      if (wsId && canTrigger(SCAN_KEY)) {
        mark(SCAN_KEY)
        onLocalChanged?.()
      }
    }

    document.addEventListener('visibilitychange', handler)
    window.addEventListener('focus', handler)

    return () => {
      document.removeEventListener('visibilitychange', handler)
      window.removeEventListener('focus', handler)
    }
  }

  return { registerWindowEvents }
}
