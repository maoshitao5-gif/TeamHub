<template>
  <el-drawer
    :model-value="modelValue"
    @update:model-value="$emit('update:modelValue', $event)"
    title="文档详情"
    size="520px"
    :destroy-on-close="true"
  >
    <template v-if="doc">
      <!-- 标题行：文件名（可编辑） -->
      <div class="detail-section" style="margin-bottom: 0;">
        <div class="detail-name-row">
          <template v-if="!editingName">
            <h3 class="detail-name" @click="startEditName">{{ doc.name }}</h3>
            <el-button text size="small" @click="startEditName">
              <el-icon><Edit /></el-icon>
            </el-button>
          </template>
          <template v-else>
            <el-input
              v-model="editName"
              size="default"
              @keyup.enter="saveName"
              @blur="saveName"
              ref="nameInputRef"
              style="flex: 1"
            />
          </template>
        </div>
      </div>

      <!-- 操作栏（紧接标题，顶部可见，左：文件访问；右：状态管理） -->
      <div class="detail-actions">
        <div class="detail-actions-left">
          <el-button plain size="small" @click="handleOpen" :disabled="doc.status === 'missing'">
            <el-icon><FolderOpened /></el-icon>
            打开文件
          </el-button>
          <el-button plain size="small" @click="handleLocate" :disabled="doc.status === 'missing'">
            <el-icon><Position /></el-icon>
            定位到文件夹
          </el-button>
        </div>
        <div class="detail-actions-right">
          <el-button
            v-if="doc && doc.status === 'organized'"
            type="warning"
            plain
            size="small"
            @click="handleMoveToPending"
          >
            <el-icon><Back /></el-icon>
            移回待整理
          </el-button>
          <el-button
            v-if="doc.status !== 'missing'"
            type="danger"
            text
            size="small"
            @click="handleDeleteDoc"
          >
            <el-icon><Delete /></el-icon>
            删除
          </el-button>
        </div>
      </div>

      <!-- 文件缺失横幅 -->
      <el-alert
        v-if="doc.status === 'missing'"
        type="error"
        :closable="false"
        style="margin-bottom: 12px;"
      >
        <template #title>
          <div style="display:flex; align-items:center; gap:6px;">
            <el-icon><WarningFilled /></el-icon>
            <span>文件已缺失</span>
          </div>
        </template>
        <div>该文件可能已被移动或从磁盘删除，TeamHub 无法打开或定位它。</div>
        <div style="margin-top:8px; display:flex; gap:8px; flex-wrap:wrap;">
          <el-button v-if="!isMissingFromPending" size="small" type="primary" @click="handleRelocateMissing">
            重新定位文件
          </el-button>
          <el-button size="small" type="danger" plain @click="handleRemoveMissingDoc">
            永久移除记录
          </el-button>
        </div>
      </el-alert>

      <!-- 描述区域 -->
      <div class="detail-section">
        <div class="detail-desc-row">
          <template v-if="!editingDesc">
            <p class="detail-desc" @click="startEditDesc">
              {{ doc.description || '点击添加描述...' }}
            </p>
          </template>
          <template v-else>
            <el-input
              v-model="editDesc"
              type="textarea"
              :rows="2"
              @blur="saveDesc"
              ref="descInputRef"
            />
            <el-button size="small" type="primary" @click="saveDesc" style="margin-top: 4px;">保存</el-button>
          </template>
        </div>
      </div>

      <el-divider />

      <!-- 基本信息 -->
      <div class="detail-section">
        <h4 class="section-title">基本信息</h4>
        <el-descriptions :column="1" size="small" border>
          <el-descriptions-item label="类型">{{ doc.is_folder ? '文件夹' : '文件' }}</el-descriptions-item>
          <el-descriptions-item label="存储方式">{{ storageModeLabel }}</el-descriptions-item>
          <el-descriptions-item label="大小">{{ formatFileSize(doc.total_size) }}</el-descriptions-item>
          <el-descriptions-item v-if="doc.is_folder" label="文件数">{{ doc.file_count }} 个</el-descriptions-item>
          <el-descriptions-item label="路径">
            <span class="path-text">{{ getFullPath() || '-' }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ formatDateTime(doc.created_at) }}</el-descriptions-item>
          <el-descriptions-item label="更新时间">{{ formatDateTime(doc.updated_at) }}</el-descriptions-item>
        </el-descriptions>
      </div>

      <el-divider />

      <!-- 标签编辑 -->
      <div class="detail-section">
        <h4 class="section-title">标签</h4>
        <div class="tag-edit-row">
          <el-select
            v-model="editTags"
            multiple
            filterable
            allow-create
            default-first-option
            placeholder="选择或输入标签"
            style="width: 100%"
            @change="tagsChanged = true"
          >
            <el-option v-for="tag in allTags" :key="tag.id" :label="tag.name" :value="tag.name" />
          </el-select>
          <el-button
            v-if="tagsChanged"
            type="primary"
            size="small"
            @click="saveTags"
            :loading="tagSaving"
            style="margin-top: 8px;"
          >
            保存标签
          </el-button>
        </div>
      </div>

      <el-divider />

      <!-- 文件夹内容（仅文件夹显示） -->
      <div class="detail-section" v-if="doc.is_folder">
        <h4 class="section-title">文件夹内容</h4>
        <div v-if="folderTreeLoading" v-loading="true" style="min-height: 100px;"></div>
        <el-tree
          v-else-if="folderTree.length > 0"
          :data="folderTree"
          :props="{ children: 'children', label: 'name' }"
          default-expand-all
          :expand-on-click-node="false"
          @node-click="handleTreeNodeClick"
        >
          <template #default="{ data }">
            <span class="tree-node">
              <el-icon :size="14" :color="data.type === 'directory' ? '#e67e22' : '#3498db'">
                <Folder v-if="data.type === 'directory'" />
                <Document v-else />
              </el-icon>
              <span class="tree-node-name">{{ data.name }}</span>
              <span v-if="data.size" class="tree-node-size">{{ formatFileSize(data.size) }}</span>
            </span>
          </template>
        </el-tree>
        <div v-else class="empty-hint">{{ doc.file_count }} 个文件</div>
      </div>

      <el-divider v-if="doc.is_folder" />

      <!-- 云端版本历史（需已关联工作空间） -->
      <div class="detail-section" v-if="doc.cloud_doc_id || doc.workspace_id">
        <h4 class="section-title">云端版本历史</h4>
        <div v-loading="cloudVersionLoading">
          <div v-if="cloudVersions.length > 0" class="version-list">
            <div v-for="ver in cloudVersions" :key="ver.id" class="version-item">
              <div class="version-header">
                <span class="version-number">v{{ ver.version_number }}</span>
                <el-tag v-if="ver.is_current" size="small" type="success">当前</el-tag>
              </div>
              <div class="version-meta">
                <div>{{ formatFileSize(ver.file_size) }} · {{ formatDateTime(ver.created_at) }}</div>
              </div>
            </div>
          </div>
          <div v-else class="empty-hint">暂无云端历史版本</div>
        </div>
      </div>
    </template>
  </el-drawer>
