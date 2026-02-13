<template>
  <div class="file-list-container">
    <el-card class="list-card" shadow="hover">
      <template #header>
        <div class="card-header">
          <el-icon><Document /></el-icon>
          <span>文件列表</span>
          <el-text type="info" style="margin-left: 12px;">
            共 {{ total }} 个文件
          </el-text>
          <div style="margin-left: auto; display: flex; gap: 8px;">
            <el-button 
              type="primary" 
              @click="handleSyncStorage" 
              :loading="syncLoading"
              size="small"
            >
              <el-icon><Refresh /></el-icon>
              核对存储
            </el-button>
          </div>
        </div>
      </template>

      <el-table
        ref="tableRef"
        :data="files"
        v-loading="loading"
        stripe
        style="width: 100%"
        :empty-text="loading ? '加载中...' : '暂无文件'"
        @selection-change="handleSelectionChange"
      >
        <!-- 勾选框列 -->
        <el-table-column type="selection" width="55" align="center" />

        <el-table-column prop="original_filename" label="文件名" min-width="200">
          <template #default="{ row }">
            <div class="file-name-cell">
              <el-icon class="file-icon"><Document /></el-icon>
              <span 
                class="file-name previewable"
                @click="handleFileNameClick(row)"
                title="点击直接打开文件（服务器机器）"
              >
                {{ row.original_filename }}
              </span>
            </div>
          </template>
        </el-table-column>

        <el-table-column prop="file_size" label="文件大小" width="120" align="right">
          <template #default="{ row }">
            {{ formatFileSize(row.file_size) }}
          </template>
        </el-table-column>

        <el-table-column prop="tags" label="标签" min-width="200">
          <template #default="{ row }">
            <div class="tags-cell">
              <el-tag
                v-for="tag in row.tags"
                :key="tag"
                size="small"
                style="margin-right: 4px;"
              >
                {{ tag }}
              </el-tag>
              <span v-if="row.tags.length === 0" class="no-tags">无标签</span>
            </div>
          </template>
        </el-table-column>

        <el-table-column prop="upload_time" label="上传时间" width="180">
          <template #default="{ row }">
            {{ formatDateTime(row.upload_time) }}
          </template>
        </el-table-column>

        <el-table-column label="操作" width="240" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="isPreviewable(row)"
              type="info"
              link
              @click="handlePreview(row)"
              :icon="View"
            >
              预览
            </el-button>
            <el-button
              type="info"
              link
              @click="handleReveal(row)"
              :icon="FolderOpened"
            >
              定位
            </el-button>
            <el-button
              type="primary"
              link
              @click="handleDownload(row)"
              :icon="Download"
            >
              下载
            </el-button>
            <el-button
              type="success"
              link
              @click="handleEditTags(row)"
              :icon="PriceTag"
            >
              打标签
            </el-button>
            <el-button
              type="danger"
              link
              @click="handleDelete(row)"
              :icon="Delete"
            >
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 文件预览对话框 -->
    <FilePreview
      v-model="previewVisible"
      :file="previewFile"
      @download="handleDownload"
    />

    <!-- 标签编辑对话框 -->
    <FileTagEditor
      v-model="tagEditorVisible"
      :file="currentFile"
      @saved="handleTagsSaved"
    />

    <!-- 重复文件提示对话框 -->
    <el-dialog
      v-model="duplicateDialogVisible"
      title="发现重复文件"
      width="800px"
    >
      <el-alert
        type="warning"
        :closable="false"
        show-icon
        style="margin-bottom: 20px;"
      >
        <template #title>
          <div style="font-size: 16px; font-weight: 600; margin-bottom: 8px;">
            系统已存在相同内容的文件，这些文件不会被重复添加到数据库
          </div>
        </template>
      </el-alert>

      <el-table :data="duplicateFilesList" stripe style="width: 100%" max-height="400">
        <el-table-column prop="original_filename" label="文件名" min-width="200" show-overflow-tooltip />
        <el-table-column label="文件大小" width="120">
          <template #default="{ row }">
            {{ formatFileSize(row.file_size) }}
          </template>
        </el-table-column>
        <el-table-column label="已存在文件" min-width="200">
          <template #default="{ row }">
            <div v-if="row.existing_file">
              <div style="font-weight: 600; margin-bottom: 4px;">
                {{ row.existing_file.original_filename }}
              </div>
              <el-text type="info" size="small">
                文件ID: #{{ row.existing_file.id }}
              </el-text>
              <div v-if="row.existing_file.tags && row.existing_file.tags.length > 0" style="margin-top: 4px;">
                <el-tag
                  v-for="tag in row.existing_file.tags"
                  :key="tag"
                  type="primary"
                  effect="plain"
                  size="small"
                  style="margin-right: 4px; margin-bottom: 4px;"
                >
                  {{ tag }}
                </el-tag>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="上传时间" width="180">
          <template #default="{ row }">
            <div v-if="row.existing_file && row.existing_file.upload_time">
              {{ formatDateTime(row.existing_file.upload_time) }}
            </div>
            <el-text v-else type="info" size="small">未知</el-text>
          </template>
        </el-table-column>
      </el-table>

      <template #footer>
        <el-button type="primary" @click="duplicateDialogVisible = false">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Document, Download, Delete, PriceTag, View, FolderOpened, Refresh } from '@element-plus/icons-vue'
