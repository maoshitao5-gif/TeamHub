import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import compiler from '../node_modules/@vue/compiler-sfc/dist/compiler-sfc.cjs.js'
import { createRenderer, h, nextTick, reactive } from '../node_modules/vue/dist/vue.runtime.esm-bundler.js'

const vueURL = new URL('../node_modules/vue/dist/vue.runtime.esm-bundler.js', import.meta.url)
const renderer = createRenderer({
  createComment: () => ({}), createElement: () => ({}), createText: () => ({}),
  insert() {}, remove() {}, patchProp() {}, setElementText() {}, setText() {}, setComment() {},
  parentNode: () => null, nextSibling: () => null,
})
let serial = 0
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done }); return { promise, resolve } }
async function settled() { for (let i = 0; i < 12; i++) { await Promise.resolve(); await nextTick() } }
const detail = id => ({ document_id: id, state: 'ready', proposal_id: `p-${id}`, proposal: { name: `${id}-建议.txt`, tags: ['资料'], target_dir: '', summary: '说明', gaps: [] } })

async function fixture(overrides = {}) {
  const calls = { details: [], apply: [] }, events = []
  const appStore = reactive({ libraryPath: '/library-a' })
  const api = {
    async getPreorganizeDocument(id, signal) { calls.details.push({ id, signal }); return detail(id) },
    async applyPreorganize(data, signal) { calls.apply.push({ data, signal }); return { items: data.items.map(item => ({ document_id: item.document_id, ok: true })) } },
    ...overrides,
  }
  const key = `__preorganize_review_${++serial}`
  globalThis[key] = { api, appStore, messages: [] }
  const source = await fs.readFile(new URL('../src/components/PreorganizeReviewDialog.vue', import.meta.url), 'utf8')
  let script = compiler.compileScript(compiler.parse(source).descriptor, { id: key }).content
  script = script.replace(/from 'vue'/g, `from '${vueURL}'`)
    .replace(/import \{ useAppStore \} from '@\/stores\/app'/, `const useAppStore = () => globalThis.${key}.appStore`)
    .replace(/import \{ ElMessage \} from 'element-plus'/, `const ElMessage = { success: text => globalThis.${key}.messages.push(text) }`)
    .replace(/import \{([^}]+)\} from '@\/api\/preorganize'/g, (_, names) => names.split(',').map(name => name.trim()).map(name => `const ${name} = (...args) => globalThis.${key}.api.${name}(...args)`).join('\n'))
    .replace(/from '@\/utils\/preorganize'/g, `from '${new URL('../src/utils/preorganize.js', import.meta.url)}'`)
    .replace(/import \w+ from '@\/components\/[^']+'/g, match => `const ${match.split(' ')[1]} = {}`)
  const { default: component } = await import(`data:text/javascript;base64,${Buffer.from(script).toString('base64')}#${serial}`)
  const props = reactive({ modelValue: false, documents: [{ id: 'd1', name: '原文.txt' }, { id: 'd2', name: '附件.txt' }], libraryToken: 'lib-a', tags: [] })
  let state
  const scriptOnly = { ...component, setup(values, context) { state = component.setup(values, context); return () => null } }
  const app = renderer.createApp({ setup() { return () => h(scriptOnly, {
    ...props, 'onUpdate:modelValue': value => { props.modelValue = value }, onApplied: value => events.push(value),
  }) } })
  app.mount({})
  return { props, appStore, calls, events, get state() { return state },
    open: async () => { props.modelValue = true; await settled() },
    close: () => { app.unmount(); delete globalThis[key] },
  }
}

test('审阅一次读取固定方案；列表轮询不覆盖用户名称和标签', async () => {
  const f = await fixture()
  try {
    await f.open()
    f.state.items.value[0].name = '人工名称'
    f.state.items.value[0].tags.push('现场')
    f.props.documents = []
    await settled()
    assert.equal(f.calls.details.length, 2)
    assert.equal(f.state.items.value[0].name, '人工名称')
    assert.deepEqual(f.state.items.value[0].tags, ['资料', '现场'])
    await f.state.apply()
    assert.equal(f.calls.apply.length, 1)
    assert.equal(f.calls.apply[0].data.library_token, 'lib-a')
    assert.equal(f.calls.apply[0].data.items[0].name, '人工名称.txt')
    assert.equal(f.props.modelValue, false)
  } finally { f.close() }
})

