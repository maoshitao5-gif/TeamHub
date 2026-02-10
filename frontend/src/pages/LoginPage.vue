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
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  padding: 20px;
}

.login-card {
  width: 100%;
  max-width: 400px;
  background: white;
  border-radius: 12px;
  box-shadow: 0 10px 40px rgba(0, 0, 0, 0.2);
  padding: 40px;
}

.login-header {
  text-align: center;
  margin-bottom: 32px;
}

.logo-icon {
  font-size: 48px;
  color: #409EFF;
  margin-bottom: 16px;
}

.login-title {
  font-size: 24px;
  font-weight: 600;
  color: #303133;
  margin: 0 0 8px 0;
}

.login-subtitle {
  font-size: 14px;
  color: #909399;
  margin: 0;
}

.login-form {
  margin-top: 32px;
}

.login-button {
  width: 100%;
  margin-top: 8px;
}

.login-tip {
  margin-top: 24px;
  text-align: center;
}

/* 响应式设计 */
@media (max-width: 480px) {
  .login-card {
    padding: 24px;
  }

  .login-title {
    font-size: 20px;
  }
}
</style>
