<template>
  <div class="settings-page">
    <div class="page-header">
      <h2>设置</h2>
    </div>

    <!-- 卡片1：文件库总览 -->
    <el-card class="settings-card" shadow="hover" v-loading="infoLoading">
      <template #header>
        <div class="card-header">
          <el-icon><FolderOpened /></el-icon>
          <span>文件库总览</span>
        </div>
      </template>

      <el-form label-width="100px" label-position="left" class="settings-form">
        <el-form-item label="文件库路径">
          <div class="storage-path-row">
            <el-input :model-value="appStore.libraryPath || '未设置'" readonly />
            <el-button @click="openStorageDialog('library')" :disabled="!appStore.libraryPath || needsRecovery">迁移</el-button>
          </div>
        </el-form-item>
        <el-form-item label="待整理路径">
          <div class="storage-path-row">
            <el-input :model-value="libraryInfo?.pending_path || '未设置'" readonly />
            <el-button @click="openStorageDialog('pending')" :disabled="!appStore.libraryPath || needsRecovery">迁移</el-button>
          </div>
        </el-form-item>
        <p class="storage-help">两个位置可独立设置，也可相互包含。仅移动所选区域已收录的资料，另一区域及未收录文件保持原位。</p>
        <el-alert v-if="needsRecovery" type="warning" :closable="false" title="上次迁移尚未完成">
          <p>{{ migrationState?.error || migrationState?.warnings?.join('；') || '迁移日志已保存，请处理后继续使用。' }}</p>
          <el-button :loading="changingPendingPath" @click="handleStorageRecovery">处理迁移恢复</el-button>
        </el-alert>
      </el-form>

      <div class="stats-grid" v-if="libraryInfo">
        <div class="stat-card">
          <div class="stat-value">{{ libraryInfo.total_documents }}</div>
          <div class="stat-label">总文档数</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">{{ formatSize(libraryInfo.total_size) }}</div>
          <div class="stat-label">文件库大小</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">{{ libraryInfo.pending_count }}</div>
          <div class="stat-label">待整理</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">{{ libraryInfo.trash_count }}</div>
          <div class="stat-label">回收站（{{ formatSize(libraryInfo.trash_size) }}）</div>
        </div>
      </div>
    </el-card>

    <!-- 卡片2：使用偏好 -->
    <el-card class="settings-card" shadow="hover" v-loading="loading">
      <template #header>
        <div class="card-header">
          <el-icon><Files /></el-icon>
          <span>使用偏好</span>
        </div>
      </template>

      <el-form label-width="160px" label-position="left" class="settings-form">
        <el-form-item label="默认存储方式">
          <el-radio-group v-model="form.default_storage_mode">
            <el-radio value="move">移动</el-radio>
            <el-radio value="copy">复制</el-radio>
            <el-radio value="index">仅索引</el-radio>
          </el-radio-group>
        </el-form-item>

        <el-form-item label="单文件大小上限">
          <el-input-number
            v-model="fileSizeMB"
            :min="1"
            :max="102400"
            :step="100"
          />
          <span class="form-tip">MB（当前：{{ formatSize(form.max_file_size) }}）</span>
        </el-form-item>

        <el-form-item label="同名文件冲突处理">
          <el-radio-group v-model="form.on_conflict">
            <el-radio value="rename">自动重命名（加序号）</el-radio>
            <el-radio value="version">追加为新版本</el-radio>
          </el-radio-group>
        </el-form-item>

        <el-form-item label="文档库默认排序">
          <el-select v-model="form.default_sort_by" style="width: 160px; margin-right: 12px;">
            <el-option label="最近更新" value="updated_at" />
            <el-option label="名称" value="name" />
            <el-option label="大小" value="total_size" />
            <el-option label="收纳时间" value="created_at" />
          </el-select>
          <el-radio-group v-model="form.default_sort_order">
            <el-radio value="desc">降序</el-radio>
            <el-radio value="asc">升序</el-radio>
          </el-radio-group>
        </el-form-item>

        <el-form-item label="每页显示数量">
          <el-input-number
            v-model="form.items_per_page"
            :min="5"
            :max="100"
            :step="5"
          />
          <span class="form-tip">个文件（文档库和待整理页面）</span>
        </el-form-item>

        <el-form-item label="文件夹导航">
          <el-switch v-model="form.library_show_tree_view" />
          <span class="form-tip">显示分类文件夹；全部资料和收藏入口始终保留</span>
        </el-form-item>


        <el-form-item label="桌面悬浮窗">
          <el-switch v-model="form.enable_floating_window" />
          <span class="form-tip">拖拽文件到悬浮窗可快速添加到待整理</span>
        </el-form-item>

        <el-form-item>
          <el-button type="primary" @click="handleSave" :loading="saving">保存设置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 卡片3：维护与清理 -->
    <el-card class="settings-card" shadow="hover" v-loading="loading">
      <template #header>
        <div class="card-header">
          <el-icon><Tools /></el-icon>
          <span>维护与清理</span>
        </div>
      </template>

      <el-form label-width="160px" label-position="left" class="settings-form">
        <el-form-item label="启动时自动扫描">
          <el-switch v-model="form.auto_scan_on_startup" />
          <span class="form-tip">每次启动时自动检查文件是否缺失</span>
        </el-form-item>

        <el-form-item label="最大版本数">
          <el-input-number v-model="form.max_versions" :min="0" :max="999" />
          <span class="form-tip">0 表示不限制</span>
        </el-form-item>

        <el-form-item label="版本保留天数">
          <el-input-number v-model="form.max_version_age_days" :min="0" :max="9999" />
          <span class="form-tip">0 表示永久保留</span>
        </el-form-item>

        <el-form-item label="回收站自动清理">
          <el-input-number v-model="form.trash_auto_clean_days" :min="0" :max="9999" />
          <span class="form-tip">天后自动清理，0 表示不自动清理</span>
        </el-form-item>

        <el-form-item>
          <el-button type="primary" @click="handleSave" :loading="saving">保存设置</el-button>
        </el-form-item>
      </el-form>

      <el-divider />

      <!-- 维护操作并排 -->
      <div class="maintenance-row">
        <el-button type="danger" @click="handleCleanTrash" :loading="cleaningTrash">
          <el-icon><Delete /></el-icon>
          立即清空回收站
          <span v-if="libraryInfo?.trash_count > 0" class="btn-count">（{{ libraryInfo.trash_count }} 个）</span>
        </el-button>
        <el-button @click="handleCleanVersions" :loading="cleaningVersions">
          <el-icon><Clock /></el-icon>
          立即版本清理
        </el-button>
        <el-button @click="handleScan" :loading="scanning">
          <el-icon><Search /></el-icon>
          扫描文件库
        </el-button>
      </div>

      <el-divider />

      <el-text type="info" size="small">备份由维护者在停止服务后执行，恢复至全新目录。操作见项目《MVP运维与交接》。</el-text>
    </el-card>

    <AISettingsCard />

    <!-- 云服务连接与账号 -->
    <el-card class="settings-card" shadow="hover" v-loading="syncCardLoading">
      <template #header>
        <div class="card-header">
          <el-icon><Promotion /></el-icon>
          <span>云服务</span>
          <el-tag v-if="syncInfo?.logged_in" type="success" size="small" style="margin-left:auto;">已登录</el-tag>
          <el-tag v-else type="info" size="small" style="margin-left:auto;">未登录</el-tag>
        </div>
      </template>

      <!-- 云服务地址（始终可见） -->
      <el-form label-width="100px" label-position="left" class="settings-form" style="max-width:560px;margin-bottom:16px;">
        <el-form-item label="云服务地址">
          <div style="display:flex;gap:8px;flex:1;">
            <el-input v-model="cloudApiUrl" placeholder="http://服务器公网IP 或 https://域名" clearable />
            <el-button @click="handleSaveCloudUrl" :loading="savingCloudUrl">保存</el-button>
          </div>
        </el-form-item>
      </el-form>

      <!-- 未登录：引导前往登录页 -->
      <template v-if="!syncInfo?.logged_in">
        <div style="padding:16px 0;display:flex;align-items:center;gap:16px;">
          <span style="color:#909399;font-size:14px;">登录后即可查看共享资料库、发布资料或收发投递</span>
          <el-button type="primary" @click="router.push('/login')">前往登录</el-button>
        </div>
      </template>

      <!-- 已登录 -->
      <template v-else>
        <!-- 账号信息行 -->
        <div class="cloud-account-row" style="margin-bottom:16px;">
          <el-avatar :size="36" style="background:#4096ff;font-size:14px;flex-shrink:0;">
            {{ syncInfo?.user?.display_name?.[0]?.toUpperCase() || '?' }}
          </el-avatar>
          <div class="cloud-account-info">
            <div class="cloud-name">{{ syncInfo?.user?.display_name || '云端用户' }}</div>
            <div class="cloud-email">{{ syncInfo?.user?.email || '' }}</div>
          </div>
        </div>

        <div class="cloud-service-actions">
          <p>在云仓库查看或切换共享资料库；发布资料时可直接选择发布到哪里。</p>
          <el-button type="primary" plain @click="router.push('/shared')">打开云仓库</el-button>
          <el-button text @click="handleCloudLogout">退出登录</el-button>
        </div>
      </template>
    </el-card>

    <el-dialog v-model="showPendingDialog" :title="migrationArea === 'library' ? '迁移文件库' : '迁移待整理区'"
      width="600px" :close-on-click-modal="false" :close-on-press-escape="!changingPendingPath && !previewLoading"
      :show-close="!changingPendingPath && !previewLoading">
      <p class="storage-help">{{ migrationArea === 'library' ? '迁移已整理资料、标签数据库、版本与回收站；待整理区保持原位。' : '只迁移已登记的待整理资料，文件库、标签和版本历史保持原位。' }}</p>
      <el-form label-position="top" :disabled="changingPendingPath || previewLoading">
        <el-form-item label="当前位置"><el-input :model-value="migrationSource" readonly /></el-form-item>
        <el-form-item label="新位置">
          <div class="storage-path-row">
            <el-input v-model="pendingNewName" placeholder="输入完整路径，或选择文件夹" @input="migrationPreview = null" />
            <el-button v-if="hasDirectoryPicker" @click="chooseStorageDirectory">选择文件夹</el-button>
          </div>
        </el-form-item>
      </el-form>
      <div v-if="migrationPreview" class="migration-summary">
        <strong>将迁移 {{ migrationPreview.count }} 项资料{{ migrationPreview.metadata ? '及系统数据' : '' }}</strong>
        <p>新位置：{{ migrationPreview.target }}</p>
        <ul><li v-for="(name, index) in migrationPreview.names.slice(0, 8)" :key="index">{{ name }}</li></ul>
        <span v-if="migrationPreview.names.length > 8">其余 {{ migrationPreview.names.length - 8 }} 项也将按登记迁移。</span>
        <p>先复制校验，成功切换后清理旧副本；同名冲突会停止，不覆盖文件。</p>
      </div>
      <el-alert v-if="changingPendingPath" type="info" :closable="false" title="正在迁移，请保持应用运行。期间暂停其他资料操作。" />
      <template #footer>
        <el-button :disabled="changingPendingPath || previewLoading" @click="showPendingDialog = false">取消</el-button>
        <el-button v-if="!migrationPreview" type="primary" :loading="previewLoading" @click="previewMigration">查看迁移清单</el-button>
        <el-button v-else type="primary" :loading="changingPendingPath" @click="confirmMigration">确认迁移</el-button>
      </template>
    </el-dialog>


  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAppStore } from '@/stores/app'
