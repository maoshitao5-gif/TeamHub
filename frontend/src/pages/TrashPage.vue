<template>
  <div class="trash-page">
    <div class="page-header">
      <h2>回收站 <span v-if="total > 0" class="count-badge">{{ total }}</span></h2>
      <div class="header-actions">
        <el-button size="small" @click="fetchTrash" :loading="loading">
          <el-icon><Refresh /></el-icon>
          刷新
        </el-button>
        <el-button
          size="small"
          type="danger"
          :disabled="documents.length === 0"
          @click="handleEmptyTrash"
        >
          清空回收站
        </el-button>
      </div>
    </div>

    <!-- 批量操作栏（底部固定，与 PendingPage 统一） -->
    <div class="batch-bar" v-if="selectedIds.length > 0">
      <span>已选 {{ selectedIds.length }} 项</span>
      <el-button size="small" type="primary" @click="handleBatchRestore">批量恢复</el-button>
      <el-button size="small" type="danger" @click="handleBatchDelete">批量永久删除</el-button>
    </div>

    <!-- 文档列表 -->
    <div class="trash-list" v-loading="loading">
      <template v-if="documents.length > 0">
        <div
          v-for="doc in documents"
          :key="doc.id"
          class="trash-card"
        >
          <el-checkbox v-model="doc._selected" class="card-checkbox" />
          <div class="doc-icon">
            <el-icon :size="22" :color="doc.is_folder ? '#e67e22' : getFileTypeColor(doc.name)">
              <Folder v-if="doc.is_folder" />
              <component v-else :is="fileIconComponent(doc.name)" />
            </el-icon>
          </div>
          <div class="doc-info">
            <div class="doc-name">{{ doc.name }}</div>
            <div class="doc-path" v-if="doc.storage_path">
              <span class="path-label">原位置：</span>{{ doc.storage_path }}
            </div>
          </div>
          <div class="doc-meta">
            <span v-if="doc.is_folder">{{ doc.file_count }} 个文件</span>
            <span v-else>{{ formatFileSize(doc.total_size) }}</span>
            <span class="meta-sep">·</span>
            <span>删除于 {{ formatDateTime(doc.trashed_at || doc.updated_at) }}</span>
          </div>
          <div class="doc-actions">
            <el-button size="small" type="primary" @click="handleRestore(doc)">恢复</el-button>
            <el-button size="small" type="danger" text @click="handlePermanentDelete(doc)">
              永久删除
            </el-button>
          </div>
        </div>
      </template>

      <div v-else-if="!loading" class="empty-state">
        <el-icon :size="64" color="#bdc3c7"><Delete /></el-icon>
        <p>回收站为空</p>
        <p class="empty-hint">删除的文档会在这里，可以恢复或永久删除</p>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import {
  searchDocuments, restoreDocument, deleteDocument,
  batchDeleteDocuments, batchRestoreDocuments
} from '@/api/document'
import { ElMessage, ElMessageBox } from 'element-plus'
import { formatFileSize, formatDateTime } from '@/utils/format'
import { Folder, Document, Delete, Refresh, Grid, DataBoard, Picture, VideoCamera, Headset, Box, Notebook } from '@element-plus/icons-vue'
import { getFileTypeIcon, getFileTypeColor } from '@/utils/fileIcons'

const iconComponents = { Document, Grid, DataBoard, Picture, VideoCamera, Headset, Box, Notebook }
const fileIconComponent = (filename) => {
  const iconName = getFileTypeIcon(filename)
  return iconComponents[iconName] || Document
}

const documents = ref([])
const loading = ref(false)
const total = ref(0)

const selectedIds = computed(() =>
  documents.value.filter(d => d._selected).map(d => d.id)
)

const fetchTrash = async () => {
  loading.value = true
  try {
    const data = await searchDocuments({
      status: 'trashed',
      sort_by: 'updated_at',
      sort_order: 'desc',
      page: 1,
      page_size: 100,
    })
    documents.value = data.documents.map(d => ({ ...d, _selected: false }))
    total.value = data.total
  } catch (e) {
    console.error('获取回收站列表失败:', e)
  } finally {
    loading.value = false
  }
}

const handleRestore = async (doc) => {
  try {
    await restoreDocument(doc.id)
    ElMessage.success(`"${doc.name}" 已恢复`)
    fetchTrash()
  } catch (e) {
    ElMessage.error(e.message || '恢复失败')
  }
}

