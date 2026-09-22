<template>
  <div class="tag-manage-page">
    <!-- 统计概览卡片 -->
    <el-card class="stats-card" shadow="hover">
      <template #header>
        <div class="card-header">
          <el-icon><PriceTag /></el-icon>
          <span>标签统计</span>
        </div>
      </template>

      <!-- 统计概览 -->
      <div class="stats-overview">
        <el-row :gutter="24">
          <el-col :span="8">
            <div class="stat-item">
              <div class="stat-icon stat-icon-primary">
                <el-icon><PriceTag /></el-icon>
              </div>
              <div class="stat-content">
                <div class="stat-value">{{ totalTags }}</div>
                <div class="stat-label">标签总数</div>
              </div>
            </div>
          </el-col>
          <el-col :span="8">
            <div class="stat-item">
              <div class="stat-icon stat-icon-success">
                <el-icon><Document /></el-icon>
              </div>
              <div class="stat-content">
                <div class="stat-value">{{ totalFiles }}</div>
                <div class="stat-label">关联文件总数</div>
              </div>
            </div>
          </el-col>
          <el-col :span="8">
            <div class="stat-item">
              <div class="stat-icon stat-icon-info">
                <el-icon><DataAnalysis /></el-icon>
              </div>
              <div class="stat-content">
                <div class="stat-value">{{ avgFilesPerTag.toFixed(1) }}</div>
                <div class="stat-label">平均每个标签关联文件数</div>
              </div>
            </div>
          </el-col>
        </el-row>
      </div>
    </el-card>

    <!-- 标签列表 -->
    <el-card class="list-card" shadow="hover" v-loading="loading">
      <template #header>
        <div class="card-header">
          <el-icon><PriceTag /></el-icon>
          <span>标签列表</span>
          <el-text type="info" style="margin-left: 12px;">
            共 {{ totalTags }} 个标签
          </el-text>
          <div style="margin-left: auto; display: flex; gap: 8px;">
            <el-button
              type="primary"
              @click="showCreateTagDialog = true"
              size="small"
            >
              <el-icon><Plus /></el-icon>
              创建标签
            </el-button>
            <el-button
              type="warning"
              :disabled="selectedTags.length < 2"
              @click="openMergeDialog"
              size="small"
            >
              <el-icon><Connection /></el-icon>
              合并标签
              <el-badge v-if="selectedTags.length >= 2" :value="selectedTags.length" class="badge-inline" />
            </el-button>
            <el-button
              type="danger"
              :disabled="selectedTags.length === 0"
              @click="handleBatchDeleteTags"
              size="small"
            >
              <el-icon><Delete /></el-icon>
              批量删除
              <el-badge v-if="selectedTags.length > 0" :value="selectedTags.length" class="badge-inline" />
            </el-button>
            <el-button
              type="primary"
              link
              @click="loadTags"
              :loading="loading"
              size="small"
            >
              <el-icon><Refresh /></el-icon>
              刷新
            </el-button>
          </div>
        </div>
      </template>

      <el-table
        ref="tableRef"
        :data="pagedTagStats"
        stripe
        style="width: 100%"
        :empty-text="loading ? '加载中...' : '暂无标签'"
        :row-class-name="tableRowClassName"
        @selection-change="handleTagSelectionChange"
      >
        <el-table-column type="selection" width="60" align="center" />
        <el-table-column prop="name" label="标签名称" min-width="300">
          <template #default="{ row }">
            <div class="tag-name-cell">
              <el-input
                v-if="row.editing"
                v-model="row.editName"
                size="small"
                @blur="handleSaveTag(row)"
                @keyup.enter="handleSaveTag(row)"
                @keyup.esc="row.editing = false"
                class="tag-edit-input"
                autofocus
              />
              <template v-else>
                <el-icon class="tag-icon"><PriceTag /></el-icon>
                <span class="tag-name">
                  {{ row.name }}
                </span>
              </template>
            </div>
          </template>
        </el-table-column>

        <el-table-column prop="file_count" label="关联文件数" width="160" align="center">
          <template #default="{ row }">
            <div class="file-count-cell">
              <el-icon class="file-count-icon"><Document /></el-icon>
              <span class="file-count-text" :class="getFileCountClass(row.file_count)">
                {{ row.file_count }}
              </span>
            </div>
          </template>
        </el-table-column>

        <el-table-column label="操作" width="280" fixed="right">
          <template #default="{ row }">
            <el-button
              type="primary"
              link
              @click="viewFilesByTag(row.name)"
              :icon="View"
            >
              查看文件
            </el-button>
            <el-button
              v-if="!row.editing"
              type="primary"
              link
              @click="handleEditTag(row)"
              :icon="Edit"
            >
              编辑
            </el-button>
            <el-button
              type="danger"
              link
              @click="handleDeleteTag(row.id)"
              :icon="Delete"
            >
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <!-- 分页 -->
      <div class="pagination-bar" v-if="totalTags > pageSize">
        <el-pagination
          v-model:current-page="currentPage"
          :page-size="pageSize"
          :total="totalTags"
          layout="total, prev, pager, next, jumper"
          @current-change="handlePageChange"
        />
      </div>
    </el-card>

    <!-- 合并标签对话框 -->
    <el-dialog
      v-model="showMergeDialog"
      title="合并标签"
      width="520px"
      :close-on-click-modal="false"
    >
      <div class="merge-info">
        <el-alert type="info" :closable="false" show-icon>
          <template #title>
            将以下标签合并为一个，所有关联文件将转移到目标标签下，源标签将被删除。
          </template>
        </el-alert>
      </div>

      <div class="merge-tags-list">
        <div class="merge-section-label">选中的标签</div>
        <div class="merge-tag-items">
          <el-tag
            v-for="tag in mergeSelectedTagObjects"
            :key="tag.id"
            :type="tag.id === mergeTargetId ? 'success' : 'info'"
            size="large"
            class="merge-tag-item"
          >
            <el-icon v-if="tag.id === mergeTargetId"><Check /></el-icon>
            {{ tag.name }}
            <span class="merge-tag-count">（{{ tag.file_count }} 个文件）</span>
          </el-tag>
        </div>
      </div>

      <el-form label-width="100px" style="margin-top: 20px;">
        <el-form-item label="合并目标">
          <el-select
            v-model="mergeTargetId"
            placeholder="选择保留的目标标签"
            style="width: 100%"
          >
            <el-option
              v-for="tag in mergeSelectedTagObjects"
              :key="tag.id"
              :label="tag.name + '（' + tag.file_count + ' 个文件）'"
              :value="tag.id"
            />
          </el-select>
          <div class="form-tip">其他标签将合并到此标签，合并后只保留目标标签</div>
        </el-form-item>
      </el-form>

      <div v-if="mergeTargetId" class="merge-preview">
        <div class="merge-section-label">合并预览</div>
        <div class="merge-preview-content">
          <div class="merge-preview-sources">
            <el-tag
              v-for="tag in mergeSourceTags"
              :key="tag.id"
              type="danger"
              effect="light"
              size="default"
              class="merge-preview-tag"
            >
              {{ tag.name }}
            </el-tag>
          </div>
          <el-icon class="merge-arrow"><Right /></el-icon>
          <el-tag type="success" size="default" class="merge-preview-tag">
            {{ mergeTargetTag?.name }}
          </el-tag>
        </div>
      </div>

      <template #footer>
        <div class="dialog-footer">
          <el-button @click="showMergeDialog = false">取消</el-button>
          <el-button
            type="primary"
            @click="handleMergeTags"
            :loading="mergeLoading"
            :disabled="!mergeTargetId"
          >
            确认合并
          </el-button>
        </div>
      </template>
    </el-dialog>

    <!-- 创建标签对话框 -->
    <el-dialog 
      v-model="showCreateTagDialog" 
      title="创建标签" 
      width="420px"
      :close-on-click-modal="false"
    >
      <el-form :model="createTagForm" label-width="80px" label-position="left">
        <el-form-item label="标签名称" required>
          <el-input 
            v-model="createTagForm.name" 
            placeholder="请输入标签名称"
            maxlength="50"
            show-word-limit
            clearable
            @keyup.enter="handleCreateTag"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <div class="dialog-footer">
          <el-button @click="showCreateTagDialog = false">取消</el-button>
          <el-button type="primary" @click="handleCreateTag">确定</el-button>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { PriceTag, Document, Refresh, Plus, Delete, DataAnalysis, View, Edit, Connection, Check, Right } from '@element-plus/icons-vue'