import { formatFileSize, formatDateTime } from '@/utils/format'
import FilePreview from './FilePreview.vue'
import FileTagEditor from './FileTagEditor.vue'
import { downloadFile, deleteFile, revealFile, openFile } from '@/api/file'
import { syncStorage } from '@/api/admin'

const props = defineProps({
  files: {
    type: Array,
    default: () => []
  },
  loading: {
    type: Boolean,
    default: false
  },
  total: {
    type: Number,
    default: 0
  }
})

const emit = defineEmits(['download', 'delete', 'refresh', 'selection-change'])

// 预览相关
const previewVisible = ref(false)
const previewFile = ref(null)

// 标签编辑相关
const tagEditorVisible = ref(false)
const currentFile = ref(null)

// 选中的文件列表
const selectedFiles = ref([])
const tableRef = ref(null)
const syncLoading = ref(false)

// 判断文件是否可预览（支持 jpg, jpeg, png, gif 和 pdf）
const isPreviewable = (file) => {
  if (!file || !file.original_filename) return false
  const filename = file.original_filename.toLowerCase()
  const imageExts = ['.jpg', '.jpeg', '.png', '.gif']
  const pdfExt = '.pdf'
  
  return imageExts.some(ext => filename.endsWith(ext)) || filename.endsWith(pdfExt)
}

// 处理文件名点击 - 在服务器机器上直接打开文件
const handleFileNameClick = async (row) => {
  try {
    await openFile(row.id)
    ElMessage.success('已打开文件（服务器机器）')
  } catch (error) {
    console.error('打开文件失败:', error)
    // 回退策略：可预览则预览，否则下载
    if (isPreviewable(row)) {
      handlePreview(row)
      ElMessage.info('已回退到预览')
    } else {
      ElMessage.warning('无法自动打开文件（可能不是本机部署或系统限制），已回退到下载')
      await handleDownload(row)
    }
  }
}

// 定位文件（可选操作）
const handleReveal = async (row) => {
  try {
    await revealFile(row.id)
    ElMessage.success('已在文件管理器中定位文件（服务器机器）')
  } catch (error) {
    console.error('定位文件失败:', error)
    ElMessage.warning('无法定位文件（可能不是本机部署或系统限制）')
  }
}

// 预览文件（通过操作栏按钮）
const handlePreview = (row) => {
  previewFile.value = row
  previewVisible.value = true
}

// 处理选择变化
const handleSelectionChange = (selection) => {
  selectedFiles.value = selection
  emit('selection-change', selection)
}

// 下载文件
const handleDownload = async (row) => {
  try {
    await downloadFile(row.id, row.original_filename)
    ElMessage.success('下载成功')
    emit('download', row)
  } catch (error) {
    // 错误信息已在 request.js 的拦截器中显示
    // 这里只记录错误，不重复显示消息
    console.error('下载失败:', error)
  }
}

// 编辑标签
const handleEditTags = (row) => {
  currentFile.value = { ...row }
  tagEditorVisible.value = true
}

// 标签保存后的处理
const handleTagsSaved = (updatedFile) => {
  // 更新文件列表中的标签信息
  const fileIndex = props.files.findIndex(f => f.id === updatedFile.id)
  if (fileIndex !== -1) {
    // 触发父组件刷新文件列表
    emit('refresh')
  }
  ElMessage.success('标签已更新，文件列表已刷新')
}

