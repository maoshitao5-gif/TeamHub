<template>
  <el-dialog
    v-model="visible"
    title="编辑文件标签"
    width="600px"
    :before-close="handleClose"
  >
    <div class="tag-editor-content">
      <!-- 文件信息 -->
      <div class="file-info">
        <el-icon class="file-icon"><Document /></el-icon>
        <span class="file-name">{{ file?.original_filename || '' }}</span>
      </div>

      <!-- 标签输入区域 -->
      <div class="tag-input-section">
        <div class="tag-input-label">
          <el-icon><PriceTag /></el-icon>
          <span>添加标签</span>
        </div>
        <el-autocomplete
          v-model="tagInput"
          :fetch-suggestions="queryTagSuggestions"
          placeholder="输入标签名或从下拉列表选择..."
          clearable
          @select="handleTagSelect"
          @keyup.enter="addTag"
          class="tag-autocomplete"
        >
          <template #prefix>
            <el-icon><PriceTag /></el-icon>
          </template>
          <template #default="{ item }">
            <div class="tag-suggestion-item">
              <span class="tag-name">{{ item.name }}</span>
              <span class="tag-count" v-if="item.file_count !== undefined">
                ({{ item.file_count }} 个文件)
              </span>
            </div>
          </template>
        </el-autocomplete>
        <el-button 
          type="primary" 
          @click="addTag" 
          :icon="Plus"
          style="margin-left: 8px;"
        >
          添加
        </el-button>
      </div>

      <!-- 当前标签列表 -->
      <div class="current-tags-section">
        <div class="current-tags-label">
          <el-icon><Collection /></el-icon>
          <span>当前标签 ({{ tags.length }})</span>
        </div>
        <div class="tags-list">
          <el-tag
            v-for="tag in tags"
            :key="tag"
            closable
            @close="removeTag(tag)"
            type="primary"
            effect="dark"
            class="current-tag"
          >
            {{ tag }}
          </el-tag>
          <el-empty 
            v-if="tags.length === 0" 
            description="暂无标签，请添加标签" 
            :image-size="60"
            style="padding: 20px 0;"
          />
        </div>
      </div>
    </div>

    <template #footer>
      <el-button @click="handleClose">取消</el-button>
      <el-button type="primary" @click="handleSave" :loading="saving">
        保存
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, watch, onMounted, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { Document, PriceTag, Collection, Plus } from '@element-plus/icons-vue'
import { getTags, updateFileTags } from '@/api/file'

const props = defineProps({
  modelValue: {
    type: Boolean,
    default: false
  },
  file: {
    type: Object,
    default: null
  }
})

const emit = defineEmits(['update:modelValue', 'saved'])

const visible = computed({
  get: () => props.modelValue,
  set: (val) => emit('update:modelValue', val)
})

// 标签相关
const tags = ref([])
const tagInput = ref('')
const allTags = ref([]) // 存储所有标签用于自动完成
const saving = ref(false)

// 加载所有标签
const loadAllTags = async () => {
  try {
    const result = await getTags()
    allTags.value = result.tags || []
  } catch (error) {
    console.error('加载标签失败:', error)
  }
}

// 标签自动完成查询
const queryTagSuggestions = (queryString, cb) => {
  const keyword = queryString.trim().toLowerCase()
  
  if (!keyword) {
    // 如果没有输入关键字，返回所有未选中的标签（按使用频率排序）
    const suggestions = allTags.value
      .filter(tag => !tags.value.includes(tag.name))
      .slice(0, 10)
      .map(tag => ({
        value: tag.name,
        name: tag.name,
        file_count: tag.file_count || 0
      }))
    cb(suggestions)
    return
  }
  
  // 过滤包含关键字的标签（不区分大小写）
  const suggestions = allTags.value
    .filter(tag => {
      const tagName = tag.name.toLowerCase()
      return tagName.includes(keyword) && !tags.value.includes(tag.name)
    })
    .sort((a, b) => {
      // 优先显示使用频率高的标签
      const countDiff = (b.file_count || 0) - (a.file_count || 0)
      if (countDiff !== 0) return countDiff
      return a.name.localeCompare(b.name)
    })
    .slice(0, 10)
    .map(tag => ({
      value: tag.name,
      name: tag.name,
      file_count: tag.file_count || 0
    }))
  
  cb(suggestions)
}

// 处理标签选择（从自动完成下拉框选择）
const handleTagSelect = (item) => {
  const tagName = item.name || item.value
  if (tagName && !tags.value.includes(tagName)) {
    tags.value.push(tagName)
    tagInput.value = ''
    ElMessage.success(`已添加标签: ${tagName}`)
  }
}