import { getTags, createTag, updateTag, deleteTag, mergeTags } from '@/api/tag'
import { useAppStore } from '@/stores/app'

const appStore = useAppStore()

const router = useRouter()

const tagStats = ref([])
const loading = ref(false)
const totalTags = ref(0)
const totalFiles = ref(0)
const selectedTags = ref([])  // 存储已选中标签的 ID（跨页保留）
const currentPage = ref(1)
const pageSize = computed(() => appStore.settings.items_per_page || 15)
const tableRef = ref(null)

// 当前页的标签切片
const pagedTagStats = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value
  return tagStats.value.slice(start, start + pageSize.value)
})

const handlePageChange = (page) => {
  currentPage.value = page
  // 翻页后恢复当前页已选行的视觉状态
  nextTick(() => {
    if (!tableRef.value) return
    pagedTagStats.value.forEach(row => {
      if (selectedTags.value.includes(row.id)) {
        tableRef.value.toggleRowSelection(row, true)
      }
    })
  })
}
const showCreateTagDialog = ref(false)
const createTagForm = ref({ name: '' })

// 计算平均每个标签关联的文件数
const avgFilesPerTag = computed(() => {
  if (totalTags.value === 0) return 0
  return totalFiles.value / totalTags.value
})

// 根据文件数量返回标签类型（保留用于其他用途）
const getFileCountType = (count) => {
  if (count === 0) return 'info'
  if (count < 5) return 'warning'
  if (count < 20) return 'primary'
  return 'success'
}

