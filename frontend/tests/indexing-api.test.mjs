import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'

test('索引请求继承 AI 能力凭证、取消信号、静默失败与逐次发送授权', async () => {
  const calls = []
  globalThis.window = { electron: { aiCapability: 'index-capability-a' } }
  globalThis.__index_api_fixture = Object.fromEntries(['get', 'post'].map(method => [method, (...args) => {
    calls.push({ method, args }); return Promise.resolve({})
  }]))
  try {
    const source = (await fs.readFile(new URL('../src/api/indexing.js', import.meta.url), 'utf8'))
      .replace("import request from './request'", 'const request = globalThis.__index_api_fixture')
    const api = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`)
    const signal = new AbortController().signal
    await api.getDocumentIndex('doc /a', signal)
    window.electron.aiCapability = 'index-capability-b'
    await api.rebuildDocumentIndex('doc /a', signal)
    await api.prepareIndexEnrichment('doc /a', signal)
    await api.enrichDocumentIndex('prepare-a', signal)
    assert.equal(calls[0].args[0], '/api/ai/indexes/doc%20%2Fa')
    assert.deepEqual(calls[0].args[1].params, { offset: 0, limit: 20 })
    assert.equal(calls[1].args[0], '/api/ai/indexes/doc%20%2Fa/rebuild')
    assert.equal(calls[2].args[0], '/api/ai/indexes/doc%20%2Fa/prepare')
    assert.deepEqual(calls[3].args.slice(0, 2), ['/api/ai/indexes/enrich', { preparation_id: 'prepare-a', consent: true }])
    for (const [index, call] of calls.entries()) {
      const config = call.args.at(-1)
      assert.equal(config.headers['X-TeamHub-AI'], '1')
      assert.equal(config.headers['X-TeamHub-AI-Capability'], `index-capability-${index ? 'b' : 'a'}`)
      assert.equal(config.signal, signal)
      assert.equal(config._silent, true)
      assert.equal(config.timeout, index === 3 ? 90000 : 30000)
    }
    window.electron = undefined
    await api.getDocumentIndex('browser-doc', signal, { offset: 20, limit: 10 })
    assert.deepEqual(calls.at(-1).args[1].params, { offset: 20, limit: 10 })
    assert.equal('X-TeamHub-AI-Capability' in calls.at(-1).args[1].headers, false)
  } finally { delete globalThis.__index_api_fixture }
})

test('资料关键词搜索保留原参数并携带本机索引授权，不改变自动补全请求', async () => {
  const calls = []
  globalThis.__index_search_fixture = { post: (...args) => calls.push(args), get: (...args) => calls.push(args) }
  globalThis.__index_search_headers = () => ({ 'X-TeamHub-AI': '1', 'X-TeamHub-AI-Capability': 'search-capability' })
  try {
    const source = (await fs.readFile(new URL('../src/api/document.js', import.meta.url), 'utf8'))
      .replace("import request from './request'", 'const request = globalThis.__index_search_fixture')
      .replace("import { getIndexRequestHeaders } from './indexing'", 'const getIndexRequestHeaders = globalThis.__index_search_headers')
    const api = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`)
    const params = { status: 'pending', keyword: '夹具', tags: ['设备'], page: 2, page_size: 10 }
    api.searchDocuments(params)
    assert.deepEqual(calls[0], ['/api/documents/search', { ...params, include_content: true }, { headers: getIndexRequestHeadersFixture() }])
    assert.equal('include_content' in params, false)
    api.autocompleteDocuments('夹具')
    assert.deepEqual(calls[1], ['/api/documents/autocomplete', { params: { q: '夹具', limit: 10 } }])
  } finally { delete globalThis.__index_search_fixture; delete globalThis.__index_search_headers }
})

function getIndexRequestHeadersFixture() {
  return { 'X-TeamHub-AI': '1', 'X-TeamHub-AI-Capability': 'search-capability' }
}
