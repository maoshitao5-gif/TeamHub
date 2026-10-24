import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import compiler from '../node_modules/@vue/compiler-sfc/dist/compiler-sfc.cjs.js'
import { createRenderer, h, nextTick, reactive } from '../node_modules/vue/dist/vue.runtime.esm-bundler.js'

const vueURL = new URL('../node_modules/vue/dist/vue.runtime.esm-bundler.js', import.meta.url)
const renderer = createRenderer({ createComment: () => ({}), createElement: () => ({}), createText: () => ({}),
  insert() {}, remove() {}, patchProp() {}, setElementText() {}, setText() {}, setComment() {}, parentNode: () => null, nextSibling: () => null })
let serial = 0
const configuration = { library_token: 'lib-a', service_revision: 'rev-a', service: { base_url: 'https://ai.example', model: 'model-a' }, configured: true, policy: { enabled: false, active: false, paused_reason: '' } }
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done }); return { promise, resolve } }
async function settled() { for (let i = 0; i < 12; i++) { await Promise.resolve(); await nextTick() } }
async function fixture(overrides = {}) {
  const calls = { start: [], policy: [], cancel: [], undo: [] }, events = [], timers = new Map()
  const appStore = reactive({ libraryPath: '/library-a' })
  const api = {
    async getPreorganizeConfig() { return structuredClone(configuration) },
    async listPreorganizeOperations() { return { items: [{ id: 'op1', document_name: '整理.txt', undone_at: null }] } },
    async startPreorganize(data, signal) { calls.start.push({ data, signal }); return { items: data.document_ids.map(document_id => ({ document_id, state: 'processing' })) } },
    async setPreorganizePolicy(data, signal) { calls.policy.push({ data, signal }); return { ...configuration, policy: { enabled: data.enabled, active: data.enabled } } },
    async cancelPreorganize(data, signal) { calls.cancel.push({ data, signal }); return { items: [] } },
    async undoPreorganize(id, data, signal) { calls.undo.push({ id, data, signal }); return { undone: true } },
    ...overrides,
  }
  const key = `__preorganize_panel_${++serial}`
  globalThis[key] = { api, appStore, router: { push() {} }, setTimeout: callback => { const id = Symbol(); timers.set(id, callback); return id }, clearTimeout: id => timers.delete(id) }
  const source = await fs.readFile(new URL('../src/components/PreorganizePanel.vue', import.meta.url), 'utf8')
  let script = compiler.compileScript(compiler.parse(source).descriptor, { id: key }).content
  script = script.replace(/from 'vue'/g, `from '${vueURL}'`)
    .replace(/import \{ useAppStore \} from '@\/stores\/app'/, `const useAppStore = () => globalThis.${key}.appStore`)
    .replace(/import \{ useRouter \} from 'vue-router'/, `const useRouter = () => globalThis.${key}.router`)
    .replace(/import \{ ElMessage \} from 'element-plus'/, 'const ElMessage = { success() {}, info() {} }')
    .replace(/import \{([^}]+)\} from '@\/api\/preorganize'/g, (_, names) => names.split(',').map(name => name.trim()).filter(Boolean).map(name => `const ${name} = (...args) => globalThis.${key}.api.${name}(...args)`).join('\n'))
    .replace(/from '@\/utils\/preorganize'/g, `from '${new URL('../src/utils/preorganize.js', import.meta.url)}'`)
    .replace(/import \w+ from '@\/components\/[^']+'/g, match => `const ${match.split(' ')[1]} = {}`)
    .replace(/\bsetTimeout\(/g, `globalThis.${key}.setTimeout(`).replace(/\bclearTimeout\(/g, `globalThis.${key}.clearTimeout(`)
  const { default: component } = await import(`data:text/javascript;base64,${Buffer.from(script).toString('base64')}#${serial}`)
  const props = reactive({ selectedDocuments: [{ id: 'd1', name: '原文.txt', preorganize: { state: 'waiting' } }], counts: {}, filter: '', tags: [] })
  let state
  const scriptOnly = { ...component, setup(values, context) { state = component.setup(values, context); return () => null } }
  const app = renderer.createApp({ setup() { return () => h(scriptOnly, { ...props, onRefresh: () => events.push('refresh') }) } })
  app.mount({}); await settled()
  return { props, appStore, calls, events, timers, get state() { return state }, close() { app.unmount(); delete globalThis[key] } }
}
test('载入页面只读配置，选中批次一次授权后启动，不隐式开启自动模式', async () => {
  const f = await fixture()
  try {
    assert.equal(f.calls.start.length, 0); assert.equal(f.calls.policy.length, 0)
    await f.state.start(f.props.selectedDocuments)
    assert.equal(f.state.showConsent.value, true); assert.equal(f.calls.start.length, 0)
    await f.state.confirmConsent()
    assert.equal(f.calls.start.length, 1)
    assert.deepEqual(f.calls.start[0].data.document_ids, ['d1'])
    assert.equal(f.calls.start[0].data.consent, true)
    assert.equal(f.calls.start[0].data.library_token, 'lib-a')
    assert.equal(f.calls.start[0].data.service_revision, 'rev-a')
    assert.equal(f.calls.policy.length, 0)
  } finally { f.close() }
})
test('文件与文件夹混合批次保留整套资料身份，授权后仅提交各自对象编号', async () => {
  const f = await fixture()
  try {
    const folder = { id: 'folder-a', name: '验收资料.v2', is_folder: true, preorganize: { state: 'failed' } }
    f.props.selectedDocuments.push(folder)
    assert.equal(f.state.startable.value.length, 2)
    await f.state.start(f.state.startable.value)
    assert.equal(f.state.consentIncludesFolders.value, true)
    assert.equal(f.state.consentDocuments.value[1].is_folder, true)
    folder.id = 'changed-after-consent'
    await f.state.confirmConsent()
    assert.deepEqual(f.calls.start[0].data.document_ids, ['d1', 'folder-a'])
  } finally { f.close() }
})

test('图片和压缩包沿用一次批次授权，说明成员阅读且不增加额外确认', async () => {
  const f = await fixture()
  try {
    const docs = [{ id: 'image-a', name: '铭牌.png' }, { id: 'archive-a', name: '验收.TAR.GZ' }]
    await f.state.start(docs)
    assert.equal(f.state.showConsent.value, true)
    assert.equal(f.state.consentIncludesArchives.value, true)
    assert.equal(f.calls.start.length, 0)
    await f.state.confirmConsent()
    assert.equal(f.calls.start.length, 1)
    assert.deepEqual(f.calls.start[0].data.document_ids, ['image-a', 'archive-a'])
    assert.equal(f.state.showConsent.value, false)
  } finally { f.close() }
})

test('自动预整理说明包含未来新收录文件夹，单文件批次不显示成套资料说明', async () => {
  const f = await fixture()
  try {
    await f.state.start(f.props.selectedDocuments)
    assert.equal(f.state.consentIncludesFolders.value, false)
    f.state.showConsent.value = false
    await f.state.changePolicy(true)
    assert.equal(f.state.consentIncludesFolders.value, true)
  } finally { f.close() }
})
test('自动模式需要主动确认；关闭不要求外发授权', async () => {
  const f = await fixture()
  try {
    await f.state.changePolicy(true)
    assert.equal(f.calls.policy.length, 0)
    await f.state.confirmConsent()
    assert.equal(f.calls.policy[0].data.enabled, true)
    assert.equal(f.calls.policy[0].data.consent, true)
    await f.state.changePolicy(false)
    assert.equal(f.calls.policy[1].data.enabled, false)
    assert.notEqual(f.calls.policy[1].data.consent, true)
  } finally { f.close() }
})
test('未配置AI不启动批次，仍可阅读已有方案与撤销', async () => {
  const f = await fixture({ getPreorganizeConfig: async () => ({ ...configuration, configured: false }) })
  try {
    await f.state.start(f.props.selectedDocuments)
    assert.equal(f.state.showConsent.value, false); assert.match(f.state.actionError.value, /配置/)
    await f.state.review([{ id: 'd1', name: '原文.txt', preorganize: { state: 'ready' } }])
    assert.equal(f.state.showReview.value, true)
    await f.state.undo({ id: 'op1' })
    assert.equal(f.calls.undo.length, 1)
  } finally { f.close() }
})
test('取消绑定当前库；撤销冲突留在记录中并显示原因', async () => {
  const f = await fixture({ undoPreorganize: async () => { throw new Error('资料已有后续修改，不能撤销') } })
  try {
    await f.state.cancel([{ id: 'd1' }])
    assert.equal(f.calls.cancel[0].data.library_token, 'lib-a')
    await f.state.openOperations()
    await f.state.undo({ id: 'op1' })
    assert.match(f.state.operationError.value, /后续修改/)
    assert.equal(f.state.operations.value.length, 1)
  } finally { f.close() }
})
test('切库取消正在准备的批次，迟到配置不能重新显示旧库授权弹窗', async () => {
  let call = 0; const gate = deferred(); let oldSignal
  const f = await fixture({ getPreorganizeConfig: async signal => {
    if (++call === 2) { oldSignal = signal; return gate.promise }
    return { ...configuration, library_token: call > 2 ? 'lib-b' : 'lib-a' }
  } })
  try {
    const pending = f.state.start(f.props.selectedDocuments)
    await settled(); f.appStore.libraryPath = '/library-b'; await settled()
    assert.equal(oldSignal.aborted, true)
    gate.resolve(configuration); await pending; await settled()
    assert.equal(f.state.showConsent.value, false)
    assert.equal(f.state.config.value.library_token, 'lib-b')
  } finally { f.close() }
})
