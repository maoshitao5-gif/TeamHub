<template>
  <div class="file-search-container">
    <el-card class="search-card" shadow="hover">
      <template #header>
        <div class="card-header">
          <el-icon><Search /></el-icon>
          <span>文件搜索</span>
        </div>
      </template>

      <!-- 关键词搜索 -->
      <div class="search-input-section">
        <el-input
          v-model="keyword"
          placeholder="输入文件名关键词搜索..."
          clearable
          @input="handleSearch"
          @clear="handleSearch"
          size="large"
        >
          <template #prefix>
            <el-icon><Search /></el-icon>
          </template>
        </el-input>
      </div>

      <!-- 标签搜索输入框 -->
      <div class="tag-search-section">
        <div class="tag-search-label">
          <el-icon><PriceTag /></el-icon>
          <span>标签搜索</span>
          <el-text type="info" size="small" style="margin-left: 8px;">
            （输入标签名关键字，系统会自动联想匹配的标签）
          </el-text>
        </div>
        <el-autocomplete
          v-model="tagSearchKeyword"
          :fetch-suggestions="queryTagSuggestions"
          placeholder="输入标签名关键字搜索..."
          clearable
          @select="handleTagSelect"
          @clear="handleTagSearchClear"
          size="large"
          class="tag-autocomplete"
          popper-class="tag-autocomplete-popper"
        >
          <template #prefix>
            <el-icon><PriceTag /></el-icon>
          </template>
          <template #default="{ item }">
            <div class="tag-suggestion-item">
              <span class="tag-name">{{ item.name }}</span>
              <span class="tag-count">({{ item.file_count }} 个文件)</span>
            </div>
          </template>
        </el-autocomplete>
      </div>

      <!-- 已选标签显示（渐进式搜索） -->
      <div v-if="selectedTags.length > 0" class="selected-tags-section">
        <div class="selected-tags-header">
          <el-text type="primary" size="small" style="font-weight: 500;">
            已选标签（{{ selectedTags.length }}）：
          </el-text>
          <el-button text type="primary" @click="clearSelectedTags" size="small">
            清空所有
          </el-button>
        </div>
        <div class="selected-tags-list">
          <el-tag
            v-for="(tag, index) in selectedTags"
            :key="tag"
            closable
            @close="removeSelectedTag(tag)"
            type="primary"
            effect="dark"
            class="selected-tag"
          >
            {{ tag }}
            <el-icon style="margin-left: 4px; font-size: 12px;">
              <Check />
            </el-icon>
          </el-tag>
        </div>
      </div>

      <!-- 标签云（可选，显示所有可用标签） -->
      <div class="tag-filter-section" v-if="showTagCloud">
        <div class="tag-filter-label">
          <el-icon><Collection /></el-icon>
          <span>所有标签</span>
          <el-button text type="info" @click="showTagCloud = false" size="small" style="margin-left: auto;">
            收起
          </el-button>
        </div>
        <div class="tag-list">
          <el-tag
            v-for="tag in filteredAvailableTags"
            :key="tag.id"
            :type="selectedTags.includes(tag.name) ? 'primary' : 'info'"
            :effect="selectedTags.includes(tag.name) ? 'dark' : 'plain'"
            class="filter-tag"
            @click="toggleTag(tag.name)"
            style="cursor: pointer;"
          >
            {{ tag.name }}
            <el-icon v-if="selectedTags.includes(tag.name)" style="margin-left: 4px;">
              <Check />
            </el-icon>
          </el-tag>
          <el-empty v-if="filteredAvailableTags.length === 0" description="暂无标签" :image-size="80" />
        </div>
      </div>
      
      <!-- 显示标签云按钮 -->
      <div v-else class="show-tag-cloud-button">
        <el-button text type="primary" @click="showTagCloud = true" size="small">
          <el-icon><Collection /></el-icon>
          显示所有标签 ({{ allTags.length }})
        </el-button>
      </div>

    </el-card>
  </div>
</template>

<script setup>
import { ref, watch, onMounted, computed } from 'vue'
import { Search, PriceTag, Check, Collection } from '@element-plus/icons-vue'
import { getTags } from '@/api/file'
import { ElMessage } from 'element-plus'

const emit = defineEmits(['search'])

// 搜索关键词
const keyword = ref('')

// 标签搜索关键字
const tagSearchKeyword = ref('')

// 所有标签
const allTags = ref([])

// 已选标签（渐进式搜索）
const selectedTags = ref([])

// 是否显示标签云
const showTagCloud = ref(false)

// 加载所有标签
const loadTags = async () => {
  try {
    const result = await getTags()
    allTags.value = result.tags || []
  } catch (error) {
    console.error('加载标签失败:', error)
  }
}