// 删除文件
const handleDelete = async (row) => {
  try {
    await ElMessageBox.confirm(
      `确定要删除文件 "${row.original_filename}" 吗？此操作不可恢复！`,
      '确认删除',
      {
        confirmButtonText: '确定删除',
        cancelButtonText: '取消',
        type: 'warning',
      }
    )
    
    try {
      await deleteFile(row.id)
      ElMessage.success('删除成功')
      emit('delete', row)
      emit('refresh')
    } catch (error) {
      console.error('删除失败:', error)
      // 错误信息已在 request.js 的拦截器中显示
    }
  } catch {
    // 用户取消删除
  }
}

// 核对存储
const duplicateDialogVisible = ref(false)
const duplicateFilesList = ref([])

const handleSyncStorage = async () => {
  syncLoading.value = true
  try {
    await ElMessageBox.confirm(
      '核对存储将扫描 storage 目录，同步数据库记录。确定要继续吗？',
      '确认核对存储',
      {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'info',
      }
    )
    
    const result = await syncStorage()
    const message = result.message || `存储核对完成：删除 ${result.deleted_count || 0} 个孤立记录，添加 ${result.added_count || 0} 个新文件`
    
    // 如果有重复文件，显示详细信息
    if (result.duplicate_count > 0 && result.duplicate_records && result.duplicate_records.length > 0) {
      duplicateFilesList.value = result.duplicate_records
      duplicateDialogVisible.value = true
      ElMessage.warning(`存储核对完成，但发现 ${result.duplicate_count} 个重复文件`)
    } else {
      ElMessage.success(message)
    }
    
    emit('refresh')
  } catch (error) {
    if (error !== 'cancel') {
      console.error('核对存储失败:', error)
      const errorMessage = error.response?.data?.detail || error.message || '核对存储失败'
      ElMessage.error(errorMessage)
    }
  } finally {
    syncLoading.value = false
  }
}

// 暴露选中的文件列表和清除选择方法供父组件使用
defineExpose({
  selectedFiles: computed(() => selectedFiles.value),
  clearSelection: () => {
    if (tableRef.value) {
      tableRef.value.clearSelection()
    }
    selectedFiles.value = []
  }
})
</script>

<style scoped>
.file-list-container {
  margin-bottom: 24px;
}

.list-card {
  border-radius: 8px;
  border: 1px solid #e4e7ed;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
}

.list-card :deep(.el-card__header) {
  background: #ffffff;
  border-bottom: 1px solid #e4e7ed;
  padding: 16px 20px;
}

.list-card :deep(.el-card__body) {
  padding: 20px;
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
  color: #2c3e50;
  font-size: 18px;
}

.file-name-cell {
  display: flex;
  align-items: center;
  gap: 10px;
}

.file-icon {
  color: #7f8c8d;
  font-size: 18px;
}

.file-name {
  font-weight: 500;
  color: #2c3e50;
  cursor: default;
  font-size: 14px;
}

.file-name.previewable {
  color: #1e88e5;
  cursor: pointer;
  text-decoration: none;
  transition: all 0.2s ease;
}

.file-name.previewable:hover {
  color: #1565c0;
  text-decoration: underline;
}

.tags-cell {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.no-tags {
  color: #95a5a6;
  font-size: 12px;
  font-style: italic;
}

/* 表格样式优化 */
:deep(.el-table) {
  border: 1px solid #e4e7ed;
  border-radius: 6px;
}

:deep(.el-table th) {
  background: #f8f9fa;
  color: #2c3e50;
  font-weight: 600;
  border-bottom: 2px solid #e4e7ed;
}

:deep(.el-table td) {
  border-bottom: 1px solid #f0f0f0;
}

:deep(.el-table--striped .el-table__body tr.el-table__row--striped td) {
  background: #fafbfc;
}

:deep(.el-table .el-button--link) {
  padding: 4px 8px;
  font-size: 13px;
  font-weight: 500;
}

:deep(.el-table .el-button--link.is-link) {
  color: #1e88e5;
}

:deep(.el-table .el-button--link.is-link:hover) {
  color: #1565c0;
}

:deep(.el-table .el-button--link.el-button--success) {
  color: #2e7d32;
}

:deep(.el-table .el-button--link.el-button--success:hover) {
  color: #1b5e20;
}

:deep(.el-table .el-button--link.el-button--danger) {
  color: #c62828;
}

:deep(.el-table .el-button--link.el-button--danger:hover) {
  color: #b71c1c;
}
</style>
