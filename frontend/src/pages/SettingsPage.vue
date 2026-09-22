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
          <el-input :model-value="appStore.libraryPath || '未设置'" disabled />
        </el-form-item>
        <el-form-item label="待整理路径">
          <div style="display: flex; align-items: center; gap: 8px; flex: 1;">
            <el-input
              :model-value="appStore.libraryPath ? (appStore.libraryPath + '/' + (libraryInfo?.pending_folder_name || '待整理')) : '未设置'"
              disabled
              style="flex: 1;"
            />
            <el-button size="small" @click="openPendingDialog" :disabled="!appStore.libraryPath">修改</el-button>
          </div>
        </el-form-item>
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

        <el-form-item label="平铺展示">
          <el-switch v-model="form.library_show_flat_view" />
          <span class="form-tip">显示全部文档列表（无目录过滤）</span>
        </el-form-item>

        <el-form-item label="目录树展示">
          <el-switch v-model="form.library_show_tree_view" />
          <span class="form-tip">左侧显示目录树，按文件夹浏览文档</span>
        </el-form-item>

        <el-alert
          v-if="!form.library_show_flat_view && !form.library_show_tree_view"
          type="warning"
          title="至少需要启用一种展示方式"
          :closable="false"
          style="margin-bottom: 16px; margin-left: 160px;"
        />

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

      <!-- 数据库云端备份 -->
      <div class="backup-tip">
        <el-text type="info" size="small">备份本地数据库（文档记录、标签、版本）到云端，换电脑或重装后可一键恢复。需先登录云服务。</el-text>
      </div>
      <div class="maintenance-row" style="margin-top: 10px;">
        <el-button @click="handleCreateBackup" :loading="backupCreating">
          <el-icon><Cloudy /></el-icon>
          备份数据库到云端
        </el-button>
        <el-button @click="handleOpenRestoreDialog">
          <el-icon><Upload /></el-icon>
          从云端恢复
        </el-button>
      </div>
    </el-card>

    <!-- 卡片5：云端同步 -->
    <el-card class="settings-card" shadow="hover" v-loading="syncCardLoading">
      <template #header>
        <div class="card-header">
          <el-icon><Promotion /></el-icon>
          <span>云端同步</span>
          <el-tag v-if="syncInfo?.logged_in && syncInfo?.workspace_id" type="success" size="small" style="margin-left:auto;">已连接</el-tag>
          <el-tag v-else-if="syncInfo?.logged_in" type="warning" size="small" style="margin-left:auto;">已登录，未绑定空间</el-tag>
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
          <span style="color:#909399;font-size:14px;">尚未登录云服务，请先登录后使用同步功能</span>
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

        <!-- 工作空间绑定 -->
        <el-form label-width="100px" label-position="left" class="settings-form" style="max-width:560px;">
          <el-form-item label="工作空间">
            <div style="display:flex;gap:8px;flex:1;align-items:center;">
              <el-select
                v-model="selectedWorkspaceId"
                placeholder="选择工作空间"
                style="flex:1;"
                @change="onWorkspaceSelectChange"
              >
                <el-option
                  v-for="ws in syncWorkspaces"
                  :key="ws.id"
                  :label="`${ws.name}（${ws.team_name}）`"
                  :value="ws.id"
                />
              </el-select>
              <el-button size="small" @click="handleBindWorkspace" :disabled="!selectedWorkspaceId">绑定</el-button>
            </div>
          </el-form-item>
          <el-form-item label="当前绑定" v-if="syncInfo?.workspace_name">
            <span class="cloud-value">{{ syncInfo.workspace_name }}</span>
          </el-form-item>
        </el-form>

        <!-- 同步统计（已绑定后） -->
        <template v-if="syncInfo?.workspace_id">
          <div class="stats-grid" style="margin-top:8px;margin-bottom:16px;">
            <div class="stat-card">
              <div class="stat-value">{{ syncInfo.pushed ?? 0 }}</div>
              <div class="stat-label">已推送</div>
            </div>
            <div class="stat-card">
              <div class="stat-value" :style="syncInfo.modified > 0 ? 'color:#e6a23c' : ''">{{ syncInfo.modified ?? 0 }}</div>
              <div class="stat-label">本地已改</div>
            </div>
          </div>
          <div style="color:#909399;font-size:13px;">
            在文档库中选择文档，通过右侧菜单「推送到云端」可将文档上传至云端，每次推送云端自动保留历史版本。
          </div>
        </template>
      </template>
    </el-card>

    <!-- 修改待整理路径对话框 -->
    <el-dialog
      v-model="showPendingDialog"
      title="修改待整理文件夹"
      width="480px"
      :close-on-click-modal="false"
    >
      <el-form label-width="90px" label-position="left">
        <el-form-item label="文件夹名称">
          <el-input
            v-model="pendingNewName"
            placeholder="请输入新的文件夹名称"
            maxlength="100"
            clearable
          />
        </el-form-item>
        <el-form-item label="处理方式">
          <el-radio-group v-model="pendingMigrate" style="display: flex; flex-direction: column; gap: 10px;">
            <el-radio :value="true">
              迁移文件
              <span class="pending-radio-tip">将现有待整理文件移至新目录，更新数据库记录</span>
            </el-radio>
            <el-radio :value="false">
              仅清空记录
              <span class="pending-radio-tip">清空待整理数据库记录，保留实体文件（文件不会被删除）</span>
            </el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <el-alert
        v-if="pendingMigrate === false"
        type="warning"
        title="所选「仅清空记录」方式将从数据库中删除所有待整理条目，但实体文件仍保留在原目录中，不会被删除。"
        :closable="false"
        show-icon
        style="margin-top: 4px;"
      />
      <template #footer>
        <el-button @click="showPendingDialog = false">取消</el-button>
        <el-button type="primary" :loading="changingPendingPath" @click="handlePendingPathChange">确定修改</el-button>
      </template>
    </el-dialog>

    <!-- 从云端恢复对话框 -->
    <el-dialog
      v-model="backupDialogVisible"
      title="从云端恢复数据库"
      width="580px"
      :close-on-click-modal="false"
    >
      <div v-loading="backupListLoading">
        <el-empty v-if="!backupListLoading && backupList.length === 0" description="暂无云端备份" />
        <el-table v-else :data="backupList" style="width: 100%">
          <el-table-column label="备份时间" min-width="150">
            <template #default="{ row }">{{ formatBackupTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="来源设备" prop="device_name" min-width="100" show-overflow-tooltip />
          <el-table-column label="大小" width="90">
            <template #default="{ row }">{{ formatBackupSize(row.file_size) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="140" fixed="right">
            <template #default="{ row }">
              <el-button
                size="small"
                type="primary"
                :loading="backupRestoring"
                @click="handleRestore(row)"
              >恢复</el-button>
              <el-button
                size="small"
                type="danger"
                plain
                @click="handleDeleteBackup(row)"
              >删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>
      <template #footer>
        <el-button @click="backupDialogVisible = false">关闭</el-button>
      </template>
    </el-dialog>

  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAppStore } from '@/stores/app'