// 根据文件数量返回CSS类名
const getFileCountClass = (count) => {
  if (count === 0) return 'file-count-zero'
  if (count < 5) return 'file-count-low'
  if (count < 20) return 'file-count-medium'
  return 'file-count-high'
}

// 表格行类名
const tableRowClassName = ({ row, rowIndex }) => {
  return 'tag-table-row'
}

// 加载标签统计信息
const loadTags = async () => {
  loading.value = true
  try {
    // 请求包含统计信息的标签列表
    const response = await getTags(true)
    
    // 处理API响应格式（包含 tags 和 total_unique_documents）
    const tags = response?.tags || []
    const totalUniqueDocs = response?.total_unique_documents ?? null
    
    tagStats.value = (tags || []).map(t => ({
      ...t,
      file_count: t.document_count || 0,
      editing: false,
      editName: ''
    }))
    totalTags.value = tagStats.value.length
    currentPage.value = 1  // 每次重新加载后回到第一页
    selectedTags.value = []  // 数据刷新后清空跨页选中
    
    // 使用API返回的总唯一文档数
    if (totalUniqueDocs !== null) {
      totalFiles.value = totalUniqueDocs
    } else {
      // 回退到求和方式（不应该发生，但为了安全）
      totalFiles.value = tagStats.value.reduce((sum, t) => sum + (t.document_count || 0), 0)
    }
  } catch (error) {
    console.error('加载标签统计失败:', error)
    ElMessage.error('加载标签统计失败')
  } finally {
    loading.value = false
  }
}

// 查看该标签关联的文件
const viewFilesByTag = (tagName) => {
  router.push({
    path: '/library',
    query: { tag: tagName }
  })
}

// 处理标签选择变化（跨页合并：只更新当前页对应的选中项）
const handleTagSelectionChange = (selection) => {
  const currentPageIds = pagedTagStats.value.map(t => t.id)
  const nowSelectedIds = selection.map(t => t.id)
  selectedTags.value = [
    ...selectedTags.value.filter(id => !currentPageIds.includes(id)),
    ...nowSelectedIds,
  ]
}

// 编辑标签
const handleEditTag = (row) => {
  row.editing = true
  row.editName = row.name
}

// 保存标签
const handleSaveTag = async (row) => {
  if (!row.editName || row.editName.trim() === '') {
    ElMessage.warning('标签名称不能为空')
    row.editing = false
    return
  }
  
  try {
    await updateTag(row.id, { name: row.editName.trim() })
    ElMessage.success('标签更新成功')
    row.name = row.editName.trim()
    row.editing = false
    loadTags()
  } catch (error) {
    console.error('更新标签失败:', error)
    row.editing = false
  }
}

// 删除标签
const handleDeleteTag = async (tagId) => {
  try {
    await ElMessageBox.confirm(
      '确定要删除该标签吗？删除后该标签将从所有文件中移除。',
      '确认删除',
      {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'warning',
      }
    )
    
    await deleteTag(tagId)
    ElMessage.success('标签删除成功')
    loadTags()
  } catch (error) {
    if (error !== 'cancel') {
      console.error('删除标签失败:', error)
    }
  }
}

// 批量删除标签
const handleBatchDeleteTags = async () => {
  if (selectedTags.value.length === 0) {
    ElMessage.warning('请选择要删除的标签')
    return
  }
  
  try {
    await ElMessageBox.confirm(
      `确定要删除选中的 ${selectedTags.value.length} 个标签吗？删除后这些标签将从所有文件中移除。`,
      '确认批量删除',
      {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'warning',
      }
    )
    
    await Promise.all(selectedTags.value.map(id => deleteTag(id)))
    ElMessage.success('批量删除成功')
    selectedTags.value = []
    loadTags()
  } catch (error) {
    if (error !== 'cancel') {
      console.error('批量删除标签失败:', error)
    }
  }
}