const handlePermanentDelete = async (doc) => {
  try {
    await ElMessageBox.confirm(
      `确定要永久删除"${doc.name}"吗？此操作不可恢复！`,
      '永久删除',
      { confirmButtonText: '永久删除', cancelButtonText: '取消', type: 'error' }
    )
    await deleteDocument(doc.id, true)
    ElMessage.success('已永久删除')
    fetchTrash()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error(e.message || '删除失败')
  }
}

const handleBatchRestore = async () => {
  try {
    await batchRestoreDocuments(selectedIds.value)
    ElMessage.success(`已恢复 ${selectedIds.value.length} 个文档`)
    fetchTrash()
  } catch (e) {
    ElMessage.error(e.message || '批量恢复失败')
  }
}

const handleBatchDelete = async () => {
  try {
    await ElMessageBox.confirm(
      `确定要永久删除选中的 ${selectedIds.value.length} 个文档吗？此操作不可恢复！`,
      '批量永久删除',
      { confirmButtonText: '永久删除', cancelButtonText: '取消', type: 'error' }
    )
    await batchDeleteDocuments(selectedIds.value)
    ElMessage.success('已永久删除')
    fetchTrash()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error(e.message || '批量删除失败')
  }
}

const handleEmptyTrash = async () => {
  try {
    await ElMessageBox.confirm(
      '确定要清空回收站吗？所有文档将被永久删除，此操作不可恢复！',
      '清空回收站',
      { confirmButtonText: '清空', cancelButtonText: '取消', type: 'error' }
    )
    const allIds = documents.value.map(d => d.id)
    await batchDeleteDocuments(allIds)
    ElMessage.success('回收站已清空')
    fetchTrash()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error(e.message || '清空失败')
  }
}

onMounted(() => {
  fetchTrash()
})
</script>

<style scoped>
.trash-page {
  width: 100%;
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 24px;
}

.page-header h2 {
  font-size: 22px;
  color: #2c3e50;
}

.header-actions {
  display: flex;
  gap: 8px;
}

.count-badge {
  background: #e74c3c;
  color: #fff;
  font-size: 14px;
  padding: 2px 10px;
  border-radius: 12px;
  margin-left: 8px;
  font-weight: 500;
}

.batch-bar {
  position: fixed;
  bottom: 0;
  left: 0;
  right: 0;
  background: #2c3e50;
  color: #fff;
  padding: 12px 32px;
  border-radius: 0;
  display: flex;
  align-items: center;
  gap: 16px;
  box-shadow: 0 -4px 20px rgba(0, 0, 0, 0.15);
  z-index: 100;
  font-size: 14px;
}

.trash-list {
  min-height: 300px;
}

.trash-card {
  display: flex;
  align-items: center;
  padding: 9px 16px;
  background: #fff;
  border-radius: 8px;
  margin-bottom: 5px;
  border: 1px solid #ebeef5;
  box-shadow: var(--shadow-sm);
  transition: var(--transition-base);
}

.trash-card:hover {
  box-shadow: inset 3px 0 0 #e74c3c, var(--shadow-hover);
  transform: translateY(-2px);
  border-color: #fde8e8;
}

.card-checkbox {
  margin-right: 10px;
}

.doc-icon {
  margin-right: 12px;
  flex-shrink: 0;
}

.doc-info {
  flex: 1;
  min-width: 0;
}

.doc-name {
  font-size: 14px;
  font-weight: 500;
  color: #2c3e50;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.doc-meta {
  font-size: 12px;
  color: #aab2bd;
  white-space: nowrap;
  flex-shrink: 0;
  margin: 0 14px;
}

.meta-sep {
  margin: 0 5px;
}

.doc-path {
  font-size: 12px;
  color: #bdc3c7;
  margin-top: 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.path-label {
  color: #95a5a6;
}

.doc-actions {
  display: flex;
  gap: 6px;
  margin-left: 4px;
  flex-shrink: 0;
}

.empty-state {
  text-align: center;
  padding: 80px 20px;
  color: #95a5a6;
}

.empty-state p {
  margin-top: 16px;
  font-size: 16px;
}

.empty-hint {
  font-size: 14px !important;
  color: #bdc3c7 !important;
  margin-top: 8px !important;
}
</style>
