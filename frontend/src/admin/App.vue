<template>
  <router-view v-if="isLoginPage" />
  <el-container v-else class="admin-layout">
    <!-- 左侧导航 -->
    <el-aside width="200px" class="sidebar">
      <div class="sidebar-logo">
        <span class="logo-icon">⚙</span>
        <span class="logo-text">管理后台</span>
      </div>
      <el-menu
        :default-active="activeMenu"
        router
        class="sidebar-menu"
        background-color="#1e2d3d"
        text-color="#bfc9d3"
        active-text-color="#60a5fa"
      >
        <el-menu-item index="/dashboard">
          <el-icon><DataLine /></el-icon>
          <span>仪表板</span>
        </el-menu-item>
        <el-menu-item index="/users">
          <el-icon><User /></el-icon>
          <span>用户管理</span>
        </el-menu-item>
        <el-menu-item index="/teams">
          <el-icon><OfficeBuilding /></el-icon>
          <span>团队管理</span>
        </el-menu-item>
      </el-menu>
      <div class="sidebar-footer">
        <el-button type="danger" text @click="handleLogout">退出登录</el-button>
      </div>
    </el-aside>

    <!-- 右侧内容 -->
    <el-container>
      <el-header class="topbar">
        <span class="topbar-title">TeamHub 管理后台</span>
        <span class="topbar-user">{{ currentUser?.display_name || currentUser?.email }}</span>
      </el-header>
      <el-main class="main-content">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { DataLine, User, OfficeBuilding } from '@element-plus/icons-vue'

const route = useRoute()
const router = useRouter()

const isLoginPage = computed(() => route.path === '/login')
const activeMenu = computed(() => route.path)
const currentUser = computed(() => {
  try {
    return JSON.parse(localStorage.getItem('admin_user') || 'null')
  } catch {
    return null
  }
})

async function handleLogout() {
  await ElMessageBox.confirm('确认退出登录？', '退出', { type: 'warning' })
  localStorage.removeItem('admin_token')
  localStorage.removeItem('admin_user')
  router.push('/login')
}
</script>

<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
</style>

<style scoped>
.admin-layout {
  height: 100vh;
}
.sidebar {
  background: #1e2d3d;
  display: flex;
  flex-direction: column;
}
.sidebar-logo {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 20px 16px;
  border-bottom: 1px solid #2c3e50;
}
.logo-icon {
  font-size: 22px;
}
.logo-text {
  color: #fff;
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 1px;
}
.sidebar-menu {
  border: none;
  flex: 1;
}
.sidebar-footer {
  padding: 16px;
  border-top: 1px solid #2c3e50;
  text-align: center;
}
.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #fff;
  border-bottom: 1px solid #e4e7ed;
  padding: 0 24px;
  height: 56px;
}
.topbar-title {
  font-size: 16px;
  font-weight: 600;
  color: #303133;
}
.topbar-user {
  font-size: 14px;
  color: #606266;
}
.main-content {
  background: #f4f6fa;
  padding: 24px;
}
</style>
