<template>
  <div class="smart-search-bar" ref="barRef">
    <!-- 主搜索框 -->
    <div
      class="search-input-area"
      :class="{ focused: isFocused }"
      @click="inputRef?.focus()"
    >
      <el-icon class="search-prefix-icon"><Search /></el-icon>

      <!-- 标签模式：已选标签芯片 -->
      <div v-if="mode === 'tag' && selectedTags.length > 0" class="selected-tag-chips">
        <el-tag
          v-for="tag in selectedTags"
          :key="tag.id"
          closable
          size="small"
          class="tag-chip"
          effect="light"
          @close="removeTag(tag)"
          @click.stop
        >{{ tag.name }}</el-tag>
      </div>

      <!-- 输入框 -->
      <input
        ref="inputRef"
        v-model="inputVal"
        class="inner-input"
        :placeholder="currentPlaceholder"
        @input="onInput"
        @keydown.enter.prevent="onEnter"
        @keydown.backspace="onBackspace"
        @focus="onFocus"
        @blur="onBlur"
      />

      <!-- 清空按钮 -->
      <el-icon v-if="hasContent" class="clear-btn" @click.stop="clear">
        <CircleClose />
      </el-icon>

      <!-- 分隔线 -->
      <div class="mode-divider"></div>

      <!-- 模式切换按钮 -->
      <div class="mode-toggle" @click.stop="toggleMode" :title="mode === 'name' ? '切换为标签搜索' : '切换为文件名搜索'">
        <el-icon><Document v-if="mode === 'name'" /><CollectionTag v-else /></el-icon>
        <span>{{ mode === 'name' ? '文件名' : '标签' }}</span>
        <el-icon class="toggle-swap-icon"><Sort /></el-icon>
      </div>
    </div>

    <!-- 下拉建议面板 -->
    <Transition name="dropdown-fade">
      <div v-if="showDropdown" class="suggestions-panel">

        <!-- ===== 文件名模式 ===== -->
        <template v-if="mode === 'name'">
          <div v-if="suggestions.length === 0" class="no-result">
            <el-icon><Search /></el-icon> 未找到匹配的文档
          </div>
          <div
            v-for="doc in suggestions"
            :key="doc.id"
            class="suggestion-item"
            @mousedown.prevent="selectDoc(doc)"
          >
            <el-icon class="item-icon" :color="doc.is_folder ? '#e67e22' : '#409eff'">
              <Folder v-if="doc.is_folder" /><Document v-else />
            </el-icon>
            <span class="item-name" v-html="highlightMatch(doc.name, inputVal)"></span>
          </div>
        </template>

        <!-- ===== 标签模式 ===== -->
        <template v-else>
          <!-- 有输入：标签联想 -->
          <template v-if="inputVal.trim()">
            <div v-if="suggestions.length === 0" class="no-result">
              <el-icon><Search /></el-icon> 未找到匹配标签
            </div>
            <div
              v-for="tag in suggestions"
              :key="tag.id"
              class="suggestion-item"
              @mousedown.prevent="selectTag(tag)"
            >
              <div class="tag-color-dot" :style="{ background: tag.color || 'var(--color-accent)' }"></div>
              <span class="item-name" v-html="highlightMatch(tag.name, inputVal)"></span>
            </div>
          </template>

          <!-- 无输入 + 已选标签：显示共同出现的关联标签 -->
          <template v-else-if="selectedTags.length > 0">
            <template v-if="relatedTags.length > 0">
              <div class="section-title">继续缩小范围</div>
              <div
                v-for="tag in relatedTags"
                :key="tag.id"
                class="suggestion-item"
                @mousedown.prevent="selectTag(tag)"
              >
                <div class="tag-color-dot" :style="{ background: tag.color || 'var(--color-accent)' }"></div>
                <span class="item-name">{{ tag.name }}</span>
                <span class="item-count">{{ tag.count }} 个文档</span>
              </div>
            </template>
            <div v-else class="no-result">已精确到最小范围，无更多关联标签</div>
          </template>

          <!-- 无输入 + 无已选标签：提示 -->
          <template v-else>
            <div class="dropdown-hint">输入标签关键字进行筛选</div>
          </template>
        </template>

      </div>
    </Transition>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import {
  Search, CircleClose, Document, Folder, CollectionTag, Sort
} from '@element-plus/icons-vue'
import { autocompleteDocuments } from '@/api/document'
import { suggestTags, getRelatedTags } from '@/api/tag'