import { useAuthStore } from '@/stores/auth'
import { scanLibrary, getLibraryInfo, cleanTrash, cleanVersions, previewStorageMigration, migrateStorage, getStorageMigration, recoverStorageMigration } from '@/api/settings'
import { getSyncStatus } from '@/api/sync'
import { ElMessage, ElMessageBox } from 'element-plus'
import { FolderOpened, Files, Clock, Delete, Tools, Search, Promotion } from '@element-plus/icons-vue'
import AISettingsCard from '@/components/AISettingsCard.vue'

const router = useRouter()
const appStore = useAppStore()
const authStore = useAuthStore()
const loading = ref(false)
const infoLoading = ref(false)
const saving = ref(false)
const scanning = ref(false)
const cleaningTrash = ref(false)
const cleaningVersions = ref(false)

// 待整理路径修改状态
const showPendingDialog = ref(false)
const pendingNewName = ref('')
const migrationArea = ref('library')
const migrationPreview = ref(null)
const migrationState = ref(null)
const previewLoading = ref(false)
const needsRecovery = computed(() => migrationState.value && !['complete', 'cancelled'].includes(migrationState.value.phase))
const migrationSource = computed(() => migrationArea.value === 'library' ? appStore.libraryPath : libraryInfo.value?.pending_path)
const hasDirectoryPicker = Boolean(window.electron?.selectDirectory)
const changingPendingPath = ref(false)

