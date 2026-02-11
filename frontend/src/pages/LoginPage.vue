<template>
  <div class="login-container">
    <div class="login-card">
      <div class="login-header">
        <el-icon class="logo-icon"><FolderOpened /></el-icon>
        <h1 class="login-title">团队文件管理系统</h1>
        <p class="login-subtitle">请输入账号密码登录</p>
      </div>

      <el-form
        ref="loginFormRef"
        :model="loginForm"
        :rules="loginRules"
        class="login-form"
        @submit.prevent="handleLogin"
      >
        <el-form-item prop="username">
          <el-input
            v-model="loginForm.username"
            placeholder="请输入用户名"
            size="large"
            :prefix-icon="User"
            clearable
            @keyup.enter="handleLogin"
          />
        </el-form-item>

        <el-form-item prop="password">
          <el-input
            v-model="loginForm.password"
            type="password"
            placeholder="请输入密码"
            size="large"
            :prefix-icon="Lock"
            show-password
            clearable
            @keyup.enter="handleLogin"
          />
        </el-form-item>

        <el-form-item>
          <el-button
            type="primary"
            size="large"
            :loading="loading"
            @click="handleLogin"
            class="login-button"
            native-type="submit"
          >
            {{ loading ? '登录中...' : '登录' }}
          </el-button>
        </el-form-item>
      </el-form>

      <div class="login-tip">
        <el-text type="info" size="small">
          默认账号：admin / admin123
        </el-text>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { FolderOpened, User, Lock } from '@element-plus/icons-vue'
import { login, getCurrentUser, setUserInfo } from '@/api/auth'

const router = useRouter()

// 表单引用
const loginFormRef = ref(null)

// 登录状态
const loading = ref(false)

// 登录表单数据
const loginForm = reactive({
  username: '',
  password: ''
})

// 表单验证规则
const loginRules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' }
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 3, message: '密码长度至少3位', trigger: 'blur' }
  ]
}

// 处理登录
const handleLogin = async () => {
  if (!loginFormRef.value) return
  
  // 表单验证
  await loginFormRef.value.validate(async (valid) => {
    if (!valid) {
      return false
    }

    loading.value = true
    try {
      const result = await login(loginForm.username, loginForm.password)
      
      // 保存 token 到 localStorage
      localStorage.setItem('access_token', result.access_token)
      localStorage.setItem('username', result.username)
      
      // 获取用户详细信息（包含is_admin）
      try {
        const userInfo = await getCurrentUser()
        setUserInfo(userInfo)
      } catch (error) {
        console.warn('获取用户信息失败:', error)
        // 如果获取失败，至少保存用户名
        setUserInfo({ username: result.username, is_admin: false })
      }
      
      ElMessage.success('登录成功')
      
      // 跳转到文件列表页
      router.push('/files')
    } catch (error) {
      console.error('登录失败:', error)
      // 错误信息已在 request.js 的拦截器中显示
    } finally {
      loading.value = false
    }
  })
}
</script>

<style scoped>
.login-container {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f5f7fa;
  padding: 20px;
}

.login-card {
  width: 100%;
  max-width: 420px;
  background: #ffffff;
  border-radius: 8px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
  padding: 48px 40px;
  border: 1px solid #e4e7ed;
}

.login-header {
  text-align: center;
  margin-bottom: 40px;
}

.logo-icon {
  font-size: 48px;
  color: #2c3e50;
  margin-bottom: 20px;
}

.login-title {
  font-size: 24px;
  font-weight: 600;
  color: #2c3e50;
  margin: 0 0 12px 0;
  letter-spacing: 0.5px;
}

.login-subtitle {
  font-size: 14px;
  color: #7f8c8d;
  margin: 0;
  font-weight: 400;
}

.login-form {
  margin-top: 32px;
}

.login-form :deep(.el-input__wrapper) {
  box-shadow: 0 0 0 1px #e4e7ed inset;
  border-radius: 6px;
}

.login-form :deep(.el-input__wrapper:hover) {
  box-shadow: 0 0 0 1px #c0c4cc inset;
}

.login-form :deep(.el-input.is-focus .el-input__wrapper) {
  box-shadow: 0 0 0 1px #1e88e5 inset;
}

.login-button {
  width: 100%;
  margin-top: 12px;
  height: 44px;
  font-size: 15px;
  font-weight: 500;
  background: #2c3e50;
  border: none;
  border-radius: 6px;
  transition: all 0.2s ease;
}

.login-button:hover {
  background: #34495e;
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(44, 62, 80, 0.2);
}

.login-button:active {
  transform: translateY(0);
}

.login-tip {
  margin-top: 28px;
  text-align: center;
  padding-top: 20px;
  border-top: 1px solid #e4e7ed;
}

.login-tip :deep(.el-text) {
  color: #7f8c8d;
  font-size: 13px;
}

/* 响应式设计 */
@media (max-width: 480px) {
  .login-card {
    padding: 32px 24px;
  }

  .login-title {
    font-size: 20px;
  }

  .logo-icon {
    font-size: 40px;
  }
}
</style>
