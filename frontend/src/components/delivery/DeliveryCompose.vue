<template>
 <el-dialog v-model="visible" title="投递资料" width="540px" :close-on-click-modal="false" :before-close="close">
  <p class="filename">{{ filename || '所选资料的固定版本' }}</p><p class="hint">接收者会收到提醒，自主下载或收录；不会自动公开到云仓库。</p>
  <el-form label-position="top"><el-form-item label="接收者"><el-select v-model="recipients" multiple filterable placeholder="选择一个或多个成员" :disabled="!!requestId" style="width:100%"><el-option v-for="m in members" :key="m.id" :value="m.id" :label="m.display_name"/></el-select></el-form-item><el-form-item label="附带说明（可选）"><el-input v-model="note" type="textarea" :rows="3" maxlength="4000" :disabled="!!requestId"/></el-form-item></el-form>
  <el-alert v-if="error" :title="error" type="error" :closable="false"/>
  <p v-if="requestId" class="hint">请求编号：{{ requestId }}。重试会核对同一次发送，不会改用新的工作内容。</p>
  <template #footer><el-button @click="close" :disabled="busy">关闭</el-button><el-button type="primary" :loading="busy" :disabled="!recipients.length" @click="send">{{requestId?'核对并重试':'确认发送'}}</el-button></template>
 </el-dialog>
</template>
<script setup>
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
const props = defineProps({ modelValue: Boolean, transport: { type: Function, required: true }, workspaceId: String, docId: String, sourceDocumentId: String, sourceVersionId: String, filename: String, identityKey: { type: String, default: '' } })
const emit = defineEmits(['update:modelValue', 'sent'])
const visible = ref(false), members = ref([]), recipients = ref([]), note = ref(''), requestId = ref(''), error = ref(''), busy = ref(false)
let operation = null, generation = 0
const prefix = () => `/api/workspaces/${encodeURIComponent(props.workspaceId)}`
const storageKey = () => props.identityKey ? `teamhub-delivery-request:${location.origin}:${props.identityKey}:${props.workspaceId}:${props.docId || props.sourceDocumentId}:${props.sourceVersionId || 'working'}` : ''
watch(() => [props.modelValue, props.identityKey, props.workspaceId, props.docId, props.sourceDocumentId, props.sourceVersionId], async ([value]) => {
  const epoch = ++generation
  visible.value = value
  if (!value) return
  error.value = ''; requestId.value = ''; recipients.value = []; note.value = ''; members.value = []; operation = null
  try {
    const key = storageKey()
    const saved = key ? JSON.parse(sessionStorage.getItem(key) || 'null') : null
    if (saved?.body?.request_id) {
      operation = saved; requestId.value = saved.body.request_id
      recipients.value = saved.body.recipient_ids; note.value = saved.body.note
    }
    const result = await props.transport('GET', prefix() + '/delivery-members')
    if (epoch === generation) members.value = result
  } catch (e) { if (epoch === generation) error.value = e.message || '读取成员失败' }
}, { immediate: true })
function close() { if (busy.value) return; visible.value = false; emit('update:modelValue', false) }
async function send() {
  if (busy.value || !recipients.value.length) return
  busy.value = true; error.value = ''
  const epoch = generation
  try {
    if (!operation) {
      const id = crypto.randomUUID()
      const body = { request_id: id, recipient_ids: [...recipients.value], note: note.value }
      let path = prefix() + '/deliveries'
      if (props.docId) { path = '/api/deliveries/send'; body.doc_id = props.docId; body.workspace_id = props.workspaceId }
      else { body.source_document_id = props.sourceDocumentId; body.source_version_id = props.sourceVersionId }
      operation = { body, path, key: storageKey() }
      requestId.value = id
      if (operation.key) sessionStorage.setItem(operation.key, JSON.stringify(operation))
    }
    const pending = operation
    const result = await props.transport('POST', pending.path, pending.body)
    if (pending.key) sessionStorage.removeItem(pending.key)
    if (epoch !== generation) return
    ElMessage.success(result.status === 'recalled' ? '该次投递已撤回' : '已存入对方收件箱')
    emit('sent', result); busy.value = false; close()
  } catch (e) {
    if (epoch === generation) error.value = e?.response?.data?.error?.message || e.message || '发送未完成，请使用原请求重试'
  } finally { busy.value = false }
}
</script>
<style scoped>.filename{font-weight:600;font-size:17px;color:#253b52}.hint{font-size:13px;line-height:1.7;color:#738092}</style>
