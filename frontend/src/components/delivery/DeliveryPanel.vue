<template>
  <section class="deliveries">
    <header><div><h2>资料投递</h2><p>收到提醒后，自主查看和保存。这里的附件固定为发送时的版本。</p></div><el-button @click="load" :loading="loading">刷新</el-button></header>
    <el-tabs v-model="box" @tab-change="page=1; load()"><el-tab-pane :label="`收到的资料${unread ? ' · '+unread+' 未读' : ''}`" name="inbox"/><el-tab-pane label="已发送的资料" name="outbox"/></el-tabs>
    <el-alert v-if="error" :title="error" type="error" :closable="false"/>
    <el-alert v-if="pendingOperations.length" title="上次未完成的操作已保留，可核对并继续。" type="warning" :closable="false"><p v-for="op in pendingOperations" :key="op.request_id">{{ op.kind==='delivery_send'?'发送资料':'收录资料' }} · {{ op.request_id }} <el-button size="small" :loading="busy" @click="retry(op)">核对并继续</el-button></p></el-alert>
    <el-empty v-if="!loading && !rows.length" :description="box==='inbox' ? '还没有收到资料' : '还没有发送资料'"/>
    <el-table v-else :data="rows" v-loading="loading" @row-click="open" class="delivery-table">
      <el-table-column label="资料" min-width="220"><template #default="{row}"><span class="unread-dot" v-if="box==='inbox' && !row.read_at && row.status==='sent'"/>{{ row.items[0]?.filename }}</template></el-table-column>
      <el-table-column prop="sender_name" label="发送人" width="140"/><el-table-column label="发送时间" min-width="170"><template #default="{row}">{{ format(row.sent_at) }}</template></el-table-column>
      <el-table-column label="状态" width="100"><template #default="{row}"><el-tag :type="row.status==='recalled' ? 'info' : 'success'">{{ row.status==='recalled' ? '已撤回' : '已送达' }}</el-tag></template></el-table-column>
    </el-table>
    <el-pagination v-if="total>20" v-model:current-page="page" :total="total" :page-size="20" layout="prev, pager, next" @current-change="load"/>
    <el-dialog v-model="visible" title="投递详情" width="560px" :close-on-click-modal="false" :before-close="done => { if (!busy) done() }">
      <template v-if="selected"><h3>{{ selected.items[0]?.filename }}</h3><p>{{ selected.sender_name }} · {{ format(selected.sent_at) }}</p><p class="note">{{ selected.note || '未附说明' }}</p>
        <el-alert v-if="selected.status==='recalled'" title="发送者已撤回，不能继续获取附件。已保存的个人副本不受影响。" type="info" :closable="false"/>
        <p v-else class="hint">附件不会随源资料后续修改而变化，也不会自动进入你的本地库。</p>
        <el-alert v-if="actionError" :title="actionError" type="error" :closable="false"/>
      </template>
      <TransferDuplicateNotice v-if="intakeDuplicate" :result="intakeDuplicate" @open="openExistingLocal" />
      <template #footer><el-button :disabled="busy" @click="visible=false">关闭</el-button><template v-if="selected?.can_download"><el-button :loading="busy" @click="download">下载附件</el-button><el-button v-if="desktop && !intakeDuplicate" type="primary" :loading="busy" @click="intake">收录到待整理</el-button></template><el-button v-if="selected?.can_recall" type="danger" plain :loading="busy" @click="recall">撤回投递</el-button></template>
    </el-dialog>
  </section>
