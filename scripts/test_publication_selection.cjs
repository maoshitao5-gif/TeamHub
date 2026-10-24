const test = require('node:test');
const assert = require('node:assert/strict');
const helper = import('../frontend/src/utils/publicationSelection.js');
const libraries = [{ id: 'a' }, { id: 'b' }];

test('publishing chooses the valid preference or sole library; first multi-library requires choice', async () => {
  const { choosePublicationLibrary } = await helper;
  assert.equal(choosePublicationLibrary(libraries, 'b'), 'b');
  assert.equal(choosePublicationLibrary(libraries, 'gone'), '');
  assert.equal(choosePublicationLibrary([{ id: 'a' }], ''), 'a');
  assert.equal(choosePublicationLibrary([], ''), '');
});

test('pending retry preserves target and recipients independently of browsing preference', async () => {
  const { restorePublicationIntent } = await helper;
  const pending = { id: 'retry', action: 'new', workspaceId: 'a', forceNew: true,
    visibility: 'restricted', recipients: ['reader'] };
  const result = restorePublicationIntent(JSON.stringify(pending), libraries, null);
  assert.deepEqual(result, pending);
  pending.recipients.push('mutated');
  assert.deepEqual(result.recipients, ['reader']);
});

test('update retry must match original target; unreadable/missing target cannot be restored as a new publication', async () => {
  const { restorePublicationIntent } = await helper;
  const pending = { id: 'retry', action: 'update', workspaceId: 'a', visibility: 'workspace', recipients: [] };
  assert.equal(restorePublicationIntent(JSON.stringify(pending), libraries, null), null);
  assert.equal(restorePublicationIntent(JSON.stringify(pending), libraries, { workspace_id: 'b' }), null);
  assert.equal(restorePublicationIntent(JSON.stringify(pending), libraries, { workspace_id: 'a', can_contribute: true }).workspaceId, 'a');
  assert.equal(restorePublicationIntent(JSON.stringify({ ...pending, action: 'new', workspaceId: 'gone' }), libraries, null), null);
});

test('malformed saved state is ignored', async () => {
  const { restorePublicationIntent } = await helper;
  for (const value of ['bad-json', 'null', '{}', '{"id":"r","action":"erase"}']) {
    assert.equal(restorePublicationIntent(value, libraries, null), null);
  }
});
