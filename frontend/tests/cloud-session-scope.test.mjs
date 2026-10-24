import test from 'node:test'
import assert from 'node:assert/strict'
import { advanceCloudSessionScope, getCloudSessionScope, isCurrentCloudSessionScope, notifyCloudSessionExpiry } from '../src/utils/cloudSession.js'

const expired = { status: 401, data: { detail: { error: { code: 'CLOUD_SESSION_EXPIRED', message: '云服务登录已失效，请重新登录' } } } }
const target = () => { const events = []; return { events, dispatchEvent: event => events.push(event) } }
globalThis.CustomEvent ??= class extends Event { constructor(type, options) { super(type); this.detail = options?.detail } }

test('explicit terminal code dispatches current scope without exposing credentials', () => {
  const scope = advanceCloudSessionScope(), sink = target()
  assert.equal(isCurrentCloudSessionScope(scope), true)
  assert.equal(notifyCloudSessionExpiry(expired, scope, sink), true)
  assert.equal(sink.events.length, 1)
  assert.equal(sink.events[0].type, 'cloud:session-expired')
  assert.deepEqual(sink.events[0].detail, { scope })
})
test('top-level terminal error envelope is supported', () => {
  const sink = target()
  assert.equal(notifyCloudSessionExpiry({ status: 401, data: { error: { code: 'CLOUD_SESSION_EXPIRED' } } }, getCloudSessionScope(), sink), true)
})
test('late expiry from an older login/server/personal library scope cannot invalidate the current session', () => {
  const oldScope = advanceCloudSessionScope(), sink = target()
  advanceCloudSessionScope()
  assert.equal(notifyCloudSessionExpiry(expired, oldScope, sink), false)
  assert.equal(notifyCloudSessionExpiry(expired, undefined, sink), false)
  assert.equal(sink.events.length, 0)
})
test('business 401, login rejection, forbidden, network and unavailable service do not expire session', () => {
  const sink = target(), scope = getCloudSessionScope()
  for (const response of [
    { status: 401, data: { detail: '邮箱或密码错误' } },
    { status: 401, data: { error: { code: 'UNAUTHORIZED' } } },
    { status: 403, data: expired.data }, { status: 503, data: expired.data },
    { status: 401, data: new Blob(['无效的认证令牌']) }, undefined,
  ]) assert.equal(notifyCloudSessionExpiry(response, scope, sink), false)
  assert.equal(sink.events.length, 0)
})