// 创建标签
const handleCreateTag = async () => {
  if (!createTagForm.value.name || createTagForm.value.name.trim() === '') {
    ElMessage.warning('请输入标签名称')
    return
  }
  
  try {
    await createTag({ name: createTagForm.value.name.trim() })
    ElMessage.success('标签创建成功')
    showCreateTagDialog.value = false
    createTagForm.value.name = ''
    loadTags()
  } catch (error) {
    console.error('创建标签失败:', error)
  }
}

// ========== 合并标签 ==========
const showMergeDialog = ref(false)
const mergeLoading = ref(false)
const mergeTargetId = ref('')

// 选中的标签对象列表
const mergeSelectedTagObjects = computed(() => {
  return tagStats.value.filter(t => selectedTags.value.includes(t.id))
})

// 目标标签对象
const mergeTargetTag = computed(() => {
  return mergeSelectedTagObjects.value.find(t => t.id === mergeTargetId.value)
})

// 源标签（排除目标）
const mergeSourceTags = computed(() => {
  return mergeSelectedTagObjects.value.filter(t => t.id !== mergeTargetId.value)
})

const openMergeDialog = () => {
  if (selectedTags.value.length < 2) {
    ElMessage.warning('请至少选择 2 个标签进行合并')
    return
  }
  // 默认选择关联文件最多的标签作为目标
  const sorted = [...mergeSelectedTagObjects.value].sort((a, b) => b.file_count - a.file_count)
  mergeTargetId.value = sorted[0]?.id || ''
  showMergeDialog.value = true
}

const handleMergeTags = async () => {
  if (!mergeTargetId.value) {
    ElMessage.warning('请选择目标标签')
    return
  }

  const sourceIds = selectedTags.value.filter(id => id !== mergeTargetId.value)
  if (sourceIds.length === 0) {
    ElMessage.warning('没有需要合并的源标签')
    return
  }

  const targetName = mergeTargetTag.value?.name || ''
  const sourceNames = mergeSourceTags.value.map(t => t.name).join('、')

  try {
    await ElMessageBox.confirm(
      `确定将「${sourceNames}」合并到「${targetName}」吗？合并后源标签将被删除，其关联的文件将转移到目标标签。`,
      '确认合并',
      {
        confirmButtonText: '确认合并',
        cancelButtonText: '取消',
        type: 'warning',
      }
    )

    mergeLoading.value = true
    await mergeTags({
      source_tag_ids: sourceIds,
      target_tag_id: mergeTargetId.value,
    })
    ElMessage.success(`已将 ${sourceIds.length} 个标签合并到「${targetName}」`)
    showMergeDialog.value = false
    selectedTags.value = []
    loadTags()
  } catch (error) {
    if (error !== 'cancel') {
      console.error('合并标签失败:', error)
      ElMessage.error(error.message || '合并标签失败')
    }
  } finally {
    mergeLoading.value = false
  }
}

// 组件挂载时加载数据
onMounted(() => {
  loadTags()
})
</script>

<style scoped>
.tag-manage-page {
  width: 100%;
}

/* 卡片样式 - 与文件列表保持一致 */
.stats-card {
  margin-bottom: 24px;
  border-radius: 8px;
  border: 1px solid #e4e7ed;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
  transition: all 0.3s ease;
}

.stats-card:hover {
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.08);
}

.stats-card :deep(.el-card__header) {
  background: #ffffff;
  border-bottom: 1px solid #e4e7ed;
  padding: 16px 20px;
}

.stats-card :deep(.el-card__body) {
  padding: 24px;
}

