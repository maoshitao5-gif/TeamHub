import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'

test('预整理API绑定本机能力、当前库授权、请求取消，读请求不启动模型', async () => {
  const calls = []
  globalThis.window = { electron: { aiCapability: 'cap-a' } }
  globalThis.__preorganize_api = Object.fromEntries(['get', 'post', 'put'].map(method => [method, (...args) => { calls.push({ method, args }); return Promise.resolve({}) }]))
  try {
    const source = (await fs.readFile(new URL('../src/api/preorganize.js', import.meta.url), 'utf8'))
      .replace("import request from './request'", 'const request = globalThis.__preorganize_api')
    const api = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`)
    const signal = new AbortController().signal
    await api.getPreorganizeConfig(signal)
    await api.listPreorganizeDocuments({ search: '图纸', state: 'ready' }, signal)
    await api.getPreorganizeDocument('doc /a', signal)
    const batch = { library_token: 'lib-a', service_revision: 'rev-a', consent: true, request_id: 'req-a', document_ids: ['d1'] }
    await api.startPreorganize(batch, signal)
    await api.setPreorganizePolicy({ ...batch, enabled: true }, signal)
    await api.cancelPreorganize({ library_token: 'lib-a', document_ids: ['d1'] }, signal)
    await api.applyPreorganize({ library_token: 'lib-a', items: [] }, signal)
    await api.listPreorganizeOperations(signal)
    window.electron.aiCapability = 'cap-b'
    await api.undoPreorganize('op /a', { library_token: 'lib-a' }, signal)
    assert.deepEqual(calls.map(call => [call.method, call.args[0]]), [
      ['get', '/api/ai/preorganize/config'], ['get', '/api/ai/preorganize/documents'],
      ['get', '/api/ai/preorganize/documents/doc%20%2Fa'], ['post', '/api/ai/preorganize/start'],
      ['put', '/api/ai/preorganize/policy'], ['post', '/api/ai/preorganize/cancel'],
      ['post', '/api/ai/preorganize/apply'], ['get', '/api/ai/preorganize/operations'],
      ['post', '/api/ai/preorganize/operations/op%20%2Fa/undo'],
    ])
    assert.deepEqual(calls[3].args[1], batch)
    for (const [i, call] of calls.entries()) {
      const config = call.args.at(-1)
      assert.equal(config.signal, signal)
      assert.equal(config._silent, true)
      assert.equal(config.headers['X-TeamHub-AI'], '1')
      assert.equal(config.headers['X-TeamHub-AI-Capability'], i === 8 ? 'cap-b' : 'cap-a')
    }
  } finally { delete globalThis.__preorganize_api }
})
