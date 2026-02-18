<template>
  <div class="app-container">
    <!-- 初始化向导 -->
    <template v-if="appStore.needSetup && $route.name !== 'Setup'">
      <router-view v-if="$route.name === 'Setup'" />
      <div v-else class="setup-redirect">
        <SetupPage />
      </div>
    </template>

    <!-- 正常布局 -->
    <template v-else-if="!appStore.needSetup">
      <!-- 顶部导航栏 -->
      <el-header class="app-header">
        <div class="header-content">
          <div class="logo-section" @click="$router.push('/library')" style="cursor: pointer;">
            <el-icon class="logo-icon"><FolderOpened /></el-icon>
            <h1 class="app-title">TeamHub</h1>
          </div>

          <!-- 导航菜单 -->
          <div class="nav-menu">
            <el-menu
              :default-active="activeMenu"
              mode="horizontal"
              class="header-menu"
              @select="handleMenuSelect"
            >
              <el-menu-item index="/library">
                <el-icon><Folder /></el-icon>
                <span>文档库</span>
              </el-menu-item>
              <el-menu-item index="/pending">
                <el-icon><Download /></el-icon>
                <span>待整理</span>
                <el-badge v-if="appStore.pendingCount > 0" :value="appStore.pendingCount" class="nav-badge" />
              </el-menu-item>
              <el-menu-item index="/tags">
                <el-icon><PriceTag /></el-icon>
                <span>标签管理</span>
              </el-menu-item>
              <el-menu-item index="/trash">
                <el-icon><Delete /></el-icon>
                <span>回收站</span>
              </el-menu-item>
              <el-menu-item index="/settings">
                <el-icon><Setting /></el-icon>
                <span>设置</span>
              </el-menu-item>
            </el-menu>
          </div>
        </div>
      </el-header>

      <!-- 主内容区 -->
      <el-main class="app-main">
        <div class="content-wrapper">
          <router-view />
        </div>
      </el-main>
    </template>

    <!-- Setup 页面单独渲染 -->
    <template v-else>
      <router-view />
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAppStore } from '@/stores/app'
import { FolderOpened, Folder, Download, PriceTag, Delete, Setting } from '@element-plus/icons-vue'
import SetupPage from '@/pages/SetupPage.vue'

const route = useRoute()
const router = useRouter()
const appStore = useAppStore()

const activeMenu = computed(() => route.path)

const handleMenuSelect = (index) => {
  router.push(index)
}

onMounted(async () => {
  console.log('[App] window.electron:', window.electron)
  console.log('[App] isElectron:', window.electron?.isElectron)
  await appStore.fetchLibraryInfo()
  if (appStore.libraryInitialized) {
    appStore.fetchPendingCount()
  }
})
</script>

<style>
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial,
    'Noto Sans', sans-serif;
  background: #f5f7fa;
  min-height: 100vh;
  color: #2c3e50;
}

#app {
  min-height: 100vh;
}
</style>

<style scoped>
.app-container {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  background: #f5f7fa;
}

.app-header {
  background: #2c3e50;
  color: #ffffff;
  padding: 0;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
}

.header-content {
  max-width: 1400px;
  margin: 0 auto;
  padding: 0 32px;
  height: 64px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.logo-section {
  display: flex;
  align-items: center;
  gap: 12px;
  transition: opacity 0.2s;
}

.logo-section:hover {
  opacity: 0.9;
}

.logo-icon {
  font-size: 28px;
  color: #ecf0f1;
}

.app-title {
  font-size: 20px;
  font-weight: 600;
  color: #ffffff;
  margin: 0;
}

.nav-menu {
  display: flex;
  align-items: center;
  flex: 1;
  justify-content: center;
}

.header-menu {
  background: transparent;
  border-bottom: none;
}

.header-menu .el-menu-item {
  color: rgba(255, 255, 255, 0.85);
  border-bottom: 2px solid transparent;
  padding: 0 24px;
  height: 64px;
  line-height: 64px;
  font-size: 14px;
  font-weight: 500;
  transition: all 0.2s ease;
}

.header-menu .el-menu-item:hover {
  background: rgba(255, 255, 255, 0.08);
  color: #ffffff;
}

.header-menu .el-menu-item.is-active {
  color: #ffffff;
  border-bottom-color: #1e88e5;
  background: rgba(255, 255, 255, 0.05);
}

.nav-badge {
  margin-left: 6px;
}

.nav-badge :deep(.el-badge__content) {
  font-size: 11px;
}

.app-main {
  flex: 1;
  padding: 32px;
  background: #f5f7fa;
}

.content-wrapper {
  max-width: 1400px;
  margin: 0 auto;
}

.setup-redirect {
  min-height: 100vh;
}

@media (max-width: 768px) {
  .app-main {
    padding: 20px;
  }
  .app-title {
    font-size: 18px;
  }
  .header-content {
    padding: 0 16px;
  }
  .header-menu .el-menu-item {
    padding: 0 12px;
    font-size: 13px;
  }
}
</style>
