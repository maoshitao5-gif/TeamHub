<template>
  <div class="library-tree-panel">
    <!-- 面板标题 -->
    <div class="panel-header">
      <span class="panel-title">目录导航</span>
    </div>

    <!-- 特殊节点（仅在 showSpecialItems=true 时显示） -->
    <template v-if="showSpecialItems">
      <div class="special-items">
        <div
          class="special-item"
          :class="{ 'is-active': modelValue === null }"
          @click="emit('update:modelValue', null)"
        >
          <el-icon><Files /></el-icon>
          <span>平铺显示全部文档</span>
        </div>
        <div
          class="special-item"
          :class="{ 'is-active': modelValue === '.' }"
          @click="emit('update:modelValue', '.')"
        >
          <el-icon><House /></el-icon>
          <span>按目录显示全部文档</span>
        </div>
      </div>

      <!-- 分隔线 -->
      <div class="tree-divider"></div>
    </template>

    <!-- 懒加载目录树 -->
    <div class="tree-body">
      <el-tree
        :key="treeKey"
        ref="treeRef"
        lazy
        :load="loadNode"
        :props="treeProps"
        node-key="path"
        :expand-on-click-node="true"
        :highlight-current="false"
        @node-click="handleNodeClick"
        class="dir-tree"
      >
        <template #default="{ data }">
          <div
            class="tree-node-content"
            :class="{ 'is-selected': modelValue === data.path }"
          >
            <el-icon class="node-folder-icon"><Folder /></el-icon>
            <span class="node-label" :title="data.name">{{ data.name }}</span>
          </div>
        </template>
      </el-tree>
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import { Folder, Files, House } from '@element-plus/icons-vue'
import { getDocumentDirs } from '@/api/document'

const props = defineProps({
  modelValue: {
    type: String,
    default: null,
  },
  showSpecialItems: {
    type: Boolean,
    default: true,
  },
})

const emit = defineEmits(['update:modelValue'])

const treeRef = ref(null)
const treeKey = ref(0)  // 改变此值可强制重载整棵树

const treeProps = {
  label: 'name',
  isLeaf: () => false,  // 所有目录都允许展开（展开后无子项则自动折叠）
}

// 懒加载：每次展开节点时调用
const loadNode = async (node, resolve) => {
  if (node.level === 0) {
    // 顶层：加载根目录的子目录
    try {
      const result = await getDocumentDirs('')
      resolve(result.items || [])
    } catch (e) {
      console.error('[LibraryTreePanel] 加载根目录失败:', e)
      resolve([])
    }
    return
  }

  const parentPath = node.data.path
  try {
    const result = await getDocumentDirs(parentPath)
    resolve(result.items || [])
  } catch (e) {
    console.error('[LibraryTreePanel] 加载子目录失败:', parentPath, e)
    resolve([])
  }
}

const handleNodeClick = (data) => {
  emit('update:modelValue', data.path)
}

// 暴露 reload 方法，供父组件在整理文档后刷新目录树
const reload = () => {
  treeKey.value++
}
defineExpose({ reload })
</script>

<style scoped>
.library-tree-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: #fafafa;
}

.panel-header {
  padding: 12px 14px 10px;
  background: linear-gradient(to right, #f0f7ff, #f8faff);
  border-bottom: 2px solid #e4eef8;
  flex-shrink: 0;
}

.panel-title {
  font-size: 11px;
  font-weight: 700;
  color: #4096ff;
  letter-spacing: 1.5px;
  text-transform: uppercase;
}

.special-items {
  flex-shrink: 0;
  padding: 4px 0;
}

.special-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 14px;
  cursor: pointer;
  font-size: 13px;
  color: #303133;
  border-left: 3px solid transparent;
  border-radius: 0 6px 6px 0;
  transition: all 0.15s;
  user-select: none;
  margin: 2px 6px 2px 0;
}

.special-item:hover {
  background: #ecf5ff;
  color: #4096ff;
}

.special-item.is-active {
  border-left-color: #4096ff;
  background: #ecf5ff;
  color: #4096ff;
  font-weight: 600;
}

.special-item .el-icon {
  font-size: 14px;
  flex-shrink: 0;
}

.tree-divider {
  height: 1px;
  background: #ebeef5;
  margin: 2px 0;
  flex-shrink: 0;
}

.tree-body {
  flex: 1;
  overflow-y: auto;
  padding: 4px 0;
}

.dir-tree {
  background: transparent;
  --el-tree-node-content-height: 30px;
}

/* 去掉 el-tree 自带高亮背景，改用自定义 is-selected */
:deep(.el-tree-node.is-current > .el-tree-node__content) {
  background-color: transparent;
}

.tree-node-content {
  display: flex;
  align-items: center;
  gap: 5px;
  width: 100%;
  border-radius: 4px;
  padding: 0 4px;
}

.tree-node-content.is-selected {
  color: #4096ff;
  font-weight: 500;
}

.node-folder-icon {
  color: #e67e22;
  flex-shrink: 0;
  font-size: 14px;
}

.tree-node-content.is-selected .node-folder-icon {
  color: #4096ff;
}

.node-label {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
}
</style>
