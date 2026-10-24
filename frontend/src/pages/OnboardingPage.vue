<template>
  <div class="onboarding-page">
    <div class="onboarding-card">
      <div class="onboarding-logo">
        <el-icon :size="40"><FolderOpened /></el-icon>
        <h1>开始团队协作</h1>
        <p class="subtitle">加入团队，在工作空间中共享资料</p>
      </div>

      <div v-if="step === 'choose'" class="choose-panel">
        <el-alert title="加入已有团队" type="info" :closable="false">
          请联系团队管理员，通过你的账号邮箱邀请你加入。
          <el-button text type="primary" :loading="refreshing" @click="refreshMembership">刷新加入状态</el-button>
        </el-alert>
        <el-button type="primary" size="large" class="action-btn" :icon="Plus" @click="step = 'create'">
          创建团队和工作空间
        </el-button>
      </div>

      <div v-else class="create-panel">
        <el-button text :icon="ArrowLeft" @click="step = 'choose'" class="back-btn">返回</el-button>
        <el-alert v-if="createdTeam" title="团队已创建，可继续完成工作空间设置" type="info" :closable="false" />
        <el-form ref="createFormRef" :model="createForm" :rules="createRules" label-position="top">
          <el-form-item label="团队名称" prop="name">
            <el-input v-model="createForm.name" :disabled="!!createdTeam" placeholder="如：产品研发部" size="large" @input="autoGenerateSlug" />
          </el-form-item>
          <el-form-item label="唯一标识 (slug)" prop="slug">
            <el-input v-model="createForm.slug" :disabled="!!createdTeam" placeholder="如：product-dev" size="large">
              <template #prepend>teamhub/</template>
            </el-input>
          </el-form-item>
          <el-form-item label="工作空间名称" prop="workspaceName">
            <el-input v-model="createForm.workspaceName" :disabled="!!createdWorkspace" placeholder="如：默认工作空间" size="large" />
          </el-form-item>
          <el-button type="primary" size="large" class="action-btn" :loading="submitting" @click="handleCreate">
            {{ createdTeam ? '继续设置' : '一键创建' }}
          </el-button>
        </el-form>
      </div>

      <div class="onboarding-footer">
        <el-link type="info" @click="skipOnboarding">暂时跳过，稍后设置</el-link>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, watch, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { FolderOpened, Plus, ArrowLeft } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'
import { useAppStore } from '@/stores/app'
import { createTeam, createWorkspace, bindWorkspace } from '@/api/sync'

const router = useRouter()
const authStore = useAuthStore()
const appStore = useAppStore()
const step = ref('choose')
const submitting = ref(false)
const refreshing = ref(false)
const createdTeam = ref(null)
const createdWorkspace = ref(null)
const createFormRef = ref(null)
const createForm = reactive({ name: '', slug: '', workspaceName: '默认工作空间' })
let active = true
let scopeGeneration = 0
const cloudUrl = () => appStore.settings.cloud_api_url || ''
const captureScope = () => ({ generation: scopeGeneration, actorId: authStore.user?.id, cloudUrl: cloudUrl() })
const isCurrent = scope => active && authStore.isLoggedIn && scope.actorId &&
  scope.generation === scopeGeneration && scope.actorId === authStore.user?.id && scope.cloudUrl === cloudUrl()
watch([() => authStore.user?.id, () => authStore.isLoggedIn, cloudUrl], () => {
  scopeGeneration++
  createdTeam.value = null; createdWorkspace.value = null
  submitting.value = false; refreshing.value = false; step.value = 'choose'
  Object.assign(createForm, { name: '', slug: '', workspaceName: '默认工作空间' })
}, { flush: 'sync' })
onBeforeUnmount(() => { active = false; scopeGeneration++ })
const createRules = {
  name: [{ required: true, message: '请输入团队名称', trigger: 'blur' }],
  slug: [
    { required: true, message: '请输入唯一标识', trigger: 'blur' },
    { pattern: /^[a-z0-9-]{2,50}$/, message: '请输入 2–50 个小写字母、数字或短横线', trigger: 'blur' },
  ],
  workspaceName: [{ required: true, message: '请输入工作空间名称', trigger: 'blur' }],
}

