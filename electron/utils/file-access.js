const fs = require('node:fs/promises');
const path = require('node:path');

// 目录遍历通过异步文件系统完成，避免大目录阻塞 Electron 主线程。
async function isDirectory(filePath) {
  try {
    return (await fs.stat(filePath)).isDirectory();
  } catch (error) {
    if (error.code === 'ENOENT' || error.code === 'ENOTDIR') return false;
    throw error;
  }
}

async function listDirectoryContents(dirPath) {
  const entries = await fs.readdir(dirPath, { withFileTypes: true });
  const files = [];
  const folders = [];
  for (const entry of entries) {
    const item = { name: entry.name, path: path.join(dirPath, entry.name) };
    if (entry.isDirectory()) folders.push(item);
    else if (entry.isFile()) files.push(item);
  }
  // 读取失败会拒绝调用，由界面显示错误；不能冒充空目录而遗漏资料。
  return { files, folders };
}

module.exports = { isDirectory, listDirectoryContents };
