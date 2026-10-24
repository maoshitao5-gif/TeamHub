<template>
  <div class="login-page">
    <div class="login-card">
      <div class="login-logo">
        <el-icon :size="40"><FolderOpened /></el-icon>
        <h1>TeamHub</h1>
        <p class="subtitle">账号由管理员预置</p>
      </div>

      <el-alert type="info" :closable="false" show-icon>
        <template #title>
          MVP 阶段不开放自助注册。请联系团队管理员创建账号后,直接登录使用。
        </template>
      </el-alert>

      <div class="login-footer">
        已有账号？
        <el-link type="primary" @click="$router.push('/login')">前往登录</el-link>
      </div>
    </div>
  </div>
</template>

<script setup>
import { FolderOpened } from '@element-plus/icons-vue'
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #1e2d3d 0%, #2c3e50 100%);
}

.login-card {
  background: #fff;
  border-radius: 16px;
  padding: 48px 40px 36px;
  width: 420px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.25);
}

.login-logo {
  text-align: center;
  margin-bottom: 36px;
}

.login-logo .el-icon {
  color: #4096ff;
  margin-bottom: 12px;
}

.login-logo h1 {
  font-size: 28px;
  font-weight: 700;
  color: #1e2d3d;
  margin: 0 0 8px;
}

.subtitle {
  color: #909399;
  font-size: 14px;
  margin: 0;
}

.login-footer {
  text-align: center;
  margin-top: 24px;
  font-size: 14px;
  color: #909399;
}
</style>
