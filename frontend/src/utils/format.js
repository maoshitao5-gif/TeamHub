/**
 * 格式化工具函数
 */

/**
 * 格式化文件大小
 * @param {number} bytes - 字节数
 * @returns {string} 格式化后的文件大小
 */
export function formatFileSize(bytes) {
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i]
}

/**
 * 格式化日期时间
 * @param {string} dateString - ISO 日期字符串
 * @returns {string} 格式化后的日期时间
 */
export function formatDateTime(dateString) {
  const date = new Date(dateString)
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  const hours = String(date.getHours()).padStart(2, '0')
  const minutes = String(date.getMinutes()).padStart(2, '0')
  return `${year}-${month}-${day} ${hours}:${minutes}`
}

// 标签颜色映射（根据标签名称生成固定颜色）
const tagColorMap = new Map()

/**
 * 根据标签名称生成颜色（相同标签总是相同颜色）
 * @param {string} tagName - 标签名称
 * @returns {string} 颜色值
 */
export function getTagColor(tagName) {
  if (!tagName) return '#909399'
  
  if (tagColorMap.has(tagName)) {
    return tagColorMap.get(tagName)
  }
  
  const colors = [
    '#409EFF', '#67C23A', '#E6A23C', '#F56C6C', '#909399',
    '#9C27B0', '#FF9800', '#00BCD4', '#8BC34A', '#FF5722'
  ]
  
  // 根据标签名称的哈希值选择颜色
  let hash = 0
  for (let i = 0; i < tagName.length; i++) {
    hash = tagName.charCodeAt(i) + ((hash << 5) - hash)
  }
  const color = colors[Math.abs(hash) % colors.length]
  
  tagColorMap.set(tagName, color)
  return color
}