.list-card {
  margin-bottom: 24px;
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

.badge-inline {
  margin-left: 4px;
}

/* 统计卡片样式 */
.stats-overview {
  padding: 4px 0;
}

.stat-item {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 20px;
  background: #fafbfc;
  border-radius: 8px;
  border: 1px solid #e4e7ed;
  transition: all 0.2s ease;
}

.stat-item:hover {
  background: #f5f7fa;
  border-color: #d0d7de;
  transform: translateY(-2px);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
}

.stat-icon {
  width: 48px;
  height: 48px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 24px;
  flex-shrink: 0;
}

.stat-icon-primary {
  background: linear-gradient(135deg, #1e88e5 0%, #1565c0 100%);
  color: #ffffff;
}

.stat-icon-success {
  background: linear-gradient(135deg, #2e7d32 0%, #1b5e20 100%);
  color: #ffffff;
}

.stat-icon-info {
  background: linear-gradient(135deg, #0288d1 0%, #01579b 100%);
  color: #ffffff;
}

.stat-content {
  flex: 1;
}

.stat-value {
  font-size: 28px;
  font-weight: 700;
  color: #2c3e50;
  line-height: 1.2;
  margin-bottom: 4px;
}

.stat-label {
  font-size: 14px;
  color: #7f8c8d;
  font-weight: 500;
}

/* 表格样式优化 - 与文件列表保持一致 */
:deep(.el-table) {
  border: 1px solid #e4e7ed;
  border-radius: 6px;
}

:deep(.el-table th) {
  background: #f8f9fa;
  color: #2c3e50;
  font-weight: 600;
  border-bottom: 2px solid #e4e7ed;
  padding: 12px 16px;
}

:deep(.el-table td) {
  border-bottom: 1px solid #f0f0f0;
  padding: 12px 16px;
}

:deep(.el-table--striped .el-table__body tr.el-table__row--striped td) {
  background: #fafbfc;
}

:deep(.el-table tr:hover > td) {
  background: #f5f7fa !important;
}

:deep(.tag-table-row) {
  transition: background-color 0.2s ease;
}

:deep(.tag-table-row:hover) {
  background-color: #f5f7fa;
}

/* 标签名称单元格 - 参照文件名样式 */
.tag-name-cell {
  display: flex;
  align-items: center;
  gap: 10px;
}

.tag-icon {
  color: #7f8c8d;
  font-size: 18px;
}

.tag-name {
  font-weight: 500;
  color: #2c3e50;
  font-size: 14px;
  transition: color 0.2s ease;
}

.tag-edit-input {
  width: 200px;
}

.tag-edit-input :deep(.el-input__wrapper) {
  box-shadow: 0 0 0 1px #1e88e5 inset;
}

/* 关联文件数显示样式 */
.file-count-cell {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
}

.file-count-icon {
  color: #7f8c8d;
  font-size: 16px;
}

.file-count-text {
  font-weight: 600;
  font-size: 14px;
  transition: color 0.2s ease;
}

.file-count-zero {
  color: #95a5a6;
}

.file-count-low {
  color: #f57c00;
}

.file-count-medium {
  color: #1e88e5;
}

.file-count-high {
  color: #2e7d32;
}


/* 操作按钮样式 - 与文件列表保持一致 */
:deep(.el-table .el-button--link) {
  padding: 4px 8px;
  font-size: 13px;
  font-weight: 500;
  margin-right: 8px;
}

:deep(.el-table .el-button--link:last-child) {
  margin-right: 0;
}

:deep(.el-table .el-button--link.is-link) {
  color: #1e88e5;
}

:deep(.el-table .el-button--link.is-link:hover) {
  color: #1565c0;
}

:deep(.el-table .el-button--link.el-button--danger) {
  color: #c62828;
}

:deep(.el-table .el-button--link.el-button--danger:hover) {
  color: #b71c1c;
}

/* 对话框样式 */
.dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}

:deep(.el-dialog__header) {
  padding: 20px 20px 16px;
  border-bottom: 1px solid #e4e7ed;
}

:deep(.el-dialog__body) {
  padding: 24px 20px;
}

:deep(.el-dialog__footer) {
  padding: 16px 20px 20px;
  border-top: 1px solid #e4e7ed;
}

/* 合并标签对话框 */
.merge-info {
  margin-bottom: 20px;
}

.merge-section-label {
  font-size: 14px;
  font-weight: 600;
  color: #2c3e50;
  margin-bottom: 10px;
}

.merge-tags-list {
  margin-bottom: 8px;
}

.merge-tag-items {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.merge-tag-item {
  font-size: 14px;
}

.merge-tag-count {
  font-size: 12px;
  color: #909399;
  margin-left: 2px;
}

.form-tip {
  font-size: 12px;
  color: #909399;
  margin-top: 4px;
  line-height: 1.4;
}

.merge-preview {
  margin-top: 20px;
  padding: 16px;
  background: #fafbfc;
  border-radius: 8px;
  border: 1px solid #e4e7ed;
}

.merge-preview-content {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.merge-preview-sources {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.merge-preview-tag {
  font-size: 13px;
}

.merge-arrow {
  font-size: 20px;
  color: #909399;
  flex-shrink: 0;
}

/* 分页栏 */
.pagination-bar {
  display: flex;
  justify-content: flex-end;
  padding: 16px 0 4px;
}

/* 响应式设计 */
@media (max-width: 768px) {
  .stats-overview .el-col {
    margin-bottom: 16px;
  }
}
</style>
