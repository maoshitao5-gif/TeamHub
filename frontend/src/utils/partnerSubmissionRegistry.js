// 仅存于当前 renderer 内存：离开再回到伙伴页面仍能核对未知提交。
// 不写 localStorage/磁盘，不向其他资料库或会话复用请求正文。
export const pendingPartnerSubmissions = new Map()