const libraryInfo = ref(null)

const form = ref({
  default_storage_mode: 'move',
  max_file_size: 1073741824,
  on_conflict: 'rename',
  default_sort_by: 'updated_at',
  default_sort_order: 'desc',
  max_versions: 0,
  max_version_age_days: 0,
  trash_auto_clean_days: 30,
  auto_scan_on_startup: false,
  items_per_page: 15,
  enable_floating_window: true,
  library_show_flat_view: true,
  library_show_tree_view: true,
})

// MB 双向换算（UI 用 MB，内部用字节）
const fileSizeMB = computed({
  get: () => Math.round(form.value.max_file_size / (1024 * 1024)),
  set: (mb) => { form.value.max_file_size = mb * 1024 * 1024 },
})

function formatSize(bytes) {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  let val = bytes
  while (val >= 1024 && i < units.length - 1) {
    val /= 1024
    i++
  }
  return `${val.toFixed(i === 0 ? 0 : 1)} ${units[i]}`
}

const loadLibraryInfo = async () => {
  infoLoading.value = true
  try {
    libraryInfo.value = await getLibraryInfo()
  } catch (e) {
    // 静默失败，信息不是关键
  } finally {
    infoLoading.value = false
  }
}

const loadSettings = async () => {
  loading.value = true
  try {
    await appStore.fetchSettings()
    const s = appStore.settings
    if (s.cloud_api_url) cloudApiUrl.value = s.cloud_api_url
    form.value = {
      default_storage_mode: s.default_storage_mode || 'move',
      max_file_size: s.max_file_size ?? 1073741824,
      on_conflict: s.on_conflict || 'rename',
      default_sort_by: s.default_sort_by || 'updated_at',
      default_sort_order: s.default_sort_order || 'desc',
      max_versions: s.max_versions ?? 0,
      max_version_age_days: s.max_version_age_days ?? 0,
      trash_auto_clean_days: s.trash_auto_clean_days ?? 30,
      auto_scan_on_startup: s.auto_scan_on_startup ?? false,
      items_per_page: s.items_per_page ?? 15,
      enable_floating_window: s.enable_floating_window !== undefined ? s.enable_floating_window : true,
      library_show_flat_view: s.library_show_flat_view !== undefined ? s.library_show_flat_view : true,
      library_show_tree_view: s.library_show_tree_view !== undefined ? s.library_show_tree_view : true,
    }
  } catch (e) {
    ElMessage.error('加载设置失败')
  } finally {
    loading.value = false
  }
}

