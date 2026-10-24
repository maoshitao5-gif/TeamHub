<template>
  <el-alert data-testid="transfer-duplicate" :title="result.target === 'cloud' ? '云端已存在相同文件，未重复新增资料' : '本地已存在相同文件，未重复收录'" type="info" :closable="false" show-icon>
    <p class="duplicate-description">{{ result.name }}<span v-if="location"> · {{ location }}</span></p>
    <p class="duplicate-hint">{{ result.target === 'cloud' ? '继续使用已有资料；内容有变化时，再向原资料提交新版本。' : '已有资料和版本保持原样。' }}</p>
    <el-button v-if="result.documentId" data-testid="duplicate-open-existing" type="primary" link @click="$emit('open')">查看已有资料</el-button>
  </el-alert>
</template>
<script setup>
import { computed } from 'vue'
const props = defineProps({ result: { type: Object, required: true } })
defineEmits(['open'])
const location = computed(() => props.result.target === 'cloud' ? '云仓库' : props.result.status === 'pending' ? '待整理' : props.result.status === 'organized' ? '文档库' : '')
</script>
<style scoped>
.duplicate-description { margin: 6px 0; overflow-wrap: anywhere; font-weight: 600; }
.duplicate-hint { margin: 6px 0; }
</style>