</template>

<script setup>
import { ref, computed, watch, nextTick } from 'vue'
import {
  getDocument, updateDocument, updateDocumentTags, deleteDocument,
  getDocumentFiles, moveToPending, relocateDocument
} from '@/api/document'
import { getTags } from '@/api/tag'
import { useAppStore } from '@/stores/app'
import { ElMessage, ElMessageBox } from 'element-plus'
import { h } from 'vue'
import { formatFileSize, formatDateTime } from '@/utils/format'
import { Edit, Folder, Document, FolderOpened, Delete, Position, Back, WarningFilled } from '@element-plus/icons-vue'
import cloudRequest from '@/api/cloud'

const props = defineProps({
  modelValue: Boolean,
  document: Object,
})
const emit = defineEmits(['update:modelValue', 'updated', 'deleted'])

const appStore = useAppStore()

// 当前文档的副本
const doc = ref(null)

// 名称编辑
const editingName = ref(false)
const editName = ref('')
const nameInputRef = ref(null)

// 描述编辑
const editingDesc = ref(false)
const editDesc = ref('')
const descInputRef = ref(null)

// 标签
const allTags = ref([])
const editTags = ref([])
const tagsChanged = ref(false)
const tagSaving = ref(false)

// 云端版本历史
const cloudVersions = ref([])
const cloudVersionLoading = ref(false)