import { useAuthStore } from '@/stores/auth'
import { scanLibrary, getLibraryInfo, cleanTrash, cleanVersions, updatePendingPath, createBackup, listBackups, restoreBackup, deleteBackup } from '@/api/settings'
import { bindWorkspace, getSyncStatus, listCloudWorkspaces } from '@/api/sync'
import { setCloudApiUrl } from '@/api/cloud'
import { ElMessage, ElMessageBox } from 'element-plus'
import { FolderOpened, Files, Clock, Delete, Tools, Search, Promotion, Upload, Cloudy } from '@element-plus/icons-vue'

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
const pendingMigrate = ref(true)
const changingPendingPath = ref(false)

// 数据库云端备份状态
const backupCreating = ref(false)
const backupDialogVisible = ref(false)
const backupList = ref([])
const backupListLoading = ref(false)
const backupRestoring = ref(false)

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
  if (!form.value.library_show_flat_view && !form.value.library_show_tree_view) {
    ElMessage.warning('文档库展示：至少需要启用一种展示方式')
    return
  }
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

// 待整理路径修改
const openPendingDialog = () => {
  pendingNewName.value = libraryInfo.value?.pending_folder_name || '待整理'
  pendingMigrate.value = true
  showPendingDialog.value = true
}

const handlePendingPathChange = async () => {
  const newName = pendingNewName.value?.trim()
  if (!newName) {
    ElMessage.warning('请输入文件夹名称')
    return
  }
  if (newName === (libraryInfo.value?.pending_folder_name || '待整理')) {
    ElMessage.info('文件夹名称未发生变化')
    return
  }
  changingPendingPath.value = true
  try {
    const res = await updatePendingPath({ new_folder_name: newName, migrate: pendingMigrate.value })
    ElMessage.success(res.message || '待整理路径已更新')
    showPendingDialog.value = false
    await loadLibraryInfo()
  } catch (e) {
    ElMessage.error(e.message || '修改失败')
  } finally {
    changingPendingPath.value = false
  }
}

// ========== 卡片4：云端同步 ==========

const cloudApiUrl = ref('http://localhost:9000')
const savingCloudUrl = ref(false)

