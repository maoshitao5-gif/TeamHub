import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import compiler from '../node_modules/@vue/compiler-sfc/dist/compiler-sfc.cjs.js'
import { createRenderer } from '../node_modules/vue/dist/vue.runtime.esm-bundler.js'

const renderer = createRenderer({ createComment: () => ({}), insert() {}, remove() {}, parentNode: () => null, nextSibling: () => null })
const vueURL = new URL('../node_modules/vue/dist/vue.runtime.esm-bundler.js', import.meta.url)
let serial = 0
async function fixture() {
  const requests = [], events = [], timers = new Map()
  let timerId = 0, state
  const key = `__search_suggestions_${++serial}`
  globalThis[key] = {
    autocompleteDocuments: () => new Promise(resolve => requests.push(resolve)),
    setTimeout: callback => { const id = ++timerId; timers.set(id, callback); return id },
    clearTimeout: id => timers.delete(id),
  }
  const source = await fs.readFile(new URL('../src/components/SmartSearchBar.vue', import.meta.url), 'utf8')
  let script = compiler.compileScript(compiler.parse(source).descriptor, { id: key }).content
  script = script.replace(/from 'vue'/g, `from '${vueURL}'`)
    .replace(/import \{[^}]*\} from '@element-plus\/icons-vue'/, '')
    .replace(/import \{ autocompleteDocuments \} from '@\/api\/document'/, `const { autocompleteDocuments } = globalThis.${key}`)
    .replace(/import \{ suggestTags, getRelatedTags \} from '@\/api\/tag'/, 'const suggestTags = async () => [], getRelatedTags = async () => []')
    .replace(/\bsetTimeout\(/g, `globalThis.${key}.setTimeout(`)
    .replace(/\bclearTimeout\(/g, `globalThis.${key}.clearTimeout(`)
  script = 'const document = { addEventListener() {}, removeEventListener() {} };\n' + script
  // 图标只是模板绑定，脚本测试无需渲染。
  script = 'const Search=null, CircleClose=null, Document=null, Folder=null, CollectionTag=null, Sort=null;\n' + script
  const { default: component } = await import(`data:text/javascript;base64,${Buffer.from(script).toString('base64')}`)
  const app = renderer.createApp({ ...component, setup(props, context) {
    state = component.setup(props, { ...context, emit: (...args) => events.push(args) })
    return () => null
  } })
  app.mount({})
  state.isFocused.value = true; state.inputVal.value = '正文关键词'
  return { state, requests, events, timers, close() { app.unmount(); delete globalThis[key] } }
}

test('按回车提交正文搜索后，已在途的名称联想不能重新遮挡结果', async () => {
  const f = await fixture()
  try {
    const pending = f.state.fetchSuggestions()
    f.state.onEnter()
    f.requests[0]([]); await pending
    assert.equal(f.state.showDropdown.value, false)
    assert.deepEqual(f.events, [['name-search', '正文关键词']])
  } finally { f.close() }
})

test('按回车取消尚未执行的防抖联想；失焦后迟到联想不重开', async () => {
  const f = await fixture()
  try {
    f.state.onInput(); f.state.onEnter()
    assert.equal(f.timers.size, 0)
    const pending = f.state.fetchSuggestions()
    f.state.onBlur(); f.requests[0]([{ id: 'old', name: '旧名称' }]); await pending
    assert.equal(f.state.showDropdown.value, false)
  } finally { f.close() }
})
