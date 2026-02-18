<template>
  <div class="setup-container">
    <div class="setup-card">
      <div class="setup-header">
        <el-icon class="setup-icon"><FolderOpened /></el-icon>
        <h1>欢迎使用 TeamHub</h1>
        <p>选择一个目录作为你的文件库，所有整理后的文件都将存放在这里。</p>
      </div>

      <div class="setup-form">
        <div class="path-input">
          <el-input
            v-model="libraryPath"
            placeholder="选择或输入文件库路径"
            size="large"
          >
            <template #append>
              <el-button @click="selectDirectory">
                <el-icon><FolderOpened /></el-icon>
                浏览
              </el-button>
            </template>
          </el-input>
        </div>

        <div class="setup-tips">
          <p>文件库是你整理后的文件存放位置：</p>
          <ul>
            <li>即使不打开软件，也能用文件管理器找到文件</li>
            <li>文件以原始文件名保存，不会变成乱码</li>
            <li>软件数据保存在文件库内的隐藏目录中</li>
          </ul>
        </div>

        <el-button
          type="primary"
          size="large"
          :loading="loading"
          :disabled="!libraryPath"
          class="setup-btn"
          @click="handleSetup"
        >
          开始使用
        </el-button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAppStore } from '@/stores/app'
import { ElMessage } from 'element-plus'
import { FolderOpened } from '@element-plus/icons-vue'

const router = useRouter()
const appStore = useAppStore()
const libraryPath = ref('')
const loading = ref(false)

const selectDirectory = async () => {
  // Electron 环境使用 IPC 选择目录
  if (window.electron?.selectDirectory) {
    const path = await window.electron.selectDirectory()
    if (path) {
      libraryPath.value = path
    }
  } else {
    // 浏览器开发环境提示手动输入
    ElMessage.info('请手动输入文件库路径（Electron 环境下可浏览选择）')
  }
}

const handleSetup = async () => {
  if (!libraryPath.value) return
  loading.value = true
  try {
    await appStore.setupLibrary(libraryPath.value)
    ElMessage.success('文件库初始化成功')
    router.push('/library')
  } catch (e) {
    ElMessage.error(e.message || '初始化失败')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.setup-container {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #2c3e50 0%, #34495e 100%);
  padding: 20px;
}

.setup-card {
  background: #ffffff;
  border-radius: 16px;
  padding: 48px;
  max-width: 560px;
  width: 100%;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.2);
}

.setup-header {
  text-align: center;
  margin-bottom: 36px;
}

.setup-icon {
  font-size: 56px;
  color: #2c3e50;
  margin-bottom: 16px;
}

.setup-header h1 {
  font-size: 28px;
  font-weight: 600;
  color: #2c3e50;
  margin-bottom: 12px;
}

.setup-header p {
  color: #7f8c8d;
  font-size: 15px;
  line-height: 1.6;
}

.path-input {
  margin-bottom: 24px;
}

.setup-tips {
  background: #f8f9fa;
  border-radius: 8px;
  padding: 16px 20px;
  margin-bottom: 32px;
}

.setup-tips p {
  font-size: 14px;
  color: #2c3e50;
  margin-bottom: 8px;
  font-weight: 500;
}

.setup-tips ul {
  list-style: none;
  padding: 0;
}

.setup-tips li {
  font-size: 13px;
  color: #7f8c8d;
  padding: 4px 0;
  padding-left: 16px;
  position: relative;
}

.setup-tips li::before {
  content: '·';
  position: absolute;
  left: 0;
  color: #1e88e5;
  font-weight: bold;
}

.setup-btn {
  width: 100%;
  height: 48px;
  font-size: 16px;
}
</style>
