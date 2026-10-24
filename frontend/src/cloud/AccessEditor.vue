<template>
  <div class="access-editor">
    <el-radio-group :model-value="visibility" @update:model-value="$emit('update:visibility', $event)">
      <el-radio value="restricted">指定成员</el-radio>
      <el-radio value="workspace">全空间成员</el-radio>
    </el-radio-group>
    <p class="muted">{{ visibility === 'restricted' ? '只有指定成员与资料管理者可访问。组织管理员不会自动获得权限。' : '空间内的有效成员可以访问，维护权限遵循团队角色。' }}</p>
    <template v-if="visibility === 'restricted'">
      <div v-for="entry in access" :key="entry.user_id" class="access-row">
        <span>{{ memberName(entry.user_id) }}</span>
        <el-select :model-value="entry.role" :aria-label="`${memberName(entry.user_id)}的权限`" @update:model-value="setRole(entry.user_id, $event)">
          <el-option label="可查看" value="reader" />
          <el-option label="可协作" value="contributor" />
          <el-option label="管理授权" value="manager" />
        </el-select>
        <el-button :aria-label="`移除${memberName(entry.user_id)}`" @click="remove(entry.user_id)">移除</el-button>
      </div>
      <el-select v-model="selected" filterable placeholder="添加成员" aria-label="添加授权成员" @change="add">
        <el-option v-for="member in remaining" :key="member.user_id" :label="memberName(member.user_id)" :value="member.user_id" />
      </el-select>
      <p class="muted">可协作：提交版本和维护标签。管理授权：另可调整访问范围与投递资料。</p>
    </template>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
const props = defineProps({ visibility: String, access: { type: Array, default: () => [] }, members: { type: Array, default: () => [] } })
const emit = defineEmits(['update:visibility', 'update:access'])
const selected = ref('')
const remaining = computed(() => props.members.filter(member => !props.access.some(entry => entry.user_id === member.user_id)))
const memberName = id => {
  const member = props.members.find(item => item.user_id === id)
  return member ? `${member.display_name || member.email}${member.email && member.display_name ? ` · ${member.email}` : ''}` : id
}
function add(id) { emit('update:access', [...props.access, { user_id: id, role: 'reader' }]); selected.value = '' }
function remove(id) { emit('update:access', props.access.filter(entry => entry.user_id !== id)) }
function setRole(id, role) { emit('update:access', props.access.map(entry => entry.user_id === id ? { ...entry, role } : entry)) }
</script>
