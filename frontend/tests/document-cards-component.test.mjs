import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import compiler from '../node_modules/@vue/compiler-sfc/dist/compiler-sfc.cjs.js'
import { createRenderer, h, nextTick, reactive } from '../node_modules/vue/dist/vue.runtime.esm-bundler.js'

const vueURL = new URL('../node_modules/vue/dist/vue.runtime.esm-bundler.js', import.meta.url)
const renderer = createRenderer({ createComment: () => ({}), createElement: () => ({}), createText: () => ({}), insert() {}, remove() {}, patchProp() {}, setElementText() {}, setText() {}, setComment() {}, parentNode: () => null, nextSibling: () => null })
const settings = () => ({ library_token: 'lib-a', service_revision: 'service-a', configured: true, service: { base_url: 'https://ai.example', model: 'fixture' } })
const detail = (id = 'doc-a', version = 'version-a') => ({ document_id: id, version_id: version, version_number: 1, state: 'missing', card: null, annotation: { text: '', revision: 0, source_version_id: version, needs_review: false }, job: { state: 'waiting' }, policy: { enabled: false, active: false } })
const card = { content: { summary: '气室压力巡视记录', document_type: '巡视记录', topics: ['断路器'], sections: [], facts: [], search_terms: ['气室压力'], uses: ['查找巡视记录'], gaps: ['时间未知'], source_ids: ['s1'] }, sources: [{ id: 's1', label: '表1 A1', excerpt: '巡视记录' }], coverage: { total_units: 2, read_units: 1 }, revision: 1, version_id: 'version-a', version_number: 1 }
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done }); return { promise, resolve } }
async function settled() { for (let i = 0; i < 16; i++) { await Promise.resolve(); await nextTick() } }
let serial = 0
async function fixture(overrides = {}) {
  const calls = { config: [], get: [], generate: [], cancel: [], policy: [], annotation: [] }, timers = new Map(), routes = []
  const appStore = reactive({ libraryPath: '/library-a' })
  const api = {
    async getDocumentCardsConfig(signal) { calls.config.push(signal); return settings() },
    async getDocumentCard(id, signal) { calls.get.push({ id, signal }); return detail(id) },
    async generateDocumentCard(id, data, signal) { calls.generate.push({ id, data, signal }); return {} },
    async cancelDocumentCard(id, data, signal) { calls.cancel.push({ id, data, signal }); return {} },
    async setDocumentCardPolicy(id, data, signal) { calls.policy.push({ id, data, signal }); return {} },
    async saveDocumentCardAnnotation(id, data, signal) { calls.annotation.push({ id, data, signal }); return {} },
    ...overrides,
  }
  const key = `__card_component_${++serial}`
  globalThis[key] = { api, appStore, router: { push: value => routes.push(value) }, setTimeout: callback => { const id = Symbol(); timers.set(id, callback); return id }, clearTimeout: id => timers.delete(id) }
  const source = await fs.readFile(new URL('../src/components/DocumentUnderstandingCard.vue', import.meta.url), 'utf8')
  let script = compiler.compileScript(compiler.parse(source).descriptor, { id: key }).content
  script = script.replace(/from 'vue'/g, `from '${vueURL}'`)
    .replace(/import \{ useRouter \} from 'vue-router'/, `const useRouter = () => globalThis.${key}.router`)
    .replace(/import \{ useAppStore \} from '@\/stores\/app'/, `const useAppStore = () => globalThis.${key}.appStore`)
    .replace(/import \{([^}]+)\} from '@\/api\/documentCards'/g, (_, names) => names.split(',').map(name => name.trim()).filter(Boolean).map(name => `const ${name} = (...args) => globalThis.${key}.api.${name}(...args)`).join('\n'))
    .replace(/\bsetTimeout\(/g, `globalThis.${key}.setTimeout(`).replace(/\bclearTimeout\(/g, `globalThis.${key}.clearTimeout(`)
  const { default: component } = await import(`data:text/javascript;base64,${Buffer.from(script).toString('base64')}#${serial}`)
  const props = reactive({ visible: false, compact: false, document: { id: 'doc-a', name: '巡视.xlsx', status: 'pending', current_version: { id: 'version-a' } } })
  let state
  const scriptOnly = { ...component, setup(values, context) { state = component.setup(values, context); return () => null } }
  const app = renderer.createApp({ setup() { return () => h(scriptOnly, { ...props }) } })
  app.mount({})
  return { props, calls, api, appStore, timers, routes, source, get state() { return state }, open: async () => { props.visible = true; await settled() }, tick: async () => { const items = [...timers.values()]; timers.clear(); for (const callback of items) callback(); await settled() }, close: () => { app.unmount(); delete globalThis[key] } }
}

test('打开只读卡片，生成由一次明确点击授权且默认不启用持续分析', async () => {
  const f = await fixture()
  try {
    await f.open()
    assert.equal(f.calls.generate.length, 0)
    assert.equal(f.state.autoUpdate.value, false)
    await f.state.generate()
    assert.equal(f.calls.generate.length, 1)
    assert.deepEqual({ ...f.calls.generate[0].data, request_id: 'id' }, { library_token: 'lib-a', service_revision: 'service-a', consent: true, request_id: 'id', auto_update: false })
    assert.ok(f.calls.generate[0].data.request_id)
    assert.match(f.source, /可能产生费用/)
    assert.match(f.source, /已保存版本的文字与必要图像/)
  } finally { f.close() }
})

test('成套文件夹读取当前资料卡并作为一个对象生成，默认不持续分析', async () => {
  const f = await fixture()
  try {
    f.props.document.is_folder = true
    f.props.document.name = '工程资料.v2'
    await f.open()
    assert.equal(f.calls.get.length, 1)
    assert.equal(f.calls.get[0].id, 'doc-a')
    assert.equal(f.calls.generate.length, 0)
    await f.state.generate()
    assert.equal(f.calls.generate.length, 1)
    assert.equal(f.calls.generate[0].id, 'doc-a')
    assert.equal(f.calls.generate[0].data.auto_update, false)
    assert.equal(f.calls.generate[0].data.consent, true)
  } finally { f.close() }
})

test('压缩包资料卡使用后台对象类型，显示成员覆盖并沿用直接生成入口', async () => {
  const f = await fixture({ getDocumentCard: async () => ({ ...detail(), object_kind: 'archive', state: 'partial', card: {
    ...card, coverage: { total_members: 4, read_members: 2, total_units: 10, read_units: 5, gaps: ['附件/旧图纸.dwg 尚未识别'] },
  } }) })
  try {
    f.props.document.name = '没有后缀的资料'
    await f.open()
    assert.equal(f.state.objectKindLabel.value, '压缩包')
    assert.equal(f.state.isArchive.value, true)
    assert.match(f.state.coverageText.value, /已阅读成员 2 \/ 4/)
    assert.match(f.state.coverageText.value, /2 个成员尚未阅读/)
    assert.equal(f.state.stateLabel.value, '部分理解')
    await f.state.generate()
    assert.equal(f.calls.generate.length, 1)
    assert.equal(f.calls.generate[0].data.consent, true)
    assert.match(f.source, /保留压缩格式、内部文件名和目录结构/)
  } finally { f.close() }
})

test('图片对象类型只展示后台识别结果，未知类型不从文件名称推断', async () => {
  const f = await fixture({ getDocumentCard: async () => ({ ...detail(), object_kind: 'image' }) })
  try {
    await f.open()
    assert.equal(f.state.objectKindLabel.value, '图片')
    f.api.getDocumentCard = async () => ({ ...detail(), object_kind: 'unknown' })
    f.props.document.name = '看似图片.png'
    await f.state.refresh()
    assert.equal(f.state.objectKindLabel.value, '')
  } finally { f.close() }
})

for (const includeUnread of [true, false]) {
test(`文件夹部分阅读显示成员覆盖和缺口，依据保留相对路径（未读数${includeUnread ? '显式返回' : '由覆盖计算'}）`, async () => {
  const f = await fixture({ getDocumentCard: async () => ({ ...detail(), state: 'partial', card: {
    ...card, coverage: { total_members: 5, read_members: 2, ...(includeUnread ? { unread_members: 3 } : {}), total_units: 20, read_units: 7, gaps: ['附件/图纸.dwg 尚未识别'] },
    sources: [{ id: 's1', label: '记录/巡视.xlsx · 巡视记录 A2', excerpt: '气室压力巡视记录' }, { id: 's2', location: { member_path: '附件/说明.txt', line: 2 } }],
  } }) })
  try {
    f.props.document.is_folder = true
    await f.open()
    assert.match(f.state.coverageText.value, /已阅读成员 2 \/ 5/)
    assert.match(f.state.coverageText.value, /3 个成员尚未阅读/)
    assert.match(f.state.coverageText.value, /原文单元 7 \/ 20/)
    assert.equal(f.state.sourceLabels(['s1']), '记录/巡视.xlsx · 巡视记录 A2')
    assert.equal(f.state.sourceLabels(['s2']), '文件 附件/说明.txt · 行 2')
    assert.ok(f.state.cardGaps.value.includes('附件/图纸.dwg 尚未识别'))
    assert.equal(f.state.stateLabel.value, '部分理解')
  } finally { f.close() }
})
}

test('成员覆盖字段缺省时不推测已读文件数', async () => {
  const f = await fixture({ getDocumentCard: async () => ({ ...detail(), state: 'ready', card }) })
  try {
    await f.open()
    assert.doesNotMatch(f.state.coverageText.value, /成员/)
    assert.match(f.state.coverageText.value, /原文单元 1 \/ 2/)
  } finally { f.close() }
})

test('服务变更后展示新服务并等待再次点击，不向新服务自动发送', async () => {
  let n = 0
  const f = await fixture({ getDocumentCardsConfig: async () => ++n === 1 ? settings() : { ...settings(), service_revision: 'service-b', service: { base_url: 'https://new.example', model: 'new' } } })
  try {
    await f.open(); await f.state.generate()
    assert.equal(f.calls.generate.length, 0)
    assert.equal(f.state.config.value.service.base_url, 'https://new.example')
    assert.match(f.state.error.value, /服务.*变化|服务.*变更/)
    await f.state.generate()
    assert.equal(f.calls.generate.length, 1)
    assert.equal(f.calls.generate[0].data.service_revision, 'service-b')
  } finally { f.close() }
})

test('卡片部分理解保留缺口；过期卡片不冒充当前版本', async () => {
  const f = await fixture({ getDocumentCard: async () => ({ ...detail(), state: 'partial', card: { ...card, coverage: { ...card.coverage, gaps: ['时间未知', '有图像未读取'] } } }) })
  try {
    await f.open(); assert.match(f.state.stateLabel.value, /部分/)
    assert.equal(f.state.currentCard.value.content.summary, card.content.summary)
    assert.deepEqual(f.state.cardGaps.value, ['时间未知', '有图像未读取'])
    f.api.getDocumentCard = async () => ({ ...detail(), state: 'stale', card: null, previous_card: card })
    await f.state.refresh()
    assert.equal(f.state.currentCard.value, null)
    assert.equal(f.state.previousCard.value.content.summary, card.content.summary)
    assert.match(f.state.stateLabel.value, /更新|旧版/)
  } finally { f.close() }
})

test('人工补充 4000 字可保存，超长输入保留并阻止提交', async () => {
  const f = await fixture()
  try {
    await f.open(); f.state.annotationText.value = '字'.repeat(4001)
    await f.state.saveAnnotation()
    assert.equal(f.calls.annotation.length, 0)
    assert.equal(f.state.annotationText.value.length, 4001)
    assert.match(f.state.annotationError.value, /4000/)
    f.state.annotationText.value = '字'.repeat(4000)
    await f.state.saveAnnotation()
    assert.equal(f.calls.annotation.length, 1)
    assert.equal(f.calls.annotation[0].data.text.length, 4000)
  } finally { f.close() }
})

test('任务轮询完成即停止；取消带库授权且不会自动重新生成', async () => {
  let n = 0
  const f = await fixture({ getDocumentCard: async () => ({ ...detail(), job: { state: ++n < 3 ? 'running' : 'ready' } }) })
  try {
    await f.open(); assert.equal(f.timers.size, 1)
    await f.tick(); assert.equal(f.timers.size, 1)
    await f.state.cancel()
    assert.equal(f.calls.cancel.length, 1); assert.equal(f.calls.cancel[0].data.library_token, 'lib-a')
    assert.equal(f.timers.size, 0); assert.equal(f.calls.generate.length, 0)
  } finally { f.close() }
})

for (const change of ['document', 'library', 'version', 'close', 'unmount']) {
  test(`切换 ${change} 会中止请求并丢弃旧卡片`, async () => {
    const gate = deferred(); let oldSignal, reads = 0
    const f = await fixture({ getDocumentCard: async (id, signal) => { if (++reads === 1) { oldSignal = signal; return gate.promise }; return detail(id, change === 'version' ? 'version-b' : 'version-a') } })
    try {
      await f.open(); const state = f.state
      if (change === 'document') f.props.document.id = 'doc-b'
      else if (change === 'library') f.appStore.libraryPath = '/library-b'
      else if (change === 'version') f.props.document.current_version.id = 'version-b'
      else if (change === 'close') f.props.visible = false
      else f.close()
      await settled(); assert.equal(oldSignal.aborted, true)
      gate.resolve({ ...detail(), state: 'ready', card }); await settled()
      assert.equal(state.currentCard.value, null)
    } finally { if (change !== 'unmount') f.close() }
  })
}

test('版本错误的响应拒绝展示且不能用于发送生成请求', async () => {
  const f = await fixture({ getDocumentCard: async () => ({ ...detail('doc-a', 'wrong-version'), state: 'ready', card }) })
  try {
    await f.open(); assert.equal(f.state.currentCard.value, null)
    assert.match(f.state.error.value, /版本|不一致/)
    await f.state.generate(); assert.equal(f.calls.generate.length, 0)
  } finally { f.close() }
})

test('并发补充冲突保留人工输入与旧基线，刷新核对后才允许采用新基线', async () => {
  let revision = 1
  const f = await fixture({
    getDocumentCard: async () => ({ ...detail(), annotation: { text: revision === 1 ? '原补充' : '另一窗口补充', revision, source_version_id: 'version-a', needs_review: false } }),
    saveDocumentCardAnnotation: async () => { revision = 2; const error = new Error('补充已更新'); error.response = { status: 409 }; throw error },
  })
  try {
    await f.open(); f.state.annotationText.value = '我的人工修正'
    await f.state.saveAnnotation(); assert.equal(f.state.annotationConflict.value, true)
    assert.equal(f.state.annotationText.value, '我的人工修正')
    await f.state.refresh()
    assert.equal(f.state.annotationText.value, '我的人工修正')
    assert.equal(f.state.annotationRevision.value, 1)
    assert.equal(f.state.snapshot.value.annotation.text, '另一窗口补充')
    f.state.rebaseAnnotation()
    assert.equal(f.state.annotationRevision.value, 2)
    assert.equal(f.state.annotationText.value, '我的人工修正')
    assert.equal(f.state.annotationConflict.value, false)
  } finally { f.close() }
})

test('版本冲突后即使父组件仍是旧版本，也能刷新核对并明确将草稿保存到新版本', async () => {
  let version = 'version-a', revision = 1
  const writes = []
  const f = await fixture({
    getDocumentCard: async () => ({ ...detail('doc-a', version), version_number: version === 'version-a' ? 1 : 2, annotation: { text: '旧补充', revision, source_version_id: 'version-a', needs_review: version !== 'version-a' } }),
    saveDocumentCardAnnotation: async (id, data) => {
      writes.push(data)
      if (data.version_id !== version) { const error = new Error('资料版本已变化'); error.response = { status: 409 }; throw error }
      revision++
    },
  })
  try {
    await f.open(); f.state.annotationText.value = '我基于 v1 正在编辑的说明'; version = 'version-b'
    await f.state.saveAnnotation()
    assert.equal(f.state.annotationConflict.value, true)
    await f.state.refresh()
    assert.equal(f.state.snapshot.value.version_id, 'version-b')
    assert.equal(f.state.annotationText.value, '我基于 v1 正在编辑的说明')
    assert.equal(f.state.annotationVersion.value, 'version-a')
    assert.equal(f.state.canRebaseAnnotation.value, true)
    assert.match(f.state.annotationRebaseLabel.value, /v2/)
    await f.state.saveAnnotation(); assert.equal(writes.length, 1)
    f.state.rebaseAnnotation()
    assert.equal(f.state.annotationVersion.value, 'version-b')
    assert.equal(f.state.annotationConflict.value, false)
    await f.state.saveAnnotation()
    assert.equal(writes.length, 2)
    assert.equal(writes[1].version_id, 'version-b')
    assert.equal(writes[1].expected_revision, 1)
    assert.equal(writes[1].text, '我基于 v1 正在编辑的说明')
  } finally { f.close() }
})

test('同一资料版本更新保留草稿但阻止直接写入；换资料或库清除旧草稿', async () => {
  let version = 'version-a'
  const f = await fixture({ getDocumentCard: async id => ({ ...detail(id, version), version_number: version === 'version-a' ? 1 : 2 }) })
  try {
    await f.open(); f.state.annotationText.value = '尚未保存的修改'; version = 'version-b'
    f.props.document.current_version.id = 'version-b'; await settled()
    assert.equal(f.state.annotationText.value, '尚未保存的修改')
    assert.equal(f.state.annotationConflict.value, true)
    assert.equal(f.state.annotationVersion.value, 'version-a')
    await f.state.saveAnnotation(); assert.equal(f.calls.annotation.length, 0)
    f.state.rebaseAnnotation(); await f.state.saveAnnotation()
    assert.equal(f.calls.annotation[0].data.version_id, 'version-b')
    f.state.annotationText.value = '不能串页的文本'
    f.props.document.id = 'doc-b'; await settled()
    assert.equal(f.state.annotationText.value, '')
    f.state.annotationText.value = '不能串库的文本'
    f.appStore.libraryPath = '/library-b'; await settled()
    assert.equal(f.state.annotationText.value, '')
  } finally { f.close() }
})

test('编辑时后台刷新发现新版本，也必须明确核对再绑定草稿', async () => {
  let version = 'version-a'
  const f = await fixture({ getDocumentCard: async () => ({ ...detail('doc-a', version), version_number: version === 'version-a' ? 1 : 2 }) })
  try {
    await f.open(); f.state.annotationText.value = '旧版观察'; version = 'version-b'; await f.state.refresh()
    assert.equal(f.state.annotationVersion.value, 'version-a')
    assert.equal(f.state.annotationConflict.value, true)
    assert.equal(f.state.annotationText.value, '旧版观察')
    f.state.rebaseAnnotation()
    f.props.document.current_version.id = 'version-b'; await settled()
    assert.equal(f.state.annotationVersion.value, 'version-b')
    assert.equal(f.state.annotationConflict.value, false)
    assert.equal(f.state.annotationText.value, '旧版观察')
  } finally { f.close() }
})

test('保存期间切换同资料版本会中止旧请求，迟到成功不清空待核对草稿', async () => {
  const gate = deferred(); let version = 'version-a', requestSignal
  const f = await fixture({
    getDocumentCard: async () => ({ ...detail('doc-a', version), version_number: version === 'version-a' ? 1 : 2 }),
    saveDocumentCardAnnotation: async (id, data, signal) => { requestSignal = signal; return gate.promise },
  })
  try {
    await f.open(); f.state.annotationText.value = '正在保存的草稿'
    const pending = f.state.saveAnnotation()
    await settled(); version = 'version-b'; f.props.document.current_version.id = 'version-b'; await settled()
    assert.equal(requestSignal.aborted, true)
    assert.equal(f.state.annotationText.value, '正在保存的草稿')
    assert.equal(f.state.annotationConflict.value, true)
    gate.resolve({}); await pending; await settled()
    assert.equal(f.state.annotationText.value, '正在保存的草稿')
    assert.equal(f.state.annotationVersion.value, 'version-a')
    assert.equal(f.state.annotationSaved.value, false)
    assert.equal(f.state.canRebaseAnnotation.value, true)
  } finally { f.close() }
})

test('同版本因规则过期的卡片不显示成旧文件版本', async () => {
  const f = await fixture({ getDocumentCard: async () => ({ ...detail(), state: 'stale', previous_card: card }) })
  try {
    await f.open()
    assert.match(f.state.previousCardLabel.value, /待更新.*v1/)
    assert.match(f.state.previousCardNotice.value, /规则/)
    assert.doesNotMatch(f.state.previousCardNotice.value, /来自旧版本/)
    f.api.getDocumentCard = async () => ({ ...detail(), state: 'stale', previous_card: { ...card, version_id: 'version-older', version_number: 0 } })
    await f.state.refresh()
    assert.match(f.state.previousCardNotice.value, /旧版本/)
  } finally { f.close() }
})

test('普通刷新及 AI 生成不覆盖尚未保存的人工补充', async () => {
  const f = await fixture()
  try {
    await f.open(); f.state.annotationText.value = '我知道的背景'
    await f.state.generate(); await f.state.refresh()
    assert.equal(f.state.annotationText.value, '我知道的背景')
    await f.state.saveAnnotation()
    assert.deepEqual(f.calls.annotation[0].data, { library_token: 'lib-a', version_id: 'version-a', expected_revision: 0, text: '我知道的背景' })
  } finally { f.close() }
})

test('未配置服务仍可阅读卡片和保存补充，不能发起分析，设置入口明确', async () => {
  const f = await fixture({ getDocumentCardsConfig: async () => ({ ...settings(), configured: false }) })
  try {
    await f.open(); await f.state.generate(); assert.equal(f.calls.generate.length, 0)
    f.state.annotationText.value = '人工背景'; await f.state.saveAnnotation(); assert.equal(f.calls.annotation.length, 1)
    f.state.openSettings(); assert.deepEqual(f.routes, ['/settings'])
  } finally { f.close() }
})

test('审阅只读卡片不能产生生成、策略或补充写入', async () => {
  const f = await fixture()
  try {
    f.props.compact = true; await f.open()
    await f.state.generate(); await f.state.cancel(); await f.state.disableAutoUpdate(); await f.state.saveAnnotation()
    for (const kind of ['generate', 'cancel', 'policy', 'annotation']) assert.equal(f.calls[kind].length, 0)
  } finally { f.close() }
})
