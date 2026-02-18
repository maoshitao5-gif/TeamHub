/**
 * 全局应用状态
 * 管理文件库信息、待整理数量、设置等共享状态
 */
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import request from '@/api/request'

export const useAppStore = defineStore('app', () => {
  // 文件库信息
  const libraryPath = ref(null)
  const libraryInitialized = ref(false)

  // 待整理数量（导航栏角标用）
  const pendingCount = ref(0)

  // 设置
  const settings = ref({
    default_storage_mode: 'move',
    max_versions: 0,
    max_version_age_days: 0,
    trash_auto_clean_days: 30,
  })

  // 是否需要显示初始化向导
  const needSetup = computed(() => !libraryInitialized.value)

  // 获取文件库信息
  async function fetchLibraryInfo() {
    try {
      const data = await request.get('/api/settings/library')
      libraryPath.value = data.path
      libraryInitialized.value = data.initialized
      pendingCount.value = data.pending_count
    } catch (e) {
      console.error('Failed to fetch library info:', e)
    }
  }

  // 获取待整理数量
  async function fetchPendingCount() {
    try {
      const data = await request.get('/api/documents/pending/count')
      pendingCount.value = data.count
    } catch (e) {
      console.error('Failed to fetch pending count:', e)
    }
  }

  // 获取设置
  async function fetchSettings() {
    try {
      const data = await request.get('/api/settings/config')
      settings.value = data
      libraryPath.value = data.library_path
      libraryInitialized.value = !!data.library_path
    } catch (e) {
      console.error('Failed to fetch settings:', e)
    }
  }

  // 初始化文件库
  async function setupLibrary(path) {
    const data = await request.post('/api/settings/library', { path })
    libraryPath.value = data.path
    libraryInitialized.value = true
    return data
  }

  // 更新设置
  async function updateSettings(updates) {
    await request.put('/api/settings/config', updates)
    Object.assign(settings.value, updates)
  }

  return {
    libraryPath,
    libraryInitialized,
    pendingCount,
    settings,
    needSetup,
    fetchLibraryInfo,
    fetchPendingCount,
    fetchSettings,
    setupLibrary,
    updateSettings,
  }
})
