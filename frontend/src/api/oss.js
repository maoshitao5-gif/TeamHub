/**
 * 本地后端 — OSS 共享 API 封装（精简版）
 * 层：前端（调用本地后端 :8001 的 /api/share 接口）
 *
 * OSS AK/SK 已迁移至云后端管理，本地后端不再暴露 OSS 配置端点。
 * 云仓库（SharedInboxPage）改由 cloudOss.js 访问云后端。
 */
import request from './request'

/** 开启/关闭文档的云同步标记 */
export const toggleDocSync = (docId, enabled) =>
  request.put(`/api/share/documents/${docId}/sync-enabled`, null, { params: { enabled } })

/** 一键共享：开启云同步 + 设置可见性 + 通过云后端上传到 OSS */
export const quickShare = (docId, data) =>
  request.post(`/api/share/documents/${docId}/quick-share`, data)
