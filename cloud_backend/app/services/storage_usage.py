"""组织容量统一按保留的逻辑字节计算，不按物理对象去重。"""
from fastapi import HTTPException
from sqlalchemy import func

from app.models import CloudDocument, CloudVersion, Delivery, DeliveryItem, Team, Workspace


def team_storage_usage(db, team_ids):
    """覆盖全部空间、历史版本、回收站和已撤回投递；不读取或更新兼容缓存列。"""
    ids = list(dict.fromkeys(team_ids))
    usage = dict.fromkeys(ids, 0)
    for offset in range(0, len(ids), 500):
        batch = ids[offset:offset + 500]
        shared = db.query(Workspace.team_id, func.sum(CloudVersion.file_size)).select_from(
            CloudVersion).join(CloudDocument).join(Workspace).filter(
            Workspace.team_id.in_(batch)).group_by(Workspace.team_id).all()
        sent = db.query(Delivery.team_id, func.sum(DeliveryItem.file_size)).select_from(
            DeliveryItem).join(Delivery).filter(
            Delivery.team_id.in_(batch)).group_by(Delivery.team_id).all()
        for team_id, size in shared + sent:
            usage[team_id] += size or 0
    return usage


def check_quota(db, team_id, size):
    # 调用方仍在原有写事务内重算，不引入会过期的跨请求容量缓存。
    used = team_storage_usage(db, [team_id])[team_id]
    team = db.get(Team, team_id)
    if used + size > team.storage_quota:
        raise HTTPException(413, {'code': 'QUOTA_EXCEEDED', 'message': '组织容量不足，操作未提交'})
