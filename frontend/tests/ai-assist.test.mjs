import test from 'node:test'
import assert from 'node:assert/strict'

// 本期新增能力；缺少实现本身就是 RED，不依赖用户服务、模型或密钥。
let ai = {}
try { ai = await import('../src/utils/aiAssist.js') } catch (error) {
  if (error.code !== 'ERR_MODULE_NOT_FOUND') throw error
}

test('AI 候选标签去空白去重，保留用户可核对的顺序', () => {
  assert.deepEqual(ai.normalizeSuggestedTags(['  变压器 ', '', '变压器', '试验', null]), ['变压器', '试验'])
})
test('采用只追加已勾选的候选，保留已有标签且不接受任意外来值', () => {
  assert.deepEqual(ai.appendSelectedTags(['原标签', '试验'], ['设备', '试验', '项目'], ['设备', '项目', '非候选']), ['原标签', '试验', '设备', '项目'])
})
test('不勾选候选时不替换已有标签，也不修改输入数组', () => {
  const original = ['原标签']
  assert.deepEqual(ai.appendSelectedTags(original, ['设备'], []), original)
  assert.deepEqual(original, ['原标签'])
})
test('更换 AI 服务地址前必须重新输入密钥，避免向新服务发送旧密钥', () => {
  assert.throws(() => ai.buildAISettingsUpdate({ base_url: 'https://other.example/v1', model: 'flash', api_key: '' }, { base_url: 'https://api.deepseek.com', has_api_key: true }), /重新输入/)
})
test('同服务留空密钥表示保留，提交后数据中不夹带 has_api_key', () => {
  assert.deepEqual(ai.buildAISettingsUpdate({ base_url: 'https://api.deepseek.com', model: 'deepseek-flash', api_key: '' }, { base_url: 'https://api.deepseek.com', has_api_key: true }), { base_url: 'https://api.deepseek.com', model: 'deepseek-flash' })
})
test('密钥更换与清除必须明确，清除不发送输入框里残留的密钥', () => {
  assert.deepEqual(ai.buildAISettingsUpdate({ base_url: 'https://api.deepseek.com', model: 'deepseek-flash', api_key: 'new-secret' }, { base_url: 'https://api.deepseek.com' }).api_key, 'new-secret')
  assert.deepEqual(ai.buildAISettingsUpdate({ base_url: 'https://api.deepseek.com', model: 'deepseek-flash', api_key: 'residue' }, { base_url: 'https://api.deepseek.com' }, true), { base_url: 'https://api.deepseek.com', model: 'deepseek-flash', clear_api_key: true })
})
test('关闭、切换资料或个人库后，迟到的准备和分析结果不再生效', () => {
  const scope = ai.createAIRequestScope()
  const a = scope.begin('doc-a', '/library-a')
  assert.equal(scope.isCurrent(a, 'doc-a', '/library-a'), true)
  assert.equal(scope.isCurrent(a, 'doc-b', '/library-a'), false)
  assert.equal(scope.isCurrent(a, 'doc-a', '/library-b'), false)
  scope.invalidate()
  assert.equal(scope.isCurrent(a, 'doc-a', '/library-a'), false)
  const b = scope.begin('doc-a', '/library-a')
  assert.equal(scope.isCurrent(b, 'doc-a', '/library-a'), true)
  assert.equal(scope.isCurrent(a, 'doc-a', '/library-a'), false)
})