</template>
<script setup>
import { ref, watch, onBeforeUnmount } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRouter } from 'vue-router'
import TransferDuplicateNotice from '../TransferDuplicateNotice.vue'
import { contentDuplicate, existingLocalLocation } from '../../utils/transferDedup'
const props=defineProps({transport:{type:Function,required:true},workspaceId:{type:String,default:''},desktop:Boolean})
const emit=defineEmits(['intaken'])
const router = props.desktop ? useRouter() : null
const intakeDuplicate = ref(null)
const pendingOperations=ref([])
const rows=ref([]),box=ref('inbox'),page=ref(1),total=ref(0),unread=ref(0),loading=ref(false),error=ref(''),visible=ref(false),selected=ref(null),busy=ref(false),actionError=ref('')
let timer, stopped=false, generation=0, context=0, pollGeneration=0, backoff=10000
const prefix=()=>`/api/workspaces/${encodeURIComponent(props.workspaceId)}`
const format=value=>value ? new Date(value).toLocaleString() : ''
const message=e=>e?.response?.data?.error?.message || e?.message || '请求失败，请重试'
async function load() {
  if (!props.workspaceId) return
  const epoch = ++generation, scope = context, workspace = props.workspaceId
  const url = prefix()
  loading.value = true; error.value = ''
  try {
    const result = await props.transport('GET', url + '/deliveries', null, { params: { box: box.value, page: page.value } })
    if (epoch !== generation || scope !== context || stopped) return
    rows.value = result.items; total.value = result.total
    if (props.desktop) {
      const pending = await props.transport('GET', '/api/deliveries/operations', null, { params: { workspace_id: workspace } })
      if (epoch === generation && scope === context && !stopped) pendingOperations.value = pending
    }
  } catch (e) {
    if (epoch === generation && scope === context && !stopped) { error.value = message(e); rows.value = []; pendingOperations.value = [] }
  } finally { if (epoch === generation && !stopped) loading.value = false }
}
async function poll() {
  clearTimeout(timer)
  const epoch = ++pollGeneration, scope = context
  if (stopped || !props.workspaceId) return
  try {
    const result = await props.transport('GET', prefix() + '/deliveries/unread-count')
    if (epoch !== pollGeneration || scope !== context || stopped) return
    unread.value = result.count; backoff = 10000
  } catch { backoff = Math.min(backoff * 2, 60000) }
  if (!stopped && epoch === pollGeneration && scope === context) timer = setTimeout(poll, document.hidden ? 60000 : backoff)
}
async function open(row) {
  if (busy.value) return
  const scope = context, url = prefix(), inbox = box.value === 'inbox'
  actionError.value = ''; intakeDuplicate.value = null
  try {
    const result = await props.transport('GET', url + `/deliveries/${row.id}`)
    if (scope !== context || stopped) return
    selected.value = result; visible.value = true
    if (inbox) {
      await props.transport('POST', url + `/deliveries/${row.id}/read`)
      if (scope !== context || stopped) return
      row.read_at = new Date().toISOString(); poll()
    }
  } catch (e) { if (scope === context && !stopped) error.value = message(e) }
}
async function action(fn) {
  if (busy.value) return
  const scope = context
  busy.value = true; actionError.value = ''
  try { await fn() } catch (e) { if (scope === context && !stopped) actionError.value = message(e) }
  finally { if (scope === context && !stopped) busy.value = false }
}
function download(){const scope=context;return action(async()=>{const item=selected.value.items[0];const blob=await props.transport('GET',prefix()+`/deliveries/${selected.value.id}/items/${item.id}/download`,null,{responseType:'blob'});if(scope!==context||stopped)return;const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=item.filename+(item.doc_type==='folder'?'.zip':'');a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)})}
const intakeIds=new Map()
function openExistingLocal() { const location=existingLocalLocation(intakeDuplicate.value);if(location&&router){visible.value=false;router.push(location)} }
function intake(){if(intakeDuplicate.value)return;const scope=context;return action(async()=>{const item=selected.value.items[0];const key=selected.value.id+'/'+item.id;if(!intakeIds.has(key))intakeIds.set(key,crypto.randomUUID());const result=await props.transport('POST',prefix()+`/deliveries/${selected.value.id}/items/${item.id}/intake`,{request_id:intakeIds.get(key)});if(scope!==context||stopped)return;const duplicate=contentDuplicate(result,'local');if(duplicate){intakeDuplicate.value=duplicate;return}ElMessage.success('已收录到待整理');emit('intaken',result)})}
function retry(op){const scope=context;return action(async()=>{const result=await props.transport('POST',`/api/deliveries/operations/${op.request_id}/retry`,null,{params:{workspace_id:props.workspaceId}});if(scope!==context||stopped)return;const duplicate=contentDuplicate(result,'local');if(duplicate){selected.value=null;intakeDuplicate.value=duplicate;visible.value=true;await load();return}ElMessage.success('操作已完成');await load();if(scope===context&&!stopped)emit('intaken')})}
async function recall() {
  const scope = context, delivery = selected.value, url = prefix()
  if (!delivery || busy.value) return
  try {
    await ElMessageBox.confirm(`这会撤回本次投递给全部 ${delivery.recipient_count} 位接收者的获取权限。已经下载或收录的副本不会删除。`, '撤回本次投递', { confirmButtonText: '确认撤回', cancelButtonText: '取消', type: 'warning' })
  } catch { return }
  if (scope !== context || selected.value?.id !== delivery.id || stopped) return
  await action(async () => {
    const result = await props.transport('POST', url + `/deliveries/${delivery.id}/recall`)
    if (scope !== context || stopped) return
    selected.value = result; await load(); poll()
  })
}
watch(() => props.workspaceId, () => {
  context++; generation++; pollGeneration++; clearTimeout(timer)
  visible.value = false; selected.value = null; intakeDuplicate.value = null; busy.value = false; rows.value = []; pendingOperations.value = []; unread.value = 0; page.value = 1
  intakeIds.clear(); load(); poll()
}, { immediate: true })
onBeforeUnmount(() => { stopped = true; context++; generation++; pollGeneration++; clearTimeout(timer) })
</script>
<style scoped>
.deliveries{max-width:1200px;margin:auto}.deliveries header{display:flex;align-items:center;justify-content:space-between;margin-bottom:18px}.deliveries h2{margin:0 0 8px;color:#253b52}.deliveries p{color:#738092;line-height:1.7}.delivery-table{cursor:pointer}.unread-dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:#409eff;margin-right:10px}.note{white-space:pre-wrap;color:#334155!important}.hint{font-size:13px}.el-pagination{margin-top:20px}
</style>
