const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const { createRequire } = require('node:module');

test('Electron builder downloader retains HTTP proxy support with the patched optional agent', async t => {
  const requests = [];
  const proxy = http.createServer((req, res) => {
    requests.push(req.url);
    res.end('synthetic electron artifact');
  });
  await new Promise(resolve => proxy.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => proxy.close(resolve)));
  process.env.GLOBAL_AGENT_HTTP_PROXY = `http://127.0.0.1:${proxy.address().port}`;
  process.env.GLOBAL_AGENT_NO_PROXY = '';
  const builderRequire = createRequire(require.resolve('app-builder-lib/package.json'));
  builderRequire('@electron/get').initializeProxy();
  const body = await new Promise((resolve, reject) => {
    const req = http.get('http://teamhub-test.invalid/artifact', res => {
      let value = '';
      res.on('data', chunk => { value += chunk; });
      res.on('end', () => resolve(value));
      res.on('error', reject);
    });
    req.on('error', reject);
    req.setTimeout(3000, () => req.destroy(new Error('proxy fixture timeout')));
  });
  assert.equal(body, 'synthetic electron artifact');
  assert.deepEqual(requests, ['http://teamhub-test.invalid/artifact']);
});
