<template>
  <div class="login-page">
    <div class="login-card">
      <div class="login-logo">
        <el-icon :size="40"><FolderOpened /></el-icon>
        <h1>TeamHub</h1>
        <p class="subtitle">创建您的账号</p>
      </div>

      <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @submit.prevent="handleRegister">
        <el-form-item label="昵称" prop="displayName">
          <el-input
            v-model="form.displayName"
            placeholder="您的显示名称"
            size="large"
            :prefix-icon="User"
          />
        </el-form-item>

        <el-form-item label="邮箱" prop="email">
          <el-input
            v-model="form.email"
            type="email"
            placeholder="your@email.com"
            size="large"
            :prefix-icon="Message"
          />
        </el-form-item>

        <el-form-item label="密码" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            placeholder="至少 8 位"
            size="large"
            :prefix-icon="Lock"
            show-password
          />
        </el-form-item>

        <el-form-item label="确认密码" prop="confirmPassword">
          <el-input
            v-model="form.confirmPassword"
            type="password"
            placeholder="再次输入密码"
            size="large"
            :prefix-icon="Lock"
            show-password
            @keyup.enter="handleRegister"
          />
        </el-form-item>

        <el-button
          type="primary"
          size="large"
          class="login-btn"
          :loading="loading"
          @click="handleRegister"
        >
          注册
        </el-button>
      </el-form>

      <div class="login-footer">
        已有账号？
        <el-link type="primary" @click="$router.push('/login')">立即登录</el-link>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { FolderOpened, User, Message, Lock } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const authStore = useAuthStore()

const formRef = ref(null)
const loading = ref(false)

const form = reactive({ displayName: '', email: '', password: '', confirmPassword: '' })
const rules = {
  displayName: [{ required: true, min: 1, max: 100, message: '请输入昵称', trigger: 'blur' }],
  email: [{ required: true, type: 'email', message: '请输入有效的邮箱地址', trigger: 'blur' }],
  password: [{ required: true, min: 8, message: '密码至少 8 位', trigger: 'blur' }],
  confirmPassword: [
    { required: true, message: '请确认密码', trigger: 'blur' },
    {
      validator: (rule, value, cb) => {
        if (value !== form.password) cb('两次输入的密码不一致')
        else cb()
      },
      trigger: 'blur'
    }
  ],
}

async function handleRegister() {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return

  loading.value = true
  try {
    await authStore.register(form.email, form.password, form.displayName)
    ElMessage.success('注册成功，欢迎加入 TeamHub！')
    router.push('/')
  } catch (e) {
    // 错误已由 cloud.js 拦截器显示
  } finally {
    loading.value = false
  }
}
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

.login-btn {
  width: 100%;
  margin-top: 8px;
  height: 44px;
  font-size: 16px;
}

.login-footer {
  text-align: center;
  margin-top: 24px;
  font-size: 14px;
  color: #909399;
}
</style>