async function refreshMembership() {
  const scope = captureScope()
  if (refreshing.value || !isCurrent(scope)) return
  refreshing.value = true
  try {
    const restored = await authStore.restoreSession()
    if (needsLogin(scope, restored)) {
      ElMessage.warning('登录状态已失效，请重新登录')
      await router.push('/login')
      return
    }
    if (!isCurrent(scope)) return
    if (authStore.teams.length) {
      await router.push('/shared')
    } else {
      ElMessage.info('尚未加入团队，请联系管理员邀请')
    }
  } finally {
    if (isCurrent(scope)) refreshing.value = false
  }
}

// restoreSession 会清空失效身份，此时普通 scope 检查也会失败。
// 保留失效登录的提示，同时不打断已经开始的新登录或切换后的服务。
function needsLogin(scope, restored) {
  return active && !restored && !authStore.isLoggedIn && !authStore.loading && scope.cloudUrl === cloudUrl()
}

function autoGenerateSlug() {
  if (!createForm.name) return
  createForm.slug = createForm.name.toLowerCase()
    .replace(/[\s_]+/g, '-')
    .replace(/[^a-z0-9-]/g, '')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '')
    .slice(0, 50)
  if (createForm.slug.length < 2) createForm.slug = 'team-' + Date.now().toString(36)
}

async function handleCreate() {
  const scope = captureScope()
  if (submitting.value || !isCurrent(scope)) return
  const valid = await createFormRef.value?.validate().catch(() => false)
  if (!valid || submitting.value || !isCurrent(scope)) return
  const form = { ...createForm }
  submitting.value = true
  try {
    // 保留已完成步骤，后续失败重试时不重复创建团队或工作空间。
    if (!createdTeam.value) {
      const team = await createTeam({ name: form.name, slug: form.slug })
      if (!isCurrent(scope)) return
      createdTeam.value = team
    }
    if (!createdWorkspace.value) {
      const workspace = await createWorkspace(createdTeam.value.id, { name: form.workspaceName, description: '' })
      if (!isCurrent(scope)) return
      createdWorkspace.value = workspace
    }
    await bindWorkspace(createdWorkspace.value.id, createdWorkspace.value.name)
    if (!isCurrent(scope)) return
    const restored = await authStore.restoreSession()
    if (needsLogin(scope, restored)) {
      ElMessage.warning('登录状态已失效，请重新登录')
      await router.push('/login')
      return
    }
    if (!isCurrent(scope)) return
    ElMessage.success('团队和工作空间创建成功')
    await router.push('/shared')
  } catch (error) {
    // API 错误由请求拦截器显示；恢复状态的错误在此显示。
    if (isCurrent(scope) && !error.response) ElMessage.error(error.message || '设置未完成，请重试')
  } finally {
    if (isCurrent(scope)) submitting.value = false
  }
}

function skipOnboarding() {
  authStore.onboardingChecked = false
  router.push('/library')
}
</script>

<style scoped>
.onboarding-page { min-height: 100vh; display: flex; align-items: center; justify-content: center; background: linear-gradient(135deg, #1e2d3d 0%, #2c3e50 100%); padding: 20px; }
.onboarding-card { background: #fff; border-radius: 16px; padding: 48px 40px 36px; width: 520px; max-height: 90vh; overflow-y: auto; box-shadow: 0 20px 60px rgba(0, 0, 0, 0.25); }
.onboarding-logo { text-align: center; margin-bottom: 32px; }
.onboarding-logo .el-icon { color: #4096ff; margin-bottom: 16px; }
.onboarding-logo h1 { font-size: 24px; font-weight: 700; color: #1e2d3d; margin: 0 0 8px; }
.subtitle { color: #909399; font-size: 14px; margin: 0; }
.back-btn { margin-bottom: 16px; }
.create-panel .el-alert { margin-bottom: 16px; }
.action-btn { width: 100%; margin-top: 16px; height: 44px; font-size: 16px; }
.onboarding-footer { text-align: center; margin-top: 24px; padding-top: 16px; border-top: 1px solid #e4e7ed; }
</style>