const handleSave = async () => {
  form.value.library_show_flat_view = true
  saving.value = true
  try {
    await appStore.updateSettings(form.value)
    
    // 如果修改了悬浮窗设置，立即生效
    if (window.electron?.toggleFloatingWindow) {
      window.electron.toggleFloatingWindow(form.value.enable_floating_window)
    }
    
    ElMessage.success('设置已保存')
  } catch (e) {
    ElMessage.error(e.message || '保存失败')
  } finally {
    saving.value = false
  }
}

const handleScan = async () => {
  scanning.value = true
  try {
    const result = await scanLibrary()
    ElMessage.success(`扫描完成：共检查 ${result.scanned} 个文档，${result.missing} 个文件缺失`)
  } catch (e) {
    ElMessage.error(e.message || '扫描失败')
  } finally {
    scanning.value = false
  }
}

const handleCleanTrash = async () => {
  const trashCount = libraryInfo.value?.trash_count || 0
  if (trashCount === 0) {
    ElMessage.info('回收站已经是空的')
    return
  }
  try {
    await ElMessageBox.confirm(
      `将永久删除回收站中的 ${trashCount} 个文档，此操作不可恢复。确定继续？`,
      '清空回收站',
      { type: 'warning', confirmButtonText: '确定清空', cancelButtonText: '取消' }
    )
  } catch {
    return
  }
  cleaningTrash.value = true
  try {
    const result = await cleanTrash()
    ElMessage.success(`已永久删除 ${result.deleted} 个文档`)
    await loadLibraryInfo()
  } catch (e) {
    ElMessage.error(e.message || '清空失败')
  } finally {
    cleaningTrash.value = false
  }
}

