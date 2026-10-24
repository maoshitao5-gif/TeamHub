const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const http = require('node:http');
const { readState, processMatches, stateMatches, healthy } = require('./launch-desktop.cjs');

test('missing or partial startup state cannot count as ready', async t => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'teamhub-launcher-state-'));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const file = path.join(root, 'state.json');
  assert.equal(readState(file), null);
  await fs.writeFile(file, '{"status":');
  assert.equal(readState(file), null);
  await fs.writeFile(file, JSON.stringify({ status: 'ready', pid: 1 }));
  assert.equal(stateMatches(readState(file), '/other/TeamHub.app', root), false);
});

test('process identity must match the binary and parent, not just a live PID', () => {
  assert.equal(processMatches(process.pid, '/wrong/TeamHub'), false);
  assert.equal(processMatches(1, process.execPath), false);
  assert.equal(processMatches(process.pid, process.execPath, 999999), false);
  assert.equal(processMatches(process.pid, process.execPath), true);
});

test('health requires the exact instance and rejects malformed or unrelated services', async t => {
  let response = '{"instance_id":"current"}';
  const server = http.createServer((_req, res) => { res.end(response); });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  const state = { port: server.address().port, instanceId: 'current' };
  assert.equal(await healthy(state), true);
  response = '{"instance_id":"someone-else"}';
  assert.equal(await healthy(state), false);
  response = 'not json';
  assert.equal(await healthy(state), false);
  response = 'x'.repeat(65537);
  assert.equal(await healthy(state), false);
});

test('a streaming health response cannot hold the launcher indefinitely', async t => {
  const server = http.createServer((_req, res) => {
    res.writeHead(200);
    const timer = setInterval(() => res.write(' '), 50);
    res.on('close', () => clearInterval(timer));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => { server.closeAllConnections(); return new Promise(resolve => server.close(resolve)); });
  let guard;
  try {
    const result = await Promise.race([
      healthy({ port: server.address().port, instanceId: 'current' }),
      new Promise((_resolve, reject) => { guard = setTimeout(() => reject(new Error('health probe never finished')), 2500); }),
    ]);
    assert.equal(result, false);
  } finally { clearTimeout(guard); }
});