// 文件夹内容树
const folderTree = ref([])
const folderTreeLoading = ref(false)

// 云端同步操作

const storageModeLabel = ref('')
const storageModeMap = { move: '移动', copy: '复制', index: '仅索引' }

// 从 API 重新获取文档最新数据
const refreshDoc = async () => {
  if (!doc.value?.id) return
  try {
    const latest = await getDocument(doc.value.id)
    console.log('[Drawer] refreshDoc API response:', { id: latest.id, storage_path: latest.storage_path, status: latest.status })
    applyDocData(latest)
  } catch (e) {
    console.error('刷新文档详情失败:', e)
  }
}

// 将文档数据应用到组件状态
const applyDocData = (docData) => {
  console.log('[Drawer] applyDocData:', { id: docData.id, name: docData.name, storage_path: docData.storage_path, status: docData.status, fullPath: (docData.storage_path && appStore.libraryPath) ? `${appStore.libraryPath}/${docData.storage_path}` : docData.original_path })
  doc.value = { ...docData }
  editTags.value = docData.tags?.map(t => t.name) || []
  tagsChanged.value = false
  storageModeLabel.value = storageModeMap[docData.storage_mode] || docData.storage_mode
  editingName.value = false
  editingDesc.value = false
}

// 当 drawer 打开时，重新拉取最新数据
watch(() => props.modelValue, async (visible) => {
  if (visible && doc.value?.id) {
    await refreshDoc()
    loadCloudVersions()
    if (doc.value.is_folder) loadFolderTree()
  }
})

// 初始化加载：当传入文档变化时
watch(() => props.document, async (newDoc) => {
  if (newDoc) {
    // 先用 prop 数据快速展示
    applyDocData(newDoc)

    // 然后从 API 获取最新数据（确保路径等信息是最新的）
    try {
      const latest = await getDocument(newDoc.id)
      applyDocData(latest)
    } catch {
      // API 获取失败则沿用 prop 数据
    }

    // 加载标签列表
    try { allTags.value = await getTags() } catch { allTags.value = [] }

    // 加载云端版本历史
    loadCloudVersions()

    // 文件夹则加载文件树
    if (newDoc.is_folder) {
      loadFolderTree()
    } else {
      folderTree.value = []
    }
  }
}, { immediate: true })

const startEditName = () => {
  editName.value = doc.value.name
  editingName.value = true
  nextTick(() => nameInputRef.value?.focus())
}

const saveName = async () => {
  if (!editName.value || editName.value === doc.value.name) {
    editingName.value = false
    return
  }
  try {
    await updateDocument(doc.value.id, { name: editName.value })
    editingName.value = false
    await refreshDoc()
    emit('updated')
  } catch (e) {
    ElMessage.error(e.message || '更新名称失败')
  }
}

const startEditDesc = () => {
  editDesc.value = doc.value.description || ''
  editingDesc.value = true
  nextTick(() => descInputRef.value?.focus())
}

const saveDesc = async () => {
  try {
    await updateDocument(doc.value.id, { description: editDesc.value })
    editingDesc.value = false
    await refreshDoc()
    emit('updated')
  } catch (e) {
    ElMessage.error(e.message || '更新描述失败')
  }
}

const saveTags = async () => {
  tagSaving.value = true
  try {
    await updateDocumentTags(doc.value.id, editTags.value)
    tagsChanged.value = false
    ElMessage.success('标签已更新')
    await refreshDoc()
    emit('updated')
  } catch (e) {
    ElMessage.error(e.message || '更新标签失败')
  } finally {
    tagSaving.value = false
  }
}