const props = defineProps({
  // 传入外部文档数组时，名称联想从该数组过滤，不调 API
  docSource: { type: Array, default: null },
  // 传入外部标签数组时，标签联想从该数组过滤，不调 API
  tagSource: { type: Array, default: null },
})

const emit = defineEmits(['name-search', 'tag-search'])

const barRef = ref(null)
const inputRef = ref(null)
const mode = ref('name')       // 'name' | 'tag'
const inputVal = ref('')
const selectedTags = ref([])   // [{id, name, color}]
const suggestions = ref([])    // 文件或标签联想列表
const relatedTags = ref([])    // 关联标签（标签模式）
const isFocused = ref(false)
const showDropdown = ref(false)

const hasContent = computed(() =>
  inputVal.value.length > 0 || (mode.value === 'tag' && selectedTags.value.length > 0)
)

const currentPlaceholder = computed(() => {
  if (mode.value === 'name') return '搜索文件名...'
  if (selectedTags.value.length > 0) return '继续添加标签缩小范围...'
  return '输入标签关键字...'
})

// ——— 防抖输入 ———
let debounceTimer = null
const onInput = () => {
  clearTimeout(debounceTimer)
  debounceTimer = setTimeout(fetchSuggestions, 200)
}

const fetchSuggestions = async () => {
  if (mode.value === 'name') {
    const q = inputVal.value.trim()
    if (!q) {
      suggestions.value = []
      showDropdown.value = false
      return
    }
    if (props.docSource !== null) {
      // 从外部数据源过滤，不调 API
      const ql = q.toLowerCase()
      suggestions.value = props.docSource
        .filter(d => d.name?.toLowerCase().includes(ql))
        .slice(0, 10)
      showDropdown.value = true
      return
    }
    try {
      suggestions.value = await autocompleteDocuments(q)
      showDropdown.value = true
    } catch {
      suggestions.value = []
    }
  } else {
    // 标签模式
    if (props.tagSource !== null) {
      const q = inputVal.value.trim().toLowerCase()
      const usedIds = new Set(selectedTags.value.map(t => t.id))
      suggestions.value = props.tagSource
        .filter(t => !usedIds.has(t.id) && t.name?.toLowerCase().includes(q))
        .slice(0, 20)
      showDropdown.value = true
      return
    }
    try {
      suggestions.value = await suggestTags(inputVal.value.trim())
      showDropdown.value = true
    } catch {
      suggestions.value = []
    }
  }
}

const fetchRelatedTags = async () => {
  if (selectedTags.value.length === 0) {
    relatedTags.value = []
    return
  }
  if (props.docSource !== null && props.tagSource !== null) {
    // 从外部数据中计算关联标签
    const selNames = new Set(selectedTags.value.map(t => t.name))
    const usedIds = new Set(selectedTags.value.map(t => t.id))
    // 找出同时包含所有已选标签的文档
    const matchDocs = props.docSource.filter(doc => {
      const docTagNames = (doc.tags || []).map(t => t.name || t)
      return [...selNames].every(n => docTagNames.includes(n))
    })
    // 统计这些文档中其他标签的出现次数
    const countMap = new Map()
    for (const doc of matchDocs) {
      for (const t of (doc.tags || [])) {
        const id = t.id || t.name
        if (!usedIds.has(id)) {
          countMap.set(id, { tag: { id, name: t.name || t, color: t.color }, count: (countMap.get(id)?.count || 0) + 1 })
        }
      }
    }
    relatedTags.value = [...countMap.values()]
      .sort((a, b) => b.count - a.count)
      .map(({ tag, count }) => ({ ...tag, count }))
    return
  }
  try {
    relatedTags.value = await getRelatedTags(selectedTags.value.map(t => t.id))
  } catch {
    relatedTags.value = []
  }
}