const handleCleanVersions = async () => {
  if (form.value.max_versions === 0 && form.value.max_version_age_days === 0) {
    ElMessage.info('未设置版本限制策略，无需清理')
    return
  }
  cleaningVersions.value = true
  try {
    const result = await cleanVersions()
    if (result.deleted > 0) {
      ElMessage.success(`版本清理完成，共删除 ${result.deleted} 个旧版本`)
    } else {
      ElMessage.info('没有需要清理的版本')
    }
  } catch (e) {
    ElMessage.error(e.message || '版本清理失败')
  } finally {
    cleaningVersions.value = false
  }
}

const openStorageDialog = (area) => {
  migrationArea.value = area
  pendingNewName.value = ''
  migrationPreview.value = null
  showPendingDialog.value = true
}
const chooseStorageDirectory = async () => {
  const path = await window.electron.selectDirectory()
  if (path) { pendingNewName.value = path; migrationPreview.value = null }
}
const previewMigration = async () => {
  if (!pendingNewName.value.trim()) return ElMessage.warning('请输入新位置的完整路径')
  previewLoading.value = true
  try {
    migrationPreview.value = await previewStorageMigration({ area: migrationArea.value, path: pendingNewName.value.trim() })
  } catch (e) { ElMessage.error(e.message || '无法生成迁移清单') }
  finally { previewLoading.value = false }
}
const refreshStorage = async () => {
  await appStore.fetchLibraryInfo()
  await loadLibraryInfo()
  migrationState.value = await getStorageMigration()
}
const confirmMigration = async () => {
  if (changingPendingPath.value || !migrationPreview.value) return
  changingPendingPath.value = true
  try {
    const result = await migrateStorage({ area: migrationArea.value, path: migrationPreview.value.target })
    ElMessage({ type: result.phase === 'complete' ? 'success' : 'warning', message: result.message })
    showPendingDialog.value = false
  } catch (e) { ElMessage.error(e.message || '迁移中断，请处理迁移恢复') }
  finally {
    changingPendingPath.value = false
    await refreshStorage()
  }
}
const handleStorageRecovery = async () => {
  changingPendingPath.value = true
  try {
    const result = await recoverStorageMigration()
    ElMessage.info(result.message)
  } catch (e) { ElMessage.error(e.message || '恢复失败，已保留副本') }
  finally { changingPendingPath.value = false; await refreshStorage() }
}

// ========== 云服务 ==========

const cloudApiUrl = ref('http://localhost:9000')
const savingCloudUrl = ref(false)

const handleSaveCloudUrl = async () => {
  const url = cloudApiUrl.value?.trim()
  if (!url) {
    ElMessage.warning('请输入云服务地址')
    return
  }
  savingCloudUrl.value = true
  const changed = url.replace(/\/+$/, '') !== (appStore.settings.cloud_api_url || '').replace(/\/+$/, '')
  try {
    await appStore.updateSettings({ cloud_api_url: url })
    if (changed) {
      syncInfo.value = null
      await authStore.restoreSession()
      await Promise.all([loadSyncInfo(), appStore.fetchSyncStatus()])
      ElMessage.success('云服务地址已保存，请登录云服务')
    } else {
      ElMessage.success('云服务地址已保存')
    }
  } catch (e) {
    ElMessage.error(e.message || '保存失败')
  } finally {
    savingCloudUrl.value = false
  }
}

const syncCardLoading = ref(false)