const loadCloudVersions = async () => {
  if (!doc.value) return
  // 仅已关联工作空间或从云仓库导入的文档才查云端
  if (!doc.value.cloud_doc_id && !doc.value.workspace_id) return
  const wsId = doc.value.workspace_id || appStore.syncStatus?.workspace_id
  if (!wsId) return
  cloudVersionLoading.value = true
  try {
    const res = await cloudRequest.get(`/api/workspaces/${wsId}/documents/${doc.value.id}/versions`)
    cloudVersions.value = res.data || []
  } catch {
    cloudVersions.value = []
  } finally {
    cloudVersionLoading.value = false
  }
}

const loadFolderTree = async () => {
  if (!doc.value) return
  folderTreeLoading.value = true
  try {
    const data = await getDocumentFiles(doc.value.id)
    folderTree.value = data || []
  } catch {
    folderTree.value = []
  } finally {
    folderTreeLoading.value = false
  }
}

const handleTreeNodeClick = (data) => {
  if (data.type === 'file' && data.path && window.electron?.openPath) {
    window.electron.openPath(data.path)
  }
}

const getFullPath = () => {
  if (doc.value.storage_path && appStore.libraryPath) {
    // 规范化路径：先统一使用反斜杠，然后替换为正确的格式
    const libPath = appStore.libraryPath.replace(/\//g, '\\').replace(/\\+$/, '') // 移除末尾的反斜杠
    const storagePath = doc.value.storage_path.replace(/\//g, '\\')
    const fullPath = `${libPath}\\${storagePath}`
    return fullPath
  }
  if (doc.value.original_path) {
    // 对于 original_path，保持原样（已经是正确的 Windows 路径格式）
    return doc.value.original_path
  }
  return null
}

const handleOpen = () => {
  const fullPath = getFullPath()
  if (!fullPath) {
    ElMessage.warning('该文档没有可用的文件路径')
    return
  }
  if (window.electron?.openPath) {
    window.electron.openPath(fullPath).then(errMsg => {
      if (errMsg) ElMessage.error(`打开失败: ${errMsg}`)
    })
  } else {
    navigator.clipboard.writeText(fullPath).then(() => {
      ElMessage.success(`文件路径已复制: ${fullPath}`)
    }).catch(() => {
      ElMessage.info(`文件路径: ${fullPath}`)
    })
  }
}

const handleLocate = () => {
  const fullPath = getFullPath()
  if (!fullPath) {
    ElMessage.warning('该文档没有可用的文件路径')
    return
  }
  
  if (window.electron?.showItemInFolder) {
    // getFullPath() 现在返回的是 Windows 格式路径（使用 \），直接使用即可
    window.electron.showItemInFolder(fullPath)
  } else {
    // Web 模式：取文件夹路径并复制
    // 对于 Windows 路径，使用 \ 分隔符；对于 Unix 路径，使用 / 分隔符
    const separator = fullPath.includes('\\') ? '\\' : '/'
    const folderPath = fullPath.substring(0, fullPath.lastIndexOf(separator)) || fullPath
    navigator.clipboard.writeText(folderPath).then(() => {
      ElMessage.success(`文件夹路径已复制: ${folderPath}`)
    }).catch(() => {
      ElMessage.info(`文件夹路径: ${folderPath}`)
    })
  }
}

const handleMoveToPending = async () => {
  try {
    await ElMessageBox.confirm(
      `确定要将"${doc.value.name}"移回待整理区吗？文件将被移动到"待整理"目录，方便您进行二次整理。`,
      '确认移回待整理',
      { type: 'warning' }
    )
    
    await moveToPending(doc.value.id)
    
    ElMessage.success('已移回待整理区')
    emit('update:modelValue', false)
    emit('updated')
    appStore.fetchPendingCount()
  } catch (e) {
    if (e !== 'cancel') {
      // 如果错误消息包含换行符，使用MessageBox显示，否则使用Message
      const errorMsg = e.message || '移回待整理失败'
      
      if (errorMsg.includes('\n')) {
        // 使用 MessageBox 显示多行错误消息
        // 使用 VNode 创建多行文本，通过 CSS 样式保留换行符
        const messageContent = h('div', { style: { whiteSpace: 'pre-line' } }, errorMsg)
        ElMessageBox.alert(messageContent, '移回待整理失败', { 
          type: 'error'
        }).catch(() => {})
      } else {
        ElMessage.error(errorMsg)
      }
    }
  }
}

const isMissingFromPending = computed(() => {
  const p = doc.value?.storage_path || ''
  return p.startsWith('待整理/') || p.startsWith('待整理\\')
})

const handleRelocateMissing = async () => {
  let newPath = null
  if (window.electron?.selectFile) {
    newPath = await window.electron.selectFile()
  } else {
    try {
      const { value } = await ElMessageBox.prompt('请输入文件新路径', '重新定位', {
        confirmButtonText: '确定', cancelButtonText: '取消',
      })
      newPath = value
    } catch { return }
  }
  if (!newPath) return
  try {
    await relocateDocument(doc.value.id, newPath)
    ElMessage.success('文件已重新定位')
    await refreshDoc()
    emit('updated')
  } catch (e) {
    ElMessage.error(e.message || '重新定位失败')
  }
}

const handleRemoveMissingDoc = async () => {
  try {
    await ElMessageBox.confirm(
      `文件"${doc.value.name}"已从磁盘缺失，确定永久移除该记录？`,
      '确认移除', { type: 'error' }
    )
    await deleteDocument(doc.value.id, true)
    ElMessage.success('已移除')
    emit('update:modelValue', false)
    emit('deleted')
  } catch (e) {
    if (e !== 'cancel') ElMessage.error(e.message || '移除失败')
  }
}

const handleDeleteDoc = async () => {
  try {
    await ElMessageBox.confirm(`确定要删除"${doc.value.name}"吗？`, '确认删除', { type: 'warning' })
    await deleteDocument(doc.value.id)
    ElMessage.success('已移入回收站')
    emit('update:modelValue', false)
    emit('deleted')
    appStore.fetchPendingCount()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error(e.message || '删除失败')
  }
}
</script>

<style scoped>
.detail-section {
  margin-bottom: 8px;
}

.detail-name-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.detail-name {
  font-size: 18px;
  font-weight: 600;
  color: #2c3e50;
  margin: 0;
  cursor: pointer;
}

.detail-name:hover {
  color: #409EFF;
}

.detail-desc-row {
  margin-top: 8px;
}

.detail-desc {
  font-size: 14px;
  color: #7f8c8d;
  margin: 0;
  cursor: pointer;
  min-height: 20px;
}

.detail-desc:hover {
  color: #409EFF;
}

.section-title {
  font-size: 14px;
  font-weight: 600;
  color: #2c3e50;
  margin: 0 0 10px 0;
}

.path-text {
  font-size: 12px;
  word-break: break-all;
  color: #7f8c8d;
}

.tag-edit-row {
  display: flex;
  flex-direction: column;
}

.version-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.version-item {
  padding: 12px 14px;
  background: #fafbfc;
  border-radius: 8px;
  border: 1px solid #e4e7ed;
}

.version-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.version-number {
  font-weight: 600;
  font-size: 14px;
  color: #2c3e50;
}

.version-meta {
  font-size: 12px;
  color: #95a5a6;
  line-height: 1.6;
}

.version-note {
  margin-top: 2px;
  color: #7f8c8d;
  font-style: italic;
}

.version-actions {
  margin-top: 6px;
  display: flex;
  gap: 8px;
}

.add-version-row {
  display: flex;
  gap: 8px;
  align-items: center;
}

/* 操作栏（顶部，紧接标题行，替代原来的底部固定栏） */
.detail-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 0 12px;
  margin-bottom: 4px;
  border-bottom: 1px solid #f0f2f5;
}

.detail-actions-left {
  display: flex;
  gap: 8px;
}

.detail-actions-right {
  display: flex;
  gap: 8px;
}

.empty-hint {
  font-size: 13px;
  color: #bdc3c7;
  text-align: center;
  padding: 20px 0;
}

.tree-node {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
}

.tree-node-name {
  color: #2c3e50;
}

.tree-node-size {
  color: #bdc3c7;
  font-size: 12px;
  margin-left: 4px;
}
</style>
