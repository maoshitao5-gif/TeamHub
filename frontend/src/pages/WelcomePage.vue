<template>
  <div class="welcome-container">
    <div class="welcome-card">
      <div class="welcome-header">
        <el-icon class="welcome-icon"><FolderOpened /></el-icon>
        <div class="welcome-title">
          <p class="welcome-subtitle">欢迎回来</p>
          <h1 class="app-name">TeamHub</h1>
        </div>
      </div>

      <div class="library-info">
        <p class="field-label">文件库路径</p>
        <div class="path-display">
          <el-tooltip :content="appStore.libraryPath" placement="top" :show-after="500">
            <span class="path-text">{{ appStore.libraryPath }}</span>
          </el-tooltip>
        </div>
      </div>

      <div class="stats-row" v-if="statsLoaded">
        <div class="stat-item">
          <el-icon><Document /></el-icon>
          <span>共 {{ totalDocs }} 个文档</span>
        </div>
        <div class="stat-item">
          <el-icon><Coin /></el-icon>
          <span>{{ formatSize(totalSize) }}</span>
        </div>
        <div class="stat-item pending" v-if="appStore.pendingCount > 0">
          <el-icon><Clock /></el-icon>
          <span>{{ appStore.pendingCount }} 个待整理</span>
        </div>
      </div>

      <el-button
        type="primary"
        size="large"
        class="enter-btn"
        @click="$emit('enter')"
      >
        打开文件库
      </el-button>

      <el-button
        link
        class="change-btn"
        @click="$emit('change')"
      >
        更换文件库
      </el-button>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useAppStore } from '@/stores/app'
import { FolderOpened, Document, Coin, Clock } from '@element-plus/icons-vue'
import request from '@/api/request'

defineEmits(['enter', 'change'])

const appStore = useAppStore()
const totalDocs = ref(0)
const totalSize = ref(0)
const statsLoaded = ref(false)

const formatSize = (bytes) => {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let size = bytes
  let unitIndex = 0
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024
    unitIndex++
  }
  return `${size.toFixed(1)} ${units[unitIndex]}`
}

onMounted(async () => {
  try {
    const data = await request.get('/api/settings/library')
    totalDocs.value = data.total_documents || 0
    totalSize.value = data.total_size || 0
    statsLoaded.value = true
  } catch (e) {
    console.error('Failed to fetch library stats:', e)
    statsLoaded.value = true
  }
})
</script>

<style scoped>
.welcome-container {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #2c3e50 0%, #34495e 100%);
  padding: 20px;
}

.welcome-card {
  background: #ffffff;
  border-radius: 16px;
  padding: 48px;
  max-width: 480px;
  width: 100%;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.2);
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
}

.welcome-header {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 36px;
}

.welcome-icon {
  font-size: 52px;
  color: #2c3e50;
  flex-shrink: 0;
}

.welcome-title {
  text-align: left;
}

.welcome-subtitle {
  font-size: 14px;
  color: #7f8c8d;
  margin-bottom: 4px;
}

.app-name {
  font-size: 28px;
  font-weight: 700;
  color: #2c3e50;
  margin: 0;
}

.library-info {
  width: 100%;
  margin-bottom: 24px;
  text-align: left;
}

.field-label {
  font-size: 12px;
  color: #95a5a6;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 6px;
}

.path-display {
  background: #f8f9fa;
  border-radius: 8px;
  padding: 10px 14px;
  border: 1px solid #e9ecef;
}

.path-text {
  font-size: 13px;
  color: #2c3e50;
  font-family: 'Courier New', Courier, monospace;
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  direction: rtl;
  text-align: left;
}

.stats-row {
  display: flex;
  gap: 20px;
  margin-bottom: 32px;
  flex-wrap: wrap;
  justify-content: center;
}

.stat-item {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  color: #5d6d7e;
}

.stat-item .el-icon {
  font-size: 16px;
  color: #7f8c8d;
}

.stat-item.pending .el-icon {
  color: #e67e22;
}

.stat-item.pending {
  color: #e67e22;
}

.enter-btn {
  width: 100%;
  height: 48px;
  font-size: 16px;
  margin-bottom: 16px;
}

.change-btn {
  color: #95a5a6;
  font-size: 14px;
}

.change-btn:hover {
  color: #2c3e50;
}
</style>
