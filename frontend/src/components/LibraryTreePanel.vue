<template>
  <div class="library-tree-panel">
    <!-- 面板标题 -->
    <div class="panel-header">
      <span class="panel-title">文档库</span>
    </div>

    <!-- 总览与收藏始终可达，文件夹为可选分类导航。 -->
    <div class="navigation-shortcuts">
      <div class="special-items">
        <button
          type="button"
          class="special-item"
          :class="{ 'is-active': modelValue === null && !favoritesActive }"
          :aria-current="modelValue === null && !favoritesActive ? 'page' : undefined"
          @click="emit('update:modelValue', null)"
        >
          <el-icon><Files /></el-icon>
          <span>全部资料</span>
        </button>
        <button
          type="button"
          class="special-item favorite-entry"
          :class="{ 'is-active': favoritesActive }"
          :aria-current="favoritesActive ? 'page' : undefined"
          @click="emit('favorites')"
        >
          <el-icon><Star /></el-icon>
          <span>我的收藏</span>
        </button>
      </div>


    </div>

    <!-- 懒加载目录树 -->
    <div v-if="showFolders" class="tree-body">
      <div class="folder-heading">
        <span>文件夹</span>
        <el-button link aria-label="新建文件夹" title="新建文件夹" @click="createFolder">
          <el-icon><Plus /></el-icon>
        </el-button>
      </div>
      <el-tree
        :key="treeKey"
        ref="treeRef"
        lazy
        empty-text="暂无文件夹，点击 ＋ 新建"
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
            :class="{ 'is-selected': !favoritesActive && modelValue === data.path }"
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
import { ref } from 'vue'
import { Folder, Files, Star, Plus } from '@element-plus/icons-vue'
import { createLibraryDir, getLibraryDirTree } from '@/api/settings'
import { ElMessage, ElMessageBox } from 'element-plus'

const props = defineProps({
  modelValue: {
    type: String,
    default: null,
  },
  favoritesActive: { type: Boolean, default: false },
  showFolders: {
    type: Boolean,
    default: true,
  },
})

const emit = defineEmits(['update:modelValue', 'favorites'])

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
      const result = await getLibraryDirTree('')
      resolve(result.items || [])
    } catch (e) {
      console.error('[LibraryTreePanel] 加载根目录失败:', e)
      resolve([])
    }
    return
  }

  const parentPath = node.data.path
  try {
    const result = await getLibraryDirTree(parentPath)
    resolve(result.items || [])
  } catch (e) {
    console.error('[LibraryTreePanel] 加载子目录失败:', parentPath, e)
    resolve([])
  }
}

const handleNodeClick = (data) => {
  emit('update:modelValue', data.path)
}

const createFolder = async () => {
  try {
    await ElMessageBox.prompt('为资料创建一个常用分类', '新建文件夹', {
      inputPlaceholder: '文件夹名称',
      confirmButtonText: '创建',
      cancelButtonText: '取消',
      inputValidator: value => {
        const name = (value || '').trim()
        return !!name && !name.startsWith('.') && !/[\\/]/.test(name)
          || '请输入有效名称，不能包含 / 或 \\'
      },
      beforeClose: async (action, instance, done) => {
        if (instance.confirmButtonLoading) return
        if (action !== 'confirm') { done(); return }
        instance.confirmButtonLoading = true
        try {
          const name = instance.inputValue.trim()
          await createLibraryDir(name)
          reload()
          emit('update:modelValue', name)
          ElMessage.success('文件夹已创建，可从全部资料中选择资料归入')
          done()
        } catch (e) {
          ElMessage.error(e.message || '创建文件夹失败')
        } finally {
          instance.confirmButtonLoading = false
        }
      },
    })
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') ElMessage.error(e.message || '创建文件夹失败')
  }
}

// 暴露 reload 方法，供父组件在整理文档后刷新目录树
const reload = () => {
  treeKey.value++
}
defineExpose({ reload })
</script>

<style scoped>
.folder-heading { display: flex; align-items: center; justify-content: space-between; padding: 12px 10px 8px; color: #606266; font-size: 12px; font-weight: 500; }
.library-tree-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: #fafafa;
}

.panel-header {
  padding: 18px 16px 12px;
  flex-shrink: 0;
}

.panel-title {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}

.special-items {
  flex-shrink: 0;
  padding: 4px 8px;
}

.special-item {
  width: 100%;
  border: 0;
  background: transparent;
  text-align: left;
  font-family: inherit;
  min-height: 38px;
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
  margin: 2px 0;
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

.favorite-entry { margin-left: 16px; width: calc(100% - 16px); }
.special-item:focus-visible { outline: 2px solid #4096ff; outline-offset: -2px; }

.tree-body {
  flex: 1;
  overflow-y: auto;
  margin: 0 8px 12px 24px;
  padding: 0 0 8px;
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
