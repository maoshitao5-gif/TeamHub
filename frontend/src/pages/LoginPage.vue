<template>
  <div class="login-page">
    <div class="login-card">
      <div class="login-logo">
        <el-icon :size="40"><FolderOpened /></el-icon>
        <h1>TeamHub</h1>
        <p class="subtitle">团队文档管理平台</p>
      </div>

      <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @submit.prevent="handleLogin">
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
            placeholder="请输入密码"
            size="large"
            :prefix-icon="Lock"
            show-password
            @keyup.enter="handleLogin"
          />
        </el-form-item>

        <el-button
          type="primary"
          size="large"
          class="login-btn"
          :loading="loading"
          @click="handleLogin"
        >
          登录
        </el-button>
      </el-form>

      <div class="login-footer">
        账号由组织管理员开通，请联系管理员获取。
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { FolderOpened, Message, Lock } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()

const formRef = ref(null)
const loading = ref(false)

const form = reactive({ email: '', password: '' })
const rules = {
  email: [{ required: true, type: 'email', message: '请输入有效的邮箱地址', trigger: 'blur' }],
  password: [{ required: true, min: 8, message: '密码至少 8 位', trigger: 'blur' }],
}

async function handleLogin() {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return

  loading.value = true
  try {
    const loggedIn = await authStore.login(form.email, form.password)
    if (!loggedIn) return
    ElMessage.success('登录成功')
    const redirect = route.query.redirect
    router.push(typeof redirect === 'string' && redirect.startsWith('/') && !redirect.startsWith('//') ? redirect : '/shared')
  } catch (e) {
    // 401 不弹全局提示，由登录入口明确反馈密码或账号错误。
    if (e.response?.status === 401) ElMessage.error(e.message || '登录失败，请检查账号和密码')
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
  width: 400px;
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