test('文件夹方案确认始终作用于整套资料，带点名称不当作文件扩展名', async () => {
  const f = await fixture({ getPreorganizeDocument: async id => ({
    ...detail(id), source_version: { id: 'folder-version', number: 2 },
    proposal: { name: '工程验收资料.v2', tags: ['工程验收'], target_dir: '项目归档', summary: '含报告与附件的成套验收资料', gaps: [] },
  }) })
  try {
    f.props.documents = [{ id: 'folder-a', name: '项目资料.v1', is_folder: true }]
    await f.open()
    const item = f.state.items.value[0]
    assert.equal(item.is_folder, true)
    assert.equal(item.extension, '')
    assert.equal(item.name, '工程验收资料.v2')
    f.state.toggleCard(item.document_id, { target: { open: true } })
    assert.deepEqual(f.state.expandedCards.value, ['folder-a'])
    assert.equal(f.calls.apply.length, 0)
    await f.state.apply()
    assert.equal(f.calls.apply[0].data.items.length, 1)
    assert.deepEqual({ ...f.calls.apply[0].data.items[0], request_id: 'id' }, {
      document_id: 'folder-a', proposal_id: 'p-folder-a', request_id: 'id', name: '工程验收资料.v2', tags: ['工程验收'], target_dir: '项目归档',
    })
  } finally { f.close() }
})

test('压缩包方案保留后台识别类型与复合扩展名，确认只提交整个资料对象', async () => {
  const f = await fixture({ getPreorganizeDocument: async id => ({
    ...detail(id), object_kind: 'archive', source_version: { id: 'archive-version', number: 1 },
    proposal: { name: '工程验收.v2.tar.gz', tags: ['工程验收'], target_dir: '项目归档', summary: '验收报告和图片', gaps: [] },
  }) })
  try {
    f.props.documents = [{ id: 'archive-a', name: '待命名.tar.gz', object_kind: 'unknown' }]
    await f.open()
    const item = f.state.items.value[0]
    assert.equal(item.object_kind, 'archive')
    assert.equal(item.is_folder, false)
    assert.equal(item.extension, '.tar.gz')
    assert.equal(item.name, '工程验收.v2')
    f.state.toggleCard(item.document_id, { target: { open: true } })
    assert.deepEqual(f.state.expandedCards.value, ['archive-a'])
    await f.state.apply()
    assert.equal(f.calls.apply.length, 1)
    assert.equal(f.calls.apply[0].data.items.length, 1)
    assert.equal(f.calls.apply[0].data.items[0].document_id, 'archive-a')
    assert.equal(f.calls.apply[0].data.items[0].name, '工程验收.v2.tar.gz')
    assert.equal(f.calls.apply[0].data.items[0].object_kind, undefined)
  } finally { f.close() }
})

test('批量部分失败保留人工输入和失败原因，仅成功项离开审阅', async () => {
  const f = await fixture({ applyPreorganize: async () => ({ items: [{ document_id: 'd1', ok: true }, { document_id: 'd2', ok: false, error: '工作内容已改变' }] }) })
  try {
    await f.open()
    f.state.items.value[1].name = '保留修改'
    await f.state.apply()
    assert.equal(f.props.modelValue, true)
    assert.equal(f.state.items.value.length, 1)
    assert.equal(f.state.items.value[0].name, '保留修改')
    assert.equal(f.state.items.value[0].error, '工作内容已改变')
    assert.equal(f.events[0], 1)
  } finally { f.close() }
})

for (const change of ['close', 'library', 'unmount']) {
  test(`审阅读取期间 ${change} 中止请求并丢弃迟到资料`, async () => {
    const gate = deferred(); let signal
    const f = await fixture({ getPreorganizeDocument: (id, value) => { signal = value; return gate.promise } })
    try {
      await f.open()
      const state = f.state
      if (change === 'close') f.props.modelValue = false
      else if (change === 'library') f.appStore.libraryPath = '/library-b'
      else f.close()
      await settled()
      assert.equal(signal.aborted, true)
      gate.resolve(detail('d1')); await settled()
      assert.equal(state.items.value.length, 0)
    } finally { if (change !== 'unmount') f.close() }
  })
}

test('响应未知时锁定已提交内容，同一请求重试不会改用新编号', async () => {
  let count = 0; const requests = []
  const f = await fixture({ applyPreorganize: async payload => {
    requests.push(structuredClone(payload)); if (++count === 1) throw new Error('连接中断')
    return { items: payload.items.map(item => ({ document_id: item.document_id, ok: true })) }
  } })
  try {
    await f.open(); await f.state.apply()
    assert.equal(f.state.items.value[0].uncertain, true)
    const id = requests[0].items[0].request_id
    await f.state.apply()
    assert.equal(requests[1].items[0].request_id, id)
    assert.deepEqual(requests[1], requests[0])
  } finally { f.close() }
})
