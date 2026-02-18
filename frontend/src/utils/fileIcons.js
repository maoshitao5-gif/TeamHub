/**
 * 文件类型图标映射工具
 * 根据文件名后缀返回对应的图标和颜色
 */

const extensionMap = {
  // PDF
  pdf: { icon: 'Document', color: '#e74c3c' },

  // Word
  doc: { icon: 'Document', color: '#2b579a' },
  docx: { icon: 'Document', color: '#2b579a' },

  // Excel
  xls: { icon: 'Grid', color: '#217346' },
  xlsx: { icon: 'Grid', color: '#217346' },
  csv: { icon: 'Grid', color: '#217346' },

  // PPT
  ppt: { icon: 'DataBoard', color: '#d24726' },
  pptx: { icon: 'DataBoard', color: '#d24726' },

  // 图片
  jpg: { icon: 'Picture', color: '#9b59b6' },
  jpeg: { icon: 'Picture', color: '#9b59b6' },
  png: { icon: 'Picture', color: '#9b59b6' },
  gif: { icon: 'Picture', color: '#9b59b6' },
  bmp: { icon: 'Picture', color: '#9b59b6' },
  svg: { icon: 'Picture', color: '#9b59b6' },
  webp: { icon: 'Picture', color: '#9b59b6' },
  ico: { icon: 'Picture', color: '#9b59b6' },

  // 视频
  mp4: { icon: 'VideoCamera', color: '#e67e22' },
  avi: { icon: 'VideoCamera', color: '#e67e22' },
  mkv: { icon: 'VideoCamera', color: '#e67e22' },
  mov: { icon: 'VideoCamera', color: '#e67e22' },
  wmv: { icon: 'VideoCamera', color: '#e67e22' },
  flv: { icon: 'VideoCamera', color: '#e67e22' },

  // 音频
  mp3: { icon: 'Headset', color: '#1abc9c' },
  wav: { icon: 'Headset', color: '#1abc9c' },
  flac: { icon: 'Headset', color: '#1abc9c' },
  aac: { icon: 'Headset', color: '#1abc9c' },
  ogg: { icon: 'Headset', color: '#1abc9c' },

  // 压缩包
  zip: { icon: 'Box', color: '#f39c12' },
  rar: { icon: 'Box', color: '#f39c12' },
  '7z': { icon: 'Box', color: '#f39c12' },
  tar: { icon: 'Box', color: '#f39c12' },
  gz: { icon: 'Box', color: '#f39c12' },

  // 代码
  js: { icon: 'Document', color: '#f7df1e' },
  ts: { icon: 'Document', color: '#3178c6' },
  py: { icon: 'Document', color: '#3776ab' },
  java: { icon: 'Document', color: '#b07219' },
  html: { icon: 'Document', color: '#e34c26' },
  css: { icon: 'Document', color: '#563d7c' },
  json: { icon: 'Document', color: '#95a5a6' },

  // 文本
  txt: { icon: 'Notebook', color: '#95a5a6' },
  md: { icon: 'Notebook', color: '#95a5a6' },
  log: { icon: 'Notebook', color: '#95a5a6' },
}

// 默认：未知文件类型
const defaultFileType = { icon: 'Document', color: '#3498db' }

/**
 * 获取文件扩展名（小写）
 */
function getExtension(filename) {
  if (!filename) return ''
  const dot = filename.lastIndexOf('.')
  if (dot < 0) return ''
  return filename.substring(dot + 1).toLowerCase()
}

/**
 * 获取文件类型图标名称（对应 Element Plus 图标组件名）
 * @param {string} filename - 文件名
 * @returns {string} Element Plus 图标名
 */
export function getFileTypeIcon(filename) {
  const ext = getExtension(filename)
  return (extensionMap[ext] || defaultFileType).icon
}

/**
 * 获取文件类型对应的颜色
 * @param {string} filename - 文件名
 * @returns {string} 颜色值
 */
export function getFileTypeColor(filename) {
  const ext = getExtension(filename)
  return (extensionMap[ext] || defaultFileType).color
}
