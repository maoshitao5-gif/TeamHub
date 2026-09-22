<template>
  <div class="dir-tree-selector">
    <!-- 工具栏 -->
    <div class="tree-toolbar">
      <el-text size="small" type="info">点击选择目录，<el-icon style="vertical-align:-2px;"><Plus /></el-icon> 新建子文件夹</el-text>
      <el-button link size="small" @click="resetTree" title="刷新目录树">
        <el-icon><Refresh /></el-icon>
      </el-button>
    </div>

    <!-- 懒加载目录树 -->
    <div class="tree-body">
      <el-tree
        :key="treeKey"
        ref="treeRef"
        lazy
        :load="loadNode"
        :props="treeProps"
        highlight-current
        node-key="path"
        :expand-on-click-node="false"
        @node-click="handleNodeClick"
        class="dir-tree"
      >
        <template #default="{ data }">
          <div class="tree-node-content">
            <el-icon class="node-folder-icon"><Folder /></el-icon>
            <span class="node-label">{{ data.path === '' ? rootLabel : data.name }}</span>
            <el-button
              link
              size="small"
              class="node-add-btn"
              :class="{ 'is-active': creatingUnder === data.path }"
              title="在此目录下新建文件夹"
              @click.stop="startCreate(data)"
            >
              <el-icon><Plus /></el-icon>
            </el-button>
          </div>
        </template>
      </el-tree>
    </div>

    <!-- 新建文件夹操作栏 -->
    <transition name="create-bar">
      <div v-if="creatingUnder !== null" class="create-folder-bar">
        <el-icon class="create-icon"><FolderAdd /></el-icon>
        <span class="create-hint">在 <strong>{{ creatingUnder || '根目录' }}</strong> 下新建：</span>
        <el-input
          ref="newFolderInputRef"
          v-model="newFolderName"
          size="small"
          placeholder="文件夹名称"
          class="create-input"
          @keyup.enter="confirmCreate"
          @keyup.esc="cancelCreate"
        />
        <el-button size="small" type="primary" :loading="creating" @click="confirmCreate">确定</el-button>
        <el-button size="small" @click="cancelCreate">取消</el-button>
      </div>
    </transition>

    <!-- 当前选中提示 -->
    <div class="selected-hint">
      <el-icon><Location /></el-icon>
      <span>已选：<strong>{{ displayPath }}</strong></span>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import { Folder, FolderAdd, Plus, Refresh, Location } from '@element-plus/icons-vue'
import { getLibraryDirTree, createLibraryDir } from '@/api/settings'

const props = defineProps({
  modelValue: {
    type: String,
    default: null,
  },
  excludePath: {
    type: String,
    default: '',
  },
  rootLabel: {
    type: String,
    default: '文件库（根目录）',
  },
})

const emit = defineEmits(['update:modelValue'])

const treeRef = ref(null)
const newFolderInputRef = ref(null)
const treeKey = ref(0)  // 改变 key 可强制重置整棵树

const creatingUnder = ref(null)
const newFolderName = ref('')
const creating = ref(false)

const treeProps = {
  label: 'name',
  isLeaf: () => false,  // 所有目录都可展开（展开后为空则无子项显示）
}

const displayPath = computed(() => {
  const v = props.modelValue
  if (v === null || v === undefined) return '（未选择）'
  return v === '' ? props.rootLabel : v
})

// 懒加载函数：el-tree 每次展开节点时调用
const loadNode = async (node, resolve) => {
  if (node.level === 0) {
    // 第 0 层：返回根节点本身（文件库根目录）作为树的顶层节点
    resolve([{ name: props.rootLabel, path: '' }])
    // 自动展开根节点，显示第一级子目录
    nextTick(() => {
      const rootNode = treeRef.value?.getNode('')
      if (rootNode && !rootNode.expanded) {
        rootNode.expand()
      }
    })
    return
  }

  // 其他层：加载该节点路径下的直接子目录
  const parentPath = node.data.path
  try {
    const result = await getLibraryDirTree(parentPath, props.excludePath)
    resolve(result.items)
  } catch (e) {
    console.error('[DirectoryTreeSelector] 加载子目录失败:', parentPath, e)
    resolve([])
  }
}

