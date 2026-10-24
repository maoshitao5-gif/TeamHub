const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const launcher = fs.readFileSync(path.resolve(__dirname, '../mvp-dev.sh'), 'utf8');
const current = 'dist-electron/photo-render-fix-2026-10-22/mac-arm64/TeamHub.app';
const lastRelease = 'dist-electron/image-preorganize-fix-2026-10-21/mac-arm64/TeamHub.app';
const previous = 'dist-electron/mac-arm64/TeamHub.app';

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'teamhub-launcher-中文 空格-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.mkdirSync(path.join(root, 'scripts'));
  fs.writeFileSync(path.join(root, 'scripts/mvp-dev.sh'), launcher);
  const record = path.join(root, 'launched-app.txt');
  function app(relative, executable = true) {
    const binary = path.join(root, relative, 'Contents/MacOS/TeamHub');
    fs.mkdirSync(path.dirname(binary), { recursive: true });
    fs.writeFileSync(binary, '#!/bin/bash\nprintf \'%s\\n\' "$2" > "$TEAMHUB_LAUNCHER_TEST_RECORD"\n',
      { mode: executable ? 0o755 : 0o644 });
  }
  function run() {
    return spawnSync('/bin/bash', [path.join(root, 'scripts/mvp-dev.sh'), 'start-desktop'], {
      encoding: 'utf8', timeout: 5000,
      env: { ...process.env, TEAMHUB_LAUNCHER_TEST_RECORD: record },
    });
  }
  return { root, record, app, run };
}

test('desktop launcher chooses the adaptive photo rendering build when both builds exist', t => {
  const f = fixture(t);
  f.app(current); f.app(lastRelease); f.app(previous);
  const result = f.run();
  assert.equal(result.status, 0, result.stderr);
  assert.equal(fs.readFileSync(f.record, 'utf8').trim(), path.join(f.root, current));
});

test('desktop launcher keeps the previous build usable until Hermes is built', t => {
  const f = fixture(t);
  f.app(previous);
  const result = f.run();
  assert.equal(result.status, 0, result.stderr);
  assert.equal(fs.readFileSync(f.record, 'utf8').trim(), path.join(f.root, previous));
});

test('desktop launcher falls back to the last verified release before the legacy build', t => {
  const f = fixture(t);
  f.app(current, false); f.app(lastRelease); f.app(previous);
  const result = f.run();
  assert.equal(result.status, 0, result.stderr);
  assert.equal(fs.readFileSync(f.record, 'utf8').trim(), path.join(f.root, lastRelease));
});

test('an incomplete Hermes build cannot hide the working previous desktop', t => {
  const f = fixture(t);
  f.app(current, false); f.app(previous);
  const result = f.run();
  assert.equal(result.status, 0, result.stderr);
  assert.equal(fs.readFileSync(f.record, 'utf8').trim(), path.join(f.root, previous));
});

test('no desktop build produces an actionable error and starts no process', t => {
  const f = fixture(t);
  const result = f.run();
  assert.equal(result.status, 1);
  assert.match(result.stderr, /缺少桌面应用/);
  assert.equal(fs.existsSync(f.record), false);
});