// ——— 选择操作 ———
const selectDoc = (doc) => {
  inputVal.value = doc.name
  showDropdown.value = false
  emit('name-search', doc.name)
}

const selectTag = (tag) => {
  if (selectedTags.value.find(t => t.id === tag.id)) return
  selectedTags.value.push(tag)
  inputVal.value = ''
  suggestions.value = []
  emit('tag-search', [...selectedTags.value])
  fetchRelatedTags()
}

const removeTag = (tag) => {
  selectedTags.value = selectedTags.value.filter(t => t.id !== tag.id)
  emit('tag-search', [...selectedTags.value])
  if (selectedTags.value.length > 0) {
    fetchRelatedTags()
  } else {
    relatedTags.value = []
    // 移除全部后保持下拉打开给用户继续选
    if (isFocused.value) showDropdown.value = true
  }
}

// ——— 键盘事件 ———
const onEnter = () => {
  if (mode.value === 'name') {
    showDropdown.value = false
    emit('name-search', inputVal.value.trim())
  } else {
    if (suggestions.value.length > 0) {
      selectTag(suggestions.value[0])
    }
  }
}

const onBackspace = () => {
  // 标签模式 + 输入框为空 + 有已选标签 → 退格删除最后一个
  if (mode.value === 'tag' && !inputVal.value && selectedTags.value.length > 0) {
    const last = selectedTags.value[selectedTags.value.length - 1]
    removeTag(last)
  }
}

// ——— 焦点 ———
const onFocus = () => {
  isFocused.value = true
  if (mode.value === 'tag') {
    showDropdown.value = true
    if (selectedTags.value.length > 0 && !inputVal.value) {
      fetchRelatedTags()
    } else if (inputVal.value.trim()) {
      fetchSuggestions()
    }
  } else if (inputVal.value.trim()) {
    showDropdown.value = true
    fetchSuggestions()
  }
}

const onBlur = () => {
  isFocused.value = false
  setTimeout(() => { showDropdown.value = false }, 180)
}

// ——— 模式切换 ———
const toggleMode = () => {
  const newMode = mode.value === 'name' ? 'tag' : 'name'
  mode.value = newMode
  inputVal.value = ''
  suggestions.value = []
  relatedTags.value = []
  selectedTags.value = []
  showDropdown.value = false
  if (newMode === 'name') {
    emit('name-search', '')
  } else {
    emit('tag-search', [])
  }
  inputRef.value?.focus()
}

// ——— 清空 ———
const clear = () => {
  inputVal.value = ''
  selectedTags.value = []
  relatedTags.value = []
  suggestions.value = []
  showDropdown.value = false
  if (mode.value === 'name') {
    emit('name-search', '')
  } else {
    emit('tag-search', [])
  }
}

// ——— 高亮匹配文字 ———
const highlightMatch = (text, query) => {
  if (!query || !query.trim()) return text
  const escaped = query.trim().replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  return text.replace(new RegExp(escaped, 'gi'), m => `<mark class="search-highlight">${m}</mark>`)
}

// ——— 点击外部关闭 ———
const handleClickOutside = (e) => {
  if (barRef.value && !barRef.value.contains(e.target)) {
    showDropdown.value = false
  }
}

// ——— 对外暴露：支持路由参数预设标签 ———
const setTagFilter = async (tagName) => {
  if (!tagName) return
  mode.value = 'tag'
  try {
    const results = await suggestTags(tagName)
    const match = results.find(t => t.name === tagName) || results[0]
    if (match) {
      selectedTags.value = [match]
      emit('tag-search', [...selectedTags.value])
      fetchRelatedTags()
    }
  } catch {
    // 忽略
  }
}