const handleNodeClick = (data) => {
  emit('update:modelValue', data.path)
}

const startCreate = (data) => {
  creatingUnder.value = data.path
  newFolderName.value = ''
  nextTick(() => {
    newFolderInputRef.value?.focus()
  })
}

const cancelCreate = () => {
  creatingUnder.value = null
  newFolderName.value = ''
}

const confirmCreate = async () => {
  const name = newFolderName.value.trim()
  if (!name) {
    ElMessage.warning('请输入文件夹名称')
    return
  }
  if (name.includes('/') || name.includes('\\')) {
    ElMessage.warning('文件夹名称不能包含路径分隔符')
    return
  }

  const parentPath = creatingUnder.value
  const newPath = parentPath ? `${parentPath}/${name}` : name

  creating.value = true
  try {
    await createLibraryDir(newPath)
    cancelCreate()

    // 刷新父节点的子目录列表
    const parentNode = treeRef.value?.getNode(parentPath)
    if (parentNode) {
      parentNode.loaded = false
      parentNode.childNodes.length = 0
      // 如果父节点已展开，重新展开触发加载；否则先展开
      parentNode.expand()
    }

    // 自动选中新建的文件夹
    emit('update:modelValue', newPath)
  } catch (e) {
    ElMessage.error(e.message || '创建文件夹失败')
  } finally {
    creating.value = false
  }
}

// excludePath 变化时重置整棵树（如切换到不同的整理文档）
watch(() => props.excludePath, () => {
  treeKey.value++
  emit('update:modelValue', '')
})

// 刷新按钮：重置整棵树
const resetTree = () => {
  treeKey.value++
}

defineExpose({ resetTree })
</script>

<style scoped>
.dir-tree-selector {
  border: 1px solid #dcdfe6;
  border-radius: 6px;
  overflow: hidden;
  background: #fff;
}

.tree-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 10px;
  background: #f5f7fa;
  border-bottom: 1px solid #ebeef5;
}

.tree-body {
  max-height: 260px;
  overflow-y: auto;
  padding: 4px 0;
}

.dir-tree {
  --el-tree-node-content-height: 32px;
}

/* 树节点布局 */
.tree-node-content {
  display: flex;
  align-items: center;
  gap: 4px;
  width: 100%;
  padding-right: 4px;
}

.node-folder-icon {
  color: #e67e22;
  flex-shrink: 0;
}

.node-label {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* "+" 按钮：默认隐藏，悬停时显示 */
.node-add-btn {
  opacity: 0;
  flex-shrink: 0;
  color: #409eff;
  transition: opacity 0.15s;
}

.tree-node-content:hover .node-add-btn,
.node-add-btn.is-active {
  opacity: 1;
}

/* 新建文件夹操作栏 */
.create-folder-bar {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 10px;
  background: #ecf5ff;
  border-top: 1px solid #d9ecff;
  flex-wrap: wrap;
}

.create-icon {
  color: #409eff;
  flex-shrink: 0;
}

.create-hint {
  font-size: 12px;
  color: #606266;
  flex-shrink: 0;
}

.create-input {
  flex: 1;
  min-width: 120px;
  max-width: 200px;
}

/* 动画 */
.create-bar-enter-active,
.create-bar-leave-active {
  transition: all 0.2s ease;
}
.create-bar-enter-from,
.create-bar-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}

/* 选中提示 */
.selected-hint {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 10px;
  font-size: 12px;
  color: #606266;
  background: #f5f7fa;
  border-top: 1px solid #ebeef5;
}

.selected-hint .el-icon {
  color: #409eff;
}

.selected-hint strong {
  color: #303133;
}
</style>
