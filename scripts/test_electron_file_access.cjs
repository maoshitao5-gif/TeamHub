const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { isDirectory, listDirectoryContents } = require('../electron/utils/file-access');

async function temporary(t) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'teamhub-file-access-'));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  return root;
}

test('directory listing preserves hidden files, Unicode names and empty folders', async t => {
  const root = await temporary(t);
  await fs.writeFile(path.join(root, '图纸 A.txt'), 'test');
  await fs.writeFile(path.join(root, '.note'), 'hidden');
  await fs.mkdir(path.join(root, '空目录'));
  const result = await listDirectoryContents(root);
  assert.deepEqual(result.files.map(item => item.name).sort(), ['.note', '图纸 A.txt']);
  assert.deepEqual(result.folders, [{ name: '空目录', path: path.join(root, '空目录') }]);
  assert.equal(result.files.every(item => path.dirname(item.path) === root), true);
  assert.deepEqual(await listDirectoryContents(path.join(root, '空目录')), { files: [], folders: [] });
});

test('file, directory and missing paths are distinguished; listing failures propagate', async t => {
  const root = await temporary(t);
  const file = path.join(root, '资料');
  await fs.writeFile(file, 'content');
  assert.equal(await isDirectory(root), true);
  assert.equal(await isDirectory(file), false);
  assert.equal(await isDirectory(path.join(root, '不存在')), false);
  await assert.rejects(listDirectoryContents(path.join(root, '不存在')), { code: 'ENOENT' });
  await assert.rejects(listDirectoryContents(file), { code: 'ENOTDIR' });
});

test('listing does not follow symlinks into unrelated directories', async t => {
  const root = await temporary(t);
  const outside = await temporary(t);
  await fs.writeFile(path.join(outside, 'other.txt'), 'external');
  await fs.symlink(outside, path.join(root, 'external'));
  assert.deepEqual(await listDirectoryContents(root), { files: [], folders: [] });
  assert.equal(await fs.readFile(path.join(outside, 'other.txt'), 'utf8'), 'external');
});