defineExpose({ setTagFilter })

onMounted(() => document.addEventListener('mousedown', handleClickOutside))
onBeforeUnmount(() => {
  document.removeEventListener('mousedown', handleClickOutside)
  clearTimeout(debounceTimer)
})
</script>

<style scoped>
.smart-search-bar {
  position: relative;
  width: 100%;
}

/* 主搜索框区域 */
.search-input-area {
  display: flex;
  align-items: center;
  gap: 6px;
  background: #fff;
  border: 1.5px solid #dcdfe6;
  border-radius: 8px;
  padding: 0 12px;
  min-height: 40px;
  cursor: text;
  transition: border-color 0.2s, box-shadow 0.2s;
  flex-wrap: wrap;
}

.search-input-area.focused {
  border-color: var(--color-accent);
  box-shadow: 0 0 0 3px rgba(64, 150, 255, 0.12);
}

.search-prefix-icon {
  color: #909399;
  font-size: 16px;
  flex-shrink: 0;
}

/* 已选标签芯片区 */
.selected-tag-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
}

.tag-chip {
  border-radius: 20px;
  cursor: default;
}

/* 透明输入框 */
.inner-input {
  flex: 1;
  min-width: 80px;
  border: none;
  outline: none;
  background: transparent;
  font-size: 14px;
  color: #303133;
  line-height: 38px;
  padding: 0;
}

.inner-input::placeholder {
  color: #c0c4cc;
}

/* 清空按钮 */
.clear-btn {
  color: #c0c4cc;
  cursor: pointer;
  flex-shrink: 0;
  transition: color 0.2s;
}
.clear-btn:hover {
  color: #909399;
}

/* 分隔线 */
.mode-divider {
  width: 1px;
  height: 18px;
  background: #e4e7ed;
  flex-shrink: 0;
  margin: 0 4px;
}

/* 模式切换按钮 */
.mode-toggle {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 8px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 12px;
  color: #606266;
  flex-shrink: 0;
  transition: background 0.2s, color 0.2s;
  user-select: none;
  white-space: nowrap;
}
.mode-toggle:hover {
  background: #f0f2f5;
  color: var(--color-accent);
}
.toggle-swap-icon {
  font-size: 12px;
  opacity: 0.6;
}

/* ——— 下拉面板 ——— */
.suggestions-panel {
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  right: 0;
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  box-shadow: 0 6px 24px rgba(0, 0, 0, 0.10), 0 2px 8px rgba(0, 0, 0, 0.06);
  z-index: 2000;
  max-height: 320px;
  overflow-y: auto;
  padding: 4px 0;
}

/* 分节标题 */
.section-title {
  padding: 8px 14px 4px;
  font-size: 11px;
  font-weight: 600;
  color: #909399;
  letter-spacing: 0.5px;
  text-transform: uppercase;
}

/* 建议项 */
.suggestion-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 14px;
  cursor: pointer;
  transition: background 0.15s;
  font-size: 14px;
  color: #303133;
}
.suggestion-item:hover {
  background: #f5f7fa;
}

.item-icon {
  flex-shrink: 0;
  font-size: 15px;
}

.item-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 标签色点 */
.tag-color-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

/* 文档计数 */
.item-count {
  font-size: 12px;
  color: #c0c4cc;
  flex-shrink: 0;
}

/* 无结果 / 提示 */
.no-result,
.dropdown-hint {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 16px 14px;
  color: #909399;
  font-size: 13px;
}

/* 高亮匹配文字 */
:deep(.search-highlight) {
  background: transparent;
  color: var(--color-accent);
  font-weight: 600;
}

/* 下拉动画 */
.dropdown-fade-enter-active,
.dropdown-fade-leave-active {
  transition: opacity 0.15s, transform 0.15s;
}
.dropdown-fade-enter-from,
.dropdown-fade-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
</style>