const syncInfo = ref(null)
let syncInfoSequence = 0, syncInfoDisposed = false
const syncIdentity = () => JSON.stringify([appStore.settings.cloud_api_url, appStore.libraryPath, authStore.user?.id, authStore.serverId])
const loadSyncInfo = async () => {
  const sequence = ++syncInfoSequence, identity = syncIdentity()
  syncCardLoading.value = true
  try {
    const value = await getSyncStatus()
    if (!syncInfoDisposed && sequence === syncInfoSequence && identity === syncIdentity()) syncInfo.value = value
  } catch {
    if (!syncInfoDisposed && sequence === syncInfoSequence) syncInfo.value = null
  } finally {
    if (!syncInfoDisposed && sequence === syncInfoSequence) syncCardLoading.value = false
  }
}
const handleCloudLogout = async () => {
  if (!await authStore.logout()) {
    ElMessage.error(authStore.workspaceError || '退出失败，请重试')
    return
  }
  await loadSyncInfo()
  ElMessage.success('已退出登录')
}
watch(syncIdentity, () => { syncInfo.value = null; loadSyncInfo() }, { flush: 'sync' })
onUnmounted(() => { syncInfoDisposed = true; syncInfoSequence++ })


onMounted(() => {
  getStorageMigration().then(value => { migrationState.value = value }).catch(() => {})
  loadSettings()
  loadLibraryInfo()
  loadSyncInfo()
})

</script>

<style scoped>
.cloud-service-actions p { color: #68788a; font-size: 13px; line-height: 1.7; }
.storage-path-row { display: flex; gap: 10px; width: 100%; min-width: 0; }
.storage-help { color: #68788a; line-height: 1.7; font-size: 13px; margin: 0 0 18px; }
.migration-summary { background: #f4f7fb; border-radius: 8px; padding: 16px; margin: 12px 0; font-size: 13px; line-height: 1.6; overflow-wrap: anywhere; }
.migration-summary ul { max-height: 140px; overflow-y: auto; }

.settings-page {
  width: 100%;
}

.page-header {
  margin-bottom: 24px;
}

.page-header h2 {
  font-size: 22px;
  color: #2c3e50;
}

.settings-card {
  border-radius: 10px;
  border: 1px solid #e4e7ed;
  box-shadow: var(--shadow-md);
  margin-bottom: 20px;
}

.settings-card :deep(.el-card__header) {
  background: linear-gradient(to right, #f5f9ff, #ffffff);
  border-left: 4px solid var(--color-accent);
  border-bottom: 1px solid #e4e7ed;
  padding: 16px 20px 16px 16px;
}

.settings-card :deep(.el-card__body) {
  padding: 24px;
}

.card-header {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 16px;
  font-weight: 600;
  color: #2c3e50;
}

.card-header .el-icon {
  color: var(--color-accent);
  font-size: 18px;
}

.settings-form {
  max-width: 700px;
}

.form-tip {
  margin-left: 12px;
  font-size: 13px;
  color: #95a5a6;
}

.pending-radio-tip {
  display: block;
  font-size: 12px;
  color: #909399;
  margin-top: 2px;
  margin-left: 24px;
  line-height: 1.4;
}

/* 统计看板 */
.stats-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  margin-top: 16px;
}

.stat-card {
  background: linear-gradient(135deg, #f0f7ff 0%, #ffffff 100%);
  border: 1px solid #dde8f8;
  border-radius: 10px;
  padding: 20px;
  text-align: center;
}

.stat-value {
  font-size: 26px;
  font-weight: 700;
  background: linear-gradient(135deg, #4096ff, #2c3e50);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  margin-bottom: 4px;
}

.stat-label {
  font-size: 12px;
  color: #95a5a6;
}

/* 备份说明 */
.backup-tip {
  padding: 2px 0 0;
}

/* 维护操作行 */
.maintenance-row {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  align-items: center;
}

.btn-count {
  font-size: 12px;
  opacity: 0.85;
  margin-left: 2px;
}

.device-id-row {
  display: flex;
  align-items: center;
  width: 100%;
}

.device-id-input {
  flex: 1;
  max-width: 340px;
}

.sync-last-change {
  margin-top: 10px;
  font-size: 12px;
  color: #95a5a6;
}

.sync-ops-row {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  align-items: center;
  margin-bottom: 4px;
}

/* 云端账号信息行 */
.cloud-account-row {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 8px 0;
}

.cloud-account-info {
  flex: 1;
}

.cloud-name {
  font-size: 16px;
  font-weight: 600;
  color: #2c3e50;
}

.cloud-email {
  font-size: 13px;
  color: #909399;
  margin-top: 2px;
}

.cloud-value {
  font-size: 14px;
  color: #2c3e50;
  font-weight: 500;
}
</style>