// 标签自动完成查询（输入关键字时自动联想）
const queryTagSuggestions = (queryString, cb) => {
  const keyword = queryString.trim().toLowerCase()
  
  if (!keyword) {
    // 如果没有输入关键字，返回所有未选中的标签（按使用频率排序）
    const suggestions = allTags.value
      .filter(tag => !selectedTags.value.includes(tag.name))
      .slice(0, 10) // 限制显示数量
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
      return tagName.includes(keyword) && !selectedTags.value.includes(tag.name)
    })
    .sort((a, b) => {
      // 优先显示使用频率高的标签
      const countDiff = (b.file_count || 0) - (a.file_count || 0)
      if (countDiff !== 0) return countDiff
      // 频率相同则按名称排序
      return a.name.localeCompare(b.name)
    })
    .slice(0, 10) // 限制显示数量
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
  if (tagName && !selectedTags.value.includes(tagName)) {
    selectedTags.value.push(tagName)
    tagSearchKeyword.value = '' // 清空输入框
    // 立即执行搜索（渐进式搜索）
    handleSearch()
    ElMessage.success(`已添加标签: ${tagName}`)
  }
}

// 处理标签搜索清空
const handleTagSearchClear = () => {
  tagSearchKeyword.value = ''
}

// 切换标签选择（从标签云点击）
const toggleTag = (tagName) => {
  const index = selectedTags.value.indexOf(tagName)
  if (index > -1) {
    selectedTags.value.splice(index, 1)
    ElMessage.info(`已移除标签: ${tagName}`)
  } else {
    selectedTags.value.push(tagName)
    ElMessage.success(`已添加标签: ${tagName}`)
  }
  // 立即执行搜索（渐进式搜索）
  handleSearch()
}

// 移除已选标签
const removeSelectedTag = (tagName) => {
  const index = selectedTags.value.indexOf(tagName)
  if (index > -1) {
    selectedTags.value.splice(index, 1)
    ElMessage.info(`已移除标签: ${tagName}`)
    // 立即执行搜索（渐进式搜索）
    handleSearch()
  }
}

// 清空已选标签
const clearSelectedTags = () => {
  selectedTags.value = []
  ElMessage.info('已清空所有标签')
  // 立即执行搜索
  handleSearch()
}

// 过滤可用的标签（排除已选中的）
const filteredAvailableTags = computed(() => {
  return allTags.value.filter(tag => !selectedTags.value.includes(tag.name))
})

// 执行搜索
const handleSearch = () => {
  const keywords = keyword.value.trim() ? [keyword.value.trim()] : null
  const tags = selectedTags.value.length > 0 ? selectedTags.value : null
  
  emit('search', {
    keywords,
    tags
  })
}

// 监听搜索条件变化
watch([keyword, selectedTags], () => {
  handleSearch()
}, { deep: true })

// 组件挂载时加载标签
onMounted(() => {
  loadTags()
})

// 暴露方法供父组件调用
defineExpose({
  loadTags
})
</script>

<style scoped>
.file-search-container {
  margin-bottom: 24px;
}

.search-card {
  border-radius: 8px;
  border: 1px solid #e4e7ed;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
}

.search-card :deep(.el-card__header) {
  background: #ffffff;
  border-bottom: 1px solid #e4e7ed;
  padding: 16px 20px;
}

.search-card :deep(.el-card__body) {
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

.search-input-section {
  margin-bottom: 20px;
}

.search-input-section :deep(.el-input__wrapper) {
  box-shadow: 0 0 0 1px #e4e7ed inset;
  border-radius: 6px;
}

.search-input-section :deep(.el-input__wrapper:hover) {
  box-shadow: 0 0 0 1px #c0c4cc inset;
}

.search-input-section :deep(.el-input.is-focus .el-input__wrapper) {
  box-shadow: 0 0 0 1px #1e88e5 inset;
}

.tag-search-section {
  margin-bottom: 20px;
}

.tag-search-label {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  font-size: 14px;
  font-weight: 500;
  color: #34495e;
}

.tag-search-label .el-icon {
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

.selected-tags-section {
  margin-bottom: 16px;
  padding: 16px;
  background: #f8f9fa;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
}

.selected-tags-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.selected-tags-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.tag-filter-section {
  margin-bottom: 16px;
}

.show-tag-cloud-button {
  margin-top: 8px;
  text-align: center;
}

.tag-filter-label {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  font-size: 14px;
  font-weight: 500;
  color: #34495e;
}

.tag-filter-label .el-icon {
  color: #2c3e50;
}

.tag-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  min-height: 40px;
}

.filter-tag {
  transition: all 0.2s ease;
  font-size: 13px;
  padding: 6px 12px;
  border-radius: 4px;
  cursor: pointer;
}

.filter-tag:hover {
  transform: translateY(-1px);
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.1);
}

.selected-tag {
  margin: 0;
  font-size: 13px;
  padding: 6px 12px;
  transition: all 0.2s ease;
  border-radius: 4px;
}

.selected-tag:hover {
  transform: translateY(-1px);
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.1);
}

/* 自动完成下拉框样式 */
:deep(.tag-autocomplete-popper) {
  border: 1px solid #e4e7ed;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
  border-radius: 6px;
}

:deep(.tag-autocomplete-popper .el-autocomplete-suggestion__list) {
  max-height: 300px;
}

:deep(.tag-autocomplete-popper .el-autocomplete-suggestion__item) {
  padding: 10px 15px;
  line-height: 1.5;
}

:deep(.tag-autocomplete-popper .el-autocomplete-suggestion__item.highlighted) {
  background-color: #f0f4f8;
  color: #1e88e5;
}
</style>
