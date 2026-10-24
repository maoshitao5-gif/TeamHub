const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const repo = path.resolve(__dirname, '../..');

function fixture(t, platform = 'darwin') {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'teamhub-hermes-build-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const python = path.join(root, '.mvp-sandbox/hermes-venv',
    platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  const source = path.join(root, '.mvp-sandbox/hermes-src');
  const worker = path.join(root, 'integrations/hermes/worker.py');
  for (const file of [python, path.join(source, 'run_agent.py'), path.join(source, 'LICENSE'),
    worker, path.join(root, 'integrations/hermes/upstream.json'), path.join(root, 'teamhub-hermes.spec'),
    path.join(root, 'scripts/prepare-hermes.py')]) {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, '# synthetic build input\n', { mode: 0o755 });
  }
  return { root, python, source, worker };
}

function build(options) {
  return require('../build-hermes.cjs').buildHermes({
    platform: 'darwin', logger: { error() {} }, ...options,
  });
}

test('desktop release includes the separate Hermes runtime and builds it before packaging', () => {
  const pkg = JSON.parse(fs.readFileSync(path.join(repo, 'package.json'), 'utf8'));
  assert.equal(pkg.scripts['hermes:build'], 'node scripts/build-hermes.cjs');
  assert(pkg.scripts['electron:build'].indexOf('npm run hermes:build') >= 0);
  assert(pkg.scripts['electron:build'].indexOf('npm run hermes:build') <
    pkg.scripts['electron:build'].indexOf('electron-builder'));
  assert(pkg.build.extraResources.some(resource => resource.from === 'dist/hermes' &&
    resource.to === 'hermes'));
});

test('build fails before launching any command when its private Python is missing', t => {
  const f = fixture(t);
  fs.unlinkSync(f.python);
  let calls = 0;
  const code = build({ root: f.root, execute() { calls++; } });
  assert.equal(code, 1);
  assert.equal(calls, 0);
});

test('build fails before launching any command when upstream or worker is missing', t => {
  for (const missing of ['.mvp-sandbox/hermes-src/run_agent.py',
    '.mvp-sandbox/hermes-src/LICENSE', 'integrations/hermes/worker.py',
    'integrations/hermes/upstream.json']) {
    const f = fixture(t);
    fs.unlinkSync(path.join(f.root, missing));
    let calls = 0;
    assert.equal(build({ root: f.root, execute() { calls++; return { status: 0 }; } }), 1);
    assert.equal(calls, 0);
  }
});

test('build invokes only the private interpreter without a shell or install commands', t => {
  const f = fixture(t);
  const calls = [];
  const code = build({ root: f.root, execute(...args) { calls.push(args); return { status: 0 }; } });
  assert.equal(code, 0);
  assert.equal(calls.length, 2);
  assert.equal(calls[0][0], f.python);
  assert.deepEqual(calls[0][1], [path.join(f.root, 'scripts/prepare-hermes.py'), '--source-only', '--offline']);
  const [command, args, options] = calls[1];
  assert.equal(command, f.python);
  assert.equal(args[0], '-m');
  assert.equal(args[1], 'PyInstaller');
  assert(args.includes(path.join(f.root, 'teamhub-hermes.spec')));
  assert.equal(options.cwd, f.root);
  assert.equal(options.shell, undefined);
  assert.equal(options.env.TEAMHUB_HERMES_SOURCE, f.source);
  assert.equal(options.env.TEAMHUB_HERMES_WORKER, f.worker);
  assert.equal(args[args.indexOf('--distpath') + 1], path.join(f.root, 'dist'));
});

test('Windows build selects its private Python executable', t => {
  const f = fixture(t, 'win32');
  let command;
  assert.equal(build({ root: f.root, platform: 'win32',
    execute(value) { command = value; return { status: 0 }; } }), 0);
  assert.equal(command, f.python);
});

test('build propagates PyInstaller failure and handles a failed spawn', t => {
  const f = fixture(t);
  let count = 0;
  assert.equal(build({ root: f.root, execute() { return { status: ++count === 1 ? 0 : 7 }; } }), 7);
  assert.equal(build({ root: f.root, execute() { return { status: null,
    error: new Error('synthetic spawn failure') }; } }), 1);
});

test('an integrity failure stops the build before PyInstaller starts', t => {
  const f = fixture(t), calls = [];
  assert.equal(build({ root: f.root, execute(command, args) {
    calls.push(args); return { status: 3 };
  } }), 3);
  assert.equal(calls.length, 1);
  assert(calls[0].includes('--source-only'));
  assert(!calls[0].includes('PyInstaller'));
});
