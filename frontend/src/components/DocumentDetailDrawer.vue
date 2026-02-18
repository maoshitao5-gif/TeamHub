<template>
  <el-drawer
    :model-value="modelValue"
    @update:model-value="$emit('update:modelValue', $event)"
    title="文档详情"
    size="520px"
    :destroy-on-close="true"
  >
    <template v-if="doc">
      <!-- 头部：名称和描述 -->
      <div class="detail-section">
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
            <span class="path-text">{{ doc.storage_path || doc.original_path || '-' }}</span>
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

      <!-- 版本历史 -->
      <div class="detail-section">
        <h4 class="section-title">版本历史</h4>
        <div v-loading="versionLoading">
          <div v-if="versions.length > 0" class="version-list">
            <div v-for="ver in versions" :key="ver.id" class="version-item">
              <div class="version-header">
                <span class="version-number">v{{ ver.version_number }}</span>
                <el-tag v-if="ver.is_current" size="small" type="success">当前版本</el-tag>
              </div>
              <div class="version-meta">
                <div>{{ ver.original_filename }}</div>
                <div>{{ formatFileSize(ver.file_size) }} · {{ formatDateTime(ver.created_at) }}</div>
                <div v-if="ver.note" class="version-note">{{ ver.note }}</div>
              </div>
              <div class="version-actions" v-if="!ver.is_current">
                <el-button size="small" text type="primary" @click="handleRestoreVersion(ver)">恢复此版本</el-button>
                <el-button size="small" text type="danger" @click="handleDeleteVersion(ver)">删除</el-button>
              </div>
            </div>
          </div>
          <div v-else class="empty-hint">暂无版本记录</div>
        </div>
      </div>

      <!-- 添加版本 -->
      <div class="detail-section" v-if="!doc.is_folder">
        <div class="add-version-row">
          <el-input v-model="newVersionPath" placeholder="新版本文件路径" size="small" style="flex: 1;">
            <template #append>
              <el-button @click="selectVersionFile" size="small">浏览</el-button>
            </template>
          </el-input>
          <el-button type="primary" size="small" @click="handleAddVersion" :loading="addVersionLoading" :disabled="!newVersionPath">
            添加版本
          </el-button>
        </div>
      </div>

      <el-divider />

      <!-- 操作栏 -->
      <div class="detail-actions">
        <el-button type="primary" @click="handleOpen">
          <el-icon><FolderOpened /></el-icon>
          打开文件
        </el-button>
        <el-button @click="handleLocate">
          <el-icon><Position /></el-icon>
          定位到文件夹
        </el-button>
        <el-button type="danger" @click="handleDeleteDoc">
          <el-icon><Delete /></el-icon>
          删除
        </el-button>
      </div>
    </template>
  </el-drawer>
</template>

<script setup>
import { ref, watch, nextTick } from 'vue'
import {
  updateDocument, updateDocumentTags, deleteDocument,
  getVersions, addVersion, restoreVersion, deleteVersion,
  getDocumentFiles
} from '@/api/document'
import { getTags } from '@/api/tag'
import { useAppStore } from '@/stores/app'
import { ElMessage, ElMessageBox } from 'element-plus'
import { formatFileSize, formatDateTime } from '@/utils/format'
import { Edit, Folder, Document, FolderOpened, Delete, Position } from '@element-plus/icons-vue'

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

// 版本
const versions = ref([])
const versionLoading = ref(false)
const newVersionPath = ref('')
const addVersionLoading = ref(false)

// 文件夹内容树
const folderTree = ref([])
const folderTreeLoading = ref(false)

const storageModeLabel = ref('')
const storageModeMap = { move: '移动', copy: '复制', index: '仅索引' }

watch(() => props.document, async (newDoc) => {
  if (newDoc) {
    doc.value = { ...newDoc }
    editTags.value = newDoc.tags?.map(t => t.name) || []
    tagsChanged.value = false
    storageModeLabel.value = storageModeMap[newDoc.storage_mode] || newDoc.storage_mode
    editingName.value = false
    editingDesc.value = false
    newVersionPath.value = ''

    // 加载标签列表
    try { allTags.value = await getTags() } catch { allTags.value = [] }

    // 加载版本
    loadVersions()

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
    const updated = await updateDocument(doc.value.id, { name: editName.value })
    doc.value.name = updated.name
    editingName.value = false
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
    const updated = await updateDocument(doc.value.id, { description: editDesc.value })
    doc.value.description = updated.description
    editingDesc.value = false
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
    emit('updated')
  } catch (e) {
    ElMessage.error(e.message || '更新标签失败')
  } finally {
    tagSaving.value = false
  }
}

const loadVersions = async () => {
  if (!doc.value) return
  versionLoading.value = true
  try {
    versions.value = await getVersions(doc.value.id)
  } catch {
    versions.value = []
  } finally {
    versionLoading.value = false
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

const handleRestoreVersion = async (ver) => {
  try {
    await restoreVersion(doc.value.id, ver.id)
    ElMessage.success(`已恢复到 v${ver.version_number}`)
    loadVersions()
    emit('updated')
  } catch (e) {
    ElMessage.error(e.message || '恢复失败')
  }
}

const handleDeleteVersion = async (ver) => {
  try {
    await ElMessageBox.confirm(`确定删除版本 v${ver.version_number}？`, '确认', { type: 'warning' })
    await deleteVersion(doc.value.id, ver.id)
    ElMessage.success('版本已删除')
    loadVersions()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error(e.message || '删除失败')
  }
}

const selectVersionFile = async () => {
  if (window.electron?.selectFile) {
    const path = await window.electron.selectFile()
    if (path) newVersionPath.value = path
  } else {
    ElMessage.info('请手动输入路径（Electron 环境下可浏览选择）')
  }
}

const handleAddVersion = async () => {
  if (!newVersionPath.value) return
  addVersionLoading.value = true
  try {
    await addVersion(doc.value.id, { file_path: newVersionPath.value })
    ElMessage.success('新版本已添加')
    newVersionPath.value = ''
    loadVersions()
    emit('updated')
  } catch (e) {
    ElMessage.error(e.message || '添加版本失败')
  } finally {
    addVersionLoading.value = false
  }
}

const handleOpen = () => {
  if (!window.electron?.openPath) {
    ElMessage.info('此功能仅在 Electron 桌面应用中可用')
    return
  }
  let fullPath = null
  if (doc.value.storage_path && appStore.libraryPath) {
    fullPath = `${appStore.libraryPath}\\${doc.value.storage_path}`
  } else if (doc.value.original_path) {
    fullPath = doc.value.original_path
  }
  if (!fullPath) {
    ElMessage.warning('该文档没有可用的文件路径')
    return
  }
  window.electron.openPath(fullPath).then(errMsg => {
    if (errMsg) ElMessage.error(`打开失败: ${errMsg}`)
  })
}

const handleLocate = () => {
  if (!window.electron?.showItemInFolder) {
    ElMessage.info('此功能仅在 Electron 桌面应用中可用')
    return
  }
  let fullPath = null
  if (doc.value.storage_path && appStore.libraryPath) {
    fullPath = `${appStore.libraryPath}\\${doc.value.storage_path}`
  } else if (doc.value.original_path) {
    fullPath = doc.value.original_path
  }
  if (fullPath) {
    window.electron.showItemInFolder(fullPath)
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

.detail-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
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
