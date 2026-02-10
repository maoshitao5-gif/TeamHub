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
                :title="isPreviewable(row) ? '点击预览文件' : '点击查看文件详情'"
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
                :color="getTagColor(tag)"
                effect="plain"
                class="file-tag"
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

        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
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
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Document, Download, Delete, PriceTag } from '@element-plus/icons-vue'
import { formatFileSize, formatDateTime, getTagColor } from '@/utils/format'
import FilePreview from './FilePreview.vue'
import FileTagEditor from './FileTagEditor.vue'
import { downloadFile, deleteFile } from '@/api/file'

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

// 判断文件是否可预览（支持 jpg, jpeg, png, gif 和 pdf）
const isPreviewable = (file) => {
  if (!file || !file.original_filename) return false
  const filename = file.original_filename.toLowerCase()
  const imageExts = ['.jpg', '.jpeg', '.png', '.gif']
  const pdfExt = '.pdf'
  
  return imageExts.some(ext => filename.endsWith(ext)) || filename.endsWith(pdfExt)
}

// 处理文件名点击 - 统一打开预览对话框
const handleFileNameClick = (row) => {
  // 所有文件都打开预览对话框
  // 对于不支持预览的文件类型，预览对话框会显示提示并提供下载按钮
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
}

.card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 16px;
  font-weight: 600;
  color: #409EFF;
}

.file-name-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}

.file-icon {
  color: #409EFF;
  font-size: 18px;
}

.file-name {
  font-weight: 500;
  color: #303133;
  cursor: default;
}

.file-name.previewable {
  color: #409EFF;
  cursor: pointer;
  text-decoration: none;
  transition: color 0.3s, text-decoration 0.3s;
}

.file-name.previewable:hover {
  color: #66b1ff;
  text-decoration: underline;
}

.tags-cell {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}

.file-tag {
  margin: 0;
  border: none;
}

.no-tags {
  color: #909399;
  font-size: 12px;
}
</style>