const handleSaveCloudUrl = async () => {
  const url = cloudApiUrl.value?.trim()
  if (!url) {
    ElMessage.warning('请输入云服务地址')
    return
  }
  savingCloudUrl.value = true
  try {
    await appStore.updateSettings({ cloud_api_url: url })
    setCloudApiUrl(url)
    ElMessage.success('云服务地址已保存')
  } catch (e) {
    ElMessage.error(e.message || '保存失败')
  } finally {
    savingCloudUrl.value = false
  }
}

const syncCardLoading = ref(false)

const syncInfo = ref(null)      // GET /api/sync/status 响应
const syncWorkspaces = ref([])  // 登录后可选的工作空间列表
const selectedWorkspaceId = ref('')

const loadSyncInfo = async () => {
  try {
    syncInfo.value = await getSyncStatus()
    // 已登录时拉取工作空间列表（覆盖登录时的缓存，确保新建空间后能立即出现）
    if (syncInfo.value?.logged_in) {
      const res = await listCloudWorkspaces()
      syncWorkspaces.value = res.workspaces || []
    }
  } catch {
    // 静默失败
  }
}

const onWorkspaceSelectChange = (id) => {
  selectedWorkspaceId.value = id
}

const handleBindWorkspace = async () => {
  const ws = syncWorkspaces.value.find(w => w.id === selectedWorkspaceId.value)
  if (!ws) {
    ElMessage.warning('请先选择工作空间')
    return
  }
  try {
    await bindWorkspace(ws.id, ws.name)
    ElMessage.success(`已绑定工作空间：${ws.name}`)
    await loadSyncInfo()
    appStore.fetchSyncStatus()
  } catch (e) {
    ElMessage.error(e.message || '绑定失败')
  }
}


onMounted(() => {
  loadSettings()
  loadLibraryInfo()
  loadSyncInfo()
})

// ===== 数据库云端备份 =====

const handleCreateBackup = async () => {
  if (!authStore.isLoggedIn) {
    ElMessage.warning('请先登录云服务后再备份')
    return
  }
  backupCreating.value = true
  try {
    const result = await createBackup()
    ElMessage.success(`备份成功：${result.filename}`)
  } catch (e) {
    ElMessage.error(e.message || '备份失败')
  } finally {
    backupCreating.value = false
  }
}

const handleOpenRestoreDialog = async () => {
  if (!authStore.isLoggedIn) {
    ElMessage.warning('请先登录云服务后再恢复')
    return
  }
  backupDialogVisible.value = true
  backupListLoading.value = true
  try {
    const result = await listBackups()
    backupList.value = result.backups || []
  } catch (e) {
    ElMessage.error(e.message || '获取备份列表失败')
  } finally {
    backupListLoading.value = false
  }
}

const handleRestore = async (backup) => {
  // 先检查本地是否有现有数据
  backupRestoring.value = true
  try {
    const check = await restoreBackup(backup.id, false)
    if (check.has_existing_data) {
      backupRestoring.value = false
      try {
        await ElMessageBox.confirm(
          '本地已有数据库数据，恢复将以云端备份覆盖本地数据，是否继续？',
          '覆盖确认',
          { type: 'warning', confirmButtonText: '覆盖恢复', cancelButtonText: '取消' }
        )
      } catch {
        return
      }
      backupRestoring.value = true
    }
    await restoreBackup(backup.id, true)
    backupDialogVisible.value = false
    ElMessage.success('恢复成功，请重启应用使数据生效')
  } catch (e) {
    ElMessage.error(e.message || '恢复失败')
  } finally {
    backupRestoring.value = false
  }
}

const handleDeleteBackup = async (backup) => {
  try {
    await ElMessageBox.confirm(
      `确定删除备份「${backup.filename}」？此操作不可恢复。`,
      '删除备份',
      { type: 'warning', confirmButtonText: '确定删除', cancelButtonText: '取消' }
    )
  } catch {
    return
  }
  try {
    await deleteBackup(backup.id)
    backupList.value = backupList.value.filter(b => b.id !== backup.id)
    ElMessage.success('备份已删除')
  } catch (e) {
    ElMessage.error(e.message || '删除失败')
  }
}

function formatBackupSize(bytes) {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let i = 0, val = bytes
  while (val >= 1024 && i < units.length - 1) { val /= 1024; i++ }
  return `${val.toFixed(1)} ${units[i]}`
}

function formatBackupTime(isoStr) {
  if (!isoStr) return ''
  const d = new Date(isoStr)
  return d.toLocaleString('zh-CN', { hour12: false })
}
</script>

<style scoped>
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
