<template>
  <div class="app-container">
    <!-- 顶部导航栏 -->
    <el-header class="app-header">
      <div class="header-content">
        <div class="logo-section" @click="$router.push('/files')" style="cursor: pointer;">
          <el-icon class="logo-icon"><FolderOpened /></el-icon>
          <h1 class="app-title">团队文件管理系统</h1>
        </div>
        
        <!-- 导航菜单 -->
        <div class="nav-menu">
          <el-menu
            :default-active="activeMenu"
            mode="horizontal"
            class="header-menu"
            @select="handleMenuSelect"
          >
            <el-menu-item index="/files">
              <el-icon><Document /></el-icon>
              <span>文件列表</span>
            </el-menu-item>
            <el-menu-item index="/upload">
              <el-icon><Upload /></el-icon>
              <span>上传文件</span>
            </el-menu-item>
            <el-menu-item index="/tags">
              <el-icon><PriceTag /></el-icon>
              <span>标签管理</span>
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
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { FolderOpened, Document, Upload, PriceTag } from '@element-plus/icons-vue'

const route = useRoute()
const router = useRouter()

// 计算当前激活的菜单项
const activeMenu = computed(() => {
  return route.path
})

// 处理菜单选择
const handleMenuSelect = (index) => {
  router.push(index)
}
</script>

<style>
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial,
    'Noto Sans', sans-serif, 'Apple Color Emoji', 'Segoe UI Emoji', 'Segoe UI Symbol',
    'Noto Color Emoji';
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
  border-bottom: 1px solid #34495e;
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

.user-section {
  margin-left: 24px;
}

.user-info {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #ecf0f1;
  cursor: pointer;
  padding: 8px 16px;
  border-radius: 6px;
  transition: all 0.2s ease;
  font-size: 14px;
  font-weight: 500;
}

.user-info:hover {
  background-color: rgba(255, 255, 255, 0.1);
  color: #ffffff;
}

.dropdown-icon {
  font-size: 12px;
  margin-left: 4px;
}

.logo-section {
  display: flex;
  align-items: center;
  gap: 12px;
  cursor: pointer;
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
  letter-spacing: 0.5px;
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

/* 确保所有菜单项都能直接显示，防止被折叠 */
.header-menu :deep(.el-menu--horizontal) {
  overflow: visible;
  white-space: nowrap;
}

.header-menu :deep(.el-menu-item) {
  display: inline-flex !important;
  visibility: visible !important;
  opacity: 1 !important;
}

/* 确保第三个菜单项（标签管理）也能显示 */
.header-menu :deep(.el-menu-item:nth-child(3)) {
  display: inline-flex !important;
  visibility: visible !important;
  opacity: 1 !important;
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

.app-main {
  flex: 1;
  padding: 32px;
  background: #f5f7fa;
}

.content-wrapper {
  max-width: 1400px;
  margin: 0 auto;
}

/* 响应式设计 */
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
