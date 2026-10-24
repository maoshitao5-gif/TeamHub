import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import compiler from '../node_modules/@vue/compiler-sfc/dist/compiler-sfc.cjs.js'
import { createRenderer, h, nextTick, reactive } from '../node_modules/vue/dist/vue.runtime.esm-bundler.js'

const vueURL = new URL('../node_modules/vue/dist/vue.runtime.esm-bundler.js', import.meta.url)
const renderer = createRenderer({ createComment: () => ({}), createElement: () => ({}), createText: () => ({}),
  insert() {}, remove() {}, patchProp() {}, setElementText() {}, setText() {}, setComment() {}, parentNode: () => null, nextSibling: () => null })
const data = () => ({ items: [{ id: 'd1', name: '资料.txt', tags: [], preorganize: { state: 'waiting' } }, { id: 'd2', name: '图纸.pdf', preorganize: { state: 'ready' } }], total: 2, counts: { waiting: 1, ready: 1 } })
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done }); return { promise, resolve } }
async function settled() { for (let i = 0; i < 12; i++) { await Promise.resolve(); await nextTick() } }
let serial = 0
async function fixture(overrides = {}) {
  globalThis.window = { electron: {} }
  const calls = [], timers = new Map(), appStore = reactive({ libraryPath: '/library-a', settings: {}, pendingCount: 2, async fetchPendingCount() {} })
  const api = {
    async listPreorganizeDocuments(params, signal) { calls.push({ params, signal }); return data() },
    async getTags() { return [] }, async searchDocuments() { return { documents: [] } },
    ...overrides,
  }
  const key = `__preorganize_pending_${++serial}`
  globalThis[key] = { api, appStore, router: { push() {} }, route: { query: {} }, setTimeout: callback => { const id = Symbol(); timers.set(id, callback); return id }, clearTimeout: id => timers.delete(id) }
  const source = await fs.readFile(new URL('../src/pages/PendingPage.vue', import.meta.url), 'utf8')
  let script = compiler.compileScript(compiler.parse(source).descriptor, { id: key }).content
  script = script.replace(/from 'vue'/g, `from '${vueURL}'`)
    .replace(/import \{ useAppStore \} from '@\/stores\/app'/, `const useAppStore = () => globalThis.${key}.appStore`)
    .replace(/import \{ useRoute, useRouter \} from 'vue-router'/, `const useRoute = () => globalThis.${key}.route; const useRouter = () => globalThis.${key}.router`)
    .replace(/import \{ ElMessage, ElMessageBox \} from 'element-plus'/, 'const ElMessage = { success() {}, warning() {}, info() {}, error() {} }; const ElMessageBox = { confirm: async () => true }')
    .replace(/import \{([^}]+)\} from '@\/api\/[^']+'/g, (_, names) => names.split(',').map(name => name.trim()).filter(Boolean).map(name => `const ${name} = (...args) => globalThis.${key}.api.${name}(...args)`).join('\n'))
    .replace(/from '@\/utils\/([^']+)'/g, (_, name) => `from '${new URL(`../src/utils/${name}.js`, import.meta.url)}'`)
    .replace(/import \{([^}]+)\} from '@element-plus\/icons-vue'/, (_, names) => names.split(',').map(name => `const ${name.trim()} = {}`).join('\n'))
    .replace(/import \w+ from '@\/components\/[^']+'/g, match => `const ${match.split(' ')[1]} = {}`)
    .replace(/\bsetTimeout\(/g, `globalThis.${key}.setTimeout(`).replace(/\bclearTimeout\(/g, `globalThis.${key}.clearTimeout(`)
  const { default: component } = await import(`data:text/javascript;base64,${Buffer.from(script).toString('base64')}#${serial}`)
  let state
  const scriptOnly = { ...component, setup(values, context) { state = component.setup(values, context); return () => null } }
  const app = renderer.createApp({ setup() { return () => h(scriptOnly) } })
  app.mount({}); await settled()
  return { appStore, calls, timers, get state() { return state }, async tick() { const callbacks = [...timers.values()]; timers.clear(); for (const callback of callbacks) callback(); await settled() }, close() { app.unmount(); delete globalThis[key] } }
}

test('待整理轮询保留勾选；打开的手工表单不随列表刷新和翻页消失', async () => {
  const f = await fixture()
  try {
    f.state.documents.value[0]._selected = true
    await f.state.organizeOne(f.state.documents.value[0])
    f.state.organizeForm.value.name = '人工编辑'
    await f.tick()
    assert.equal(f.state.documents.value[0]._selected, true)
    f.state.documents.value = []
    assert.equal(f.state.currentDoc.value.id, 'd1')
    assert.equal(f.state.organizeForm.value.name, '人工编辑')
    assert.equal(f.state.showOrganizeDialog.value, true)
  } finally { f.close() }
})

test('手工整理压缩包名称时完整保留复合扩展名的表单约定', async () => {
  const f = await fixture()
  try {
    await f.state.organizeOne({ id: 'archive-a', name: '验收资料.tar.gz', tags: [] })
    assert.equal(f.state.organizeForm.value.name, '验收资料')
    assert.equal(f.state.currentDoc.value.name, '验收资料.tar.gz')
  } finally { f.close() }
})

test('状态与关键词同一个请求筛选，筛选重置页码和旧选择', async () => {
  const f = await fixture()
  try {
    f.state.currentPage.value = 3
    f.state.documents.value[0]._selected = true
    f.state.activeKeyword.value = '设备'
    f.state.changePreorganizeFilter('ready')
    await settled()
    assert.equal(f.calls.at(-1).params.state, 'ready')
    assert.equal(f.calls.at(-1).params.search, '设备')
    assert.equal(f.calls.at(-1).params.page, 1)
    assert.equal(f.state.selectedIds.value.length, 0)
  } finally { f.close() }
})

test('切换资料库立即中止旧列表请求，迟到旧资料不进入新列表', async () => {
  const gate = deferred(); let count = 0, oldSignal
  const f = await fixture({ listPreorganizeDocuments: async (params, signal) => {
    if (++count === 2) { oldSignal = signal; return gate.promise }
    return count > 2 ? { items: [{ id: 'new', name: '新库.txt' }], total: 1, counts: { waiting: 1 } } : data()
  } })
  try {
    const pending = f.state.fetchPending(true)
    await settled()
    f.appStore.libraryPath = '/library-b'
    await settled()
    assert.equal(oldSignal.aborted, true)
    gate.resolve(data()); await pending; await settled()
    assert.deepEqual(f.state.documents.value.map(item => item.id), ['new'])
  } finally { f.close() }
})

test('离开页面终止轮询，请求失败保留已有资料供手工操作', async () => {
  let count = 0
  const f = await fixture({ listPreorganizeDocuments: async () => { if (++count > 1) throw new Error('暂时不可用'); return data() } })
  try {
    await f.tick()
    assert.equal(f.state.documents.value.length, 2)
    assert.equal(f.state.pendingSearchError.value, '暂时不可用')
    assert.equal(f.timers.size, 1)
  } finally { f.close() }
  assert.equal(f.timers.size, 0)
})

for (const mode of ['organize', 'tag']) {
  test(`手工批量 ${mode} 开窗后目标固定，AI 状态轮询移出筛选也不会缩小提交范围`, async () => {
    const writes = []
    const f = await fixture({
      async batchOrganizeDocuments(value) { writes.push(value) },
      async batchUpdateTags(value) { writes.push(value) },
    })
    try {
      f.state.documents.value.forEach(doc => { doc._selected = true })
      if (mode === 'organize') {
        await f.state.batchOrganize()
        f.state.batchOrganizeForm.value.tags = ['现场']
      } else {
        await f.state.batchTag()
        f.state.batchTagForm.value.add_tags = ['现场']
      }
      // 任务完成后对象已不再匹配 processing 筛选。
      f.state.documents.value = []
      if (mode === 'organize') await f.state.submitBatchOrganize()
      else await f.state.submitBatchTag()
      assert.equal(writes.length, 1)
      assert.deepEqual(writes[0].document_ids, ['d1', 'd2'])
    } finally { f.close() }
  })
}

for (const mode of ['organize', 'tag']) {
  test(`切库作废手工批量 ${mode} 的开窗准备，迟到标签不能打开旧库窗口`, async () => {
    const gate = deferred(); let tagCalls = 0
    const f = await fixture({ async getTags() { return ++tagCalls === 2 ? gate.promise : [] } })
    try {
      f.state.documents.value[0]._selected = true
      const opening = mode === 'organize' ? f.state.batchOrganize() : f.state.batchTag()
      await settled()
      f.appStore.libraryPath = '/library-b'
      await settled(); gate.resolve([{ name: '旧库标签' }]); await opening; await settled()
      assert.equal(f.state.showBatchOrganizeDialog.value, false)
      assert.equal(f.state.showBatchTagDialog.value, false)
    } finally { f.close() }
  })
}

for (const mode of ['organize', 'tag']) {
  test(`切库后旧手工批量 ${mode} 提交的响应不刷新或改变新库窗口`, async () => {
    const gate = deferred()
    const f = await fixture({ async batchOrganizeDocuments() { return gate.promise }, async batchUpdateTags() { return gate.promise } })
    try {
      f.state.documents.value[0]._selected = true
      if (mode === 'organize') {
        await f.state.batchOrganize()
        f.state.batchOrganizeForm.value.tags = ['现场']
      } else {
        await f.state.batchTag()
        f.state.batchTagForm.value.add_tags = ['现场']
      }
      const submitting = mode === 'organize' ? f.state.submitBatchOrganize() : f.state.submitBatchTag()
      await settled(); f.appStore.libraryPath = '/library-b'; await settled()
      const newLibraryReads = f.calls.length
      gate.resolve({}); await submitting; await settled()
      assert.equal(f.calls.length, newLibraryReads)
      assert.equal(f.state.showBatchOrganizeDialog.value, false)
      assert.equal(f.state.showBatchTagDialog.value, false)
      assert.equal(f.state.batchOrganizeSnapshot.value.length, 0)
      assert.equal(f.state.batchTagSnapshot.value.length, 0)
    } finally { f.close() }
  })
  test(`关闭手工批量 ${mode} 清空快照，重新打开只包含新选择`, async () => {
    const f = await fixture()
    try {
      f.state.documents.value[0]._selected = true
      if (mode === 'organize') {
        await f.state.batchOrganize()
        assert.equal(f.state.batchOrganizeSnapshot.value[0].name, '资料.txt')
        f.state.showBatchOrganizeDialog.value = false
        await settled()
        assert.equal(f.state.batchOrganizeSnapshot.value.length, 0)
      } else {
        await f.state.batchTag()
        assert.equal(f.state.batchTagSnapshot.value[0].name, '资料.txt')
        f.state.showBatchTagDialog.value = false
        await settled()
        assert.equal(f.state.batchTagSnapshot.value.length, 0)
      }
      f.state.documents.value[0]._selected = false
      f.state.documents.value[1]._selected = true
      if (mode === 'organize') {
        await f.state.batchOrganize()
        assert.deepEqual(f.state.batchOrganizeSnapshot.value.map(doc => doc.id), ['d2'])
      } else {
        await f.state.batchTag()
        assert.deepEqual(f.state.batchTagSnapshot.value.map(doc => doc.id), ['d2'])
      }
    } finally { f.close() }
  })
}