// 添加标签
const addTag = () => {
  const tag = tagInput.value.trim()
  if (tag && !tags.value.includes(tag)) {
    tags.value.push(tag)
    tagInput.value = ''
    ElMessage.success(`已添加标签: ${tag}`)
  } else if (tags.value.includes(tag)) {
    ElMessage.warning('标签已存在')
  } else if (!tag) {
    ElMessage.warning('请输入标签名')
  }
}

// 删除标签
const removeTag = (tag) => {
  const index = tags.value.indexOf(tag)
  if (index > -1) {
    tags.value.splice(index, 1)
    ElMessage.info(`已移除标签: ${tag}`)
  }
}

// 保存标签
const handleSave = async () => {
  if (!props.file || !props.file.id) {
    ElMessage.error('文件信息无效')
    return
  }

  saving.value = true
  try {
    await updateFileTags(props.file.id, tags.value)
    ElMessage.success('标签更新成功')
    emit('saved', {
      ...props.file,
      tags: [...tags.value]
    })
    handleClose()
  } catch (error) {
    console.error('更新标签失败:', error)
    // 错误信息已在 request.js 的拦截器中显示
  } finally {
    saving.value = false
  }
}

// 关闭对话框
const handleClose = () => {
  visible.value = false
  // 重置状态
  tagInput.value = ''
}

// 监听文件变化，初始化标签
watch(() => props.file, (newFile) => {
  if (newFile && visible.value) {
    // 初始化当前文件的标签
    tags.value = newFile.tags ? [...newFile.tags] : []
    // 加载所有标签
    loadAllTags()
  }
}, { immediate: true })

// 监听对话框显示状态
watch(visible, (newVal) => {
  if (newVal && props.file) {
    // 对话框打开时，初始化标签
    tags.value = props.file.tags ? [...props.file.tags] : []
    loadAllTags()
  }
})

// 组件挂载时加载标签
onMounted(() => {
  if (visible.value) {
    loadAllTags()
  }
})
</script>

<style scoped>
.tag-editor-content {
  padding: 10px 0;
}

.file-info {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 16px;
  background: #f8f9fa;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  margin-bottom: 24px;
}

.file-icon {
  color: #1e88e5;
  font-size: 20px;
}

.file-name {
  font-weight: 500;
  color: #2c3e50;
  font-size: 14px;
}

.tag-input-section {
  margin-bottom: 24px;
}

.tag-input-label {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 14px;
  font-size: 14px;
  font-weight: 500;
  color: #34495e;
}

.tag-input-label .el-icon {
  color: #2c3e50;
}

.tag-autocomplete {
  width: 100%;
}

.tag-autocomplete :deep(.el-input__wrapper) {
  box-shadow: 0 0 0 1px #e4e7ed inset;
  border-radius: 6px;
}

.tag-autocomplete :deep(.el-input__wrapper:hover) {
  box-shadow: 0 0 0 1px #c0c4cc inset;
}

.tag-autocomplete :deep(.el-input.is-focus .el-input__wrapper) {
  box-shadow: 0 0 0 1px #1e88e5 inset;
}

.tag-suggestion-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
}

.tag-name {
  font-weight: 500;
  color: #2c3e50;
}

.tag-count {
  font-size: 12px;
  color: #7f8c8d;
}

.current-tags-section {
  margin-bottom: 10px;
}

.current-tags-label {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 14px;
  font-size: 14px;
  font-weight: 500;
  color: #34495e;
}

.current-tags-label .el-icon {
  color: #2c3e50;
}

.tags-list {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  min-height: 40px;
  align-items: flex-start;
  padding: 12px;
  background: #f8f9fa;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
}

.current-tag {
  font-size: 13px;
  padding: 6px 12px;
  transition: all 0.2s ease;
  border-radius: 4px;
}

.current-tag:hover {
  transform: translateY(-1px);
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.1);
}

/* 对话框样式优化 */
:deep(.el-dialog) {
  border-radius: 8px;
}

:deep(.el-dialog__header) {
  padding: 20px 20px 16px;
  border-bottom: 1px solid #e4e7ed;
}

:deep(.el-dialog__title) {
  color: #2c3e50;
  font-weight: 600;
  font-size: 16px;
}

:deep(.el-dialog__body) {
  padding: 20px;
}

:deep(.el-dialog__footer) {
  padding: 16px 20px;
  border-top: 1px solid #e4e7ed;
}

:deep(.el-dialog__footer .el-button) {
  padding: 8px 20px;
  font-size: 14px;
  font-weight: 500;
  border-radius: 6px;
}

:deep(.el-dialog__footer .el-button--primary) {
  background: #2c3e50;
  border-color: #2c3e50;
}

:deep(.el-dialog__footer .el-button--primary:hover) {
  background: #34495e;
  border-color: #34495e;
}
</style>
