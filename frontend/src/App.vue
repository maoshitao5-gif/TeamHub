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
            <el-menu-item v-if="isAdminUser" index="/admin">
              <el-icon><Setting /></el-icon>
              <span>管理后台</span>
            </el-menu-item>
          </el-menu>
        </div>
        
        <!-- 用户信息 -->
        <div class="user-section">
          <el-dropdown @command="handleUserCommand">
            <span class="user-info">
              <el-icon><User /></el-icon>
              <span>{{ username || '用户' }}</span>
              <el-icon class="dropdown-icon"><ArrowDown /></el-icon>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="logout">
                  <el-icon><SwitchButton /></el-icon>
                  退出登录
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
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
import { computed, ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { FolderOpened, Document, Upload, PriceTag, User, ArrowDown, SwitchButton, Setting } from '@element-plus/icons-vue'
import { getUsername, logout, isAdmin } from '@/api/auth'

const route = useRoute()
const router = useRouter()

// 用户名
const username = ref('')
// 是否为管理员
const isAdminUser = ref(false)

// 计算当前激活的菜单项
const activeMenu = computed(() => {
  return route.path
})

// 处理菜单选择
const handleMenuSelect = (index) => {
  router.push(index)
}

// 处理用户下拉菜单命令
const handleUserCommand = (command) => {
  if (command === 'logout') {
    ElMessageBox.confirm(
      '确定要退出登录吗？',
      '确认退出',
      {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'warning',
      }
    ).then(() => {
      logout()
      ElMessage.success('已退出登录')
      router.push('/login')
    }).catch(() => {
      // 用户取消
    })
  }
}

// 加载用户信息
onMounted(() => {
  username.value = getUsername() || ''
  isAdminUser.value = isAdmin()
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
