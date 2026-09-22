<template>
  <div class="login-wrap">
    <el-card class="login-card" shadow="always">
      <div class="login-logo">
        <span class="logo-icon">⚙</span>
        <h2>TeamHub 管理后台</h2>
      </div>
      <el-form :model="form" :rules="rules" ref="formRef" label-position="top" @submit.prevent="handleLogin">
        <el-form-item label="邮箱" prop="email">
          <el-input v-model="form.email" placeholder="admin@example.com" size="large" />
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input v-model="form.password" type="password" placeholder="请输入密码" size="large" show-password />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" size="large" style="width:100%" :loading="loading" native-type="submit">
            登录
          </el-button>
        </el-form-item>
      </el-form>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { login } from '../api/admin'

const router = useRouter()
const formRef = ref(null)
const loading = ref(false)

const form = reactive({ email: '', password: '' })
const rules = {
  email: [{ required: true, message: '请输入邮箱', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

async function handleLogin() {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return
  loading.value = true
  try {
    const res = await login(form.email, form.password)
    const data = res.data
    // 检查是否超管
    if (!data.user?.is_superuser) {
      ElMessage.error('该账号没有管理员权限')
      return
    }
    localStorage.setItem('admin_token', data.access_token)
    localStorage.setItem('admin_user', JSON.stringify(data.user))
    ElMessage.success('登录成功')
    router.push('/dashboard')
  } catch (err) {
    const msg = err.response?.data?.detail || '登录失败，请检查邮箱和密码'
    ElMessage.error(msg)
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-wrap {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #1e2d3d 0%, #2c3e50 100%);
}
.login-card {
  width: 400px;
  border-radius: 12px;
}
.login-logo {
  text-align: center;
  margin-bottom: 24px;
}
.logo-icon {
  font-size: 48px;
  display: block;
  margin-bottom: 8px;
}
.login-logo h2 {
  margin: 0;
  color: #303133;
  font-size: 20px;
}
</style>
