/**
 * 云后端 — 共享收件箱 API 封装
 * 层：前端（调用云后端 :9000 的 /api/sharing 接口）
 * 使用 cloudRequest（自动附加 JWT Bearer Token + 401 静默续期）
 */
import cloudRequest from './cloud'

/** 列出所有设备的公开共享文档（读 manifest）*/
export const getCloudInbox = () => cloudRequest.get('/api/sharing/inbox', { _silent: true })

/** 查询云端存储状态（是否已配置 OSS）*/
export const getCloudStatus = () => cloudRequest.get('/api/sharing/status', { _silent: true })

/**
 * 获取共享文件的预签名下载 URL（有效期 1 小时）
 * 前端拿到 URL 后通过 Electron IPC download-url 将文件下载到本机，再调用本地后端入库。
 * @param {string} deviceId  - 来源设备 ID
 * @param {string} docId     - 文档 ID
 * @param {string} filename  - 文件名（含扩展名）
 * @returns {{ url: string, expires_in: number }}
 */
export const presignDownload = (deviceId, docId, filename) =>
  cloudRequest.get(`/api/sharing/presign-download/${deviceId}/${docId}/${encodeURIComponent(filename)}`)

/**
 * 撤回共享（删除 OSS 上的文件 + 更新 manifest）
 * @param {string} deviceId - 设备 ID
 * @param {string} docId    - 文档 ID
 */
export const withdrawShared = (deviceId, docId) =>
  cloudRequest.delete(`/api/sharing/${deviceId}/${docId}`)
