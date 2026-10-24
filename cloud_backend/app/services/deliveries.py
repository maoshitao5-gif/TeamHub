"""Delivery authorization is independent of document access and organizational roles."""
from datetime import datetime, timezone
from fastapi import HTTPException
from sqlalchemy import func
from app.models import Delivery, DeliveryRecipient, User, TeamMember
from app.core.deps import check_workspace_access


def valid_actor(db, ws_id, actor_id, role='viewer'):
    actor = db.query(User).filter_by(id=actor_id, is_active=True).first()
    if not actor:
        raise HTTPException(401, '账号已失效')
    return check_workspace_access(ws_id, actor_id, role, db)


def validate_recipients(db, team_id, recipients):
    found = {row[0] for row in db.query(User.id).join(TeamMember, TeamMember.user_id == User.id)
             .filter(TeamMember.team_id == team_id, User.is_active.is_(True), User.id.in_(recipients)).all()}
    if found != set(recipients):
        raise HTTPException(422, {'code': 'INVALID_RECIPIENTS', 'message': '接收者已失效，请重新选择'})


def authorized_delivery(db, ws_id, delivery_id, actor_id):
    valid_actor(db, ws_id, actor_id)
    delivery = db.query(Delivery).filter_by(id=delivery_id, workspace_id=ws_id).first()
    recipient = db.query(DeliveryRecipient).filter_by(delivery_id=delivery_id, recipient_id=actor_id).first()
    if not delivery or (delivery.sender_id != actor_id and not recipient):
        raise HTTPException(404, '资源不存在')
    return delivery, recipient


def serialize(db, delivery, actor_id):
    sender = db.get(User, delivery.sender_id)
    recipient = db.query(DeliveryRecipient).filter_by(delivery_id=delivery.id, recipient_id=actor_id).first()
    count = len(delivery.recipients) if delivery.sender_id == actor_id else None
    return _serialize(delivery, actor_id, sender.display_name if sender else '已停用成员', recipient, count)


def serialize_many(db, deliveries, actor_id):
    """仅批量读取当前页、当前收件人的记录，不扩大投递 DTO 的可见范围。"""
    if not deliveries:
        return []
    ids = [delivery.id for delivery in deliveries]
    senders = dict(db.query(User.id, User.display_name).filter(
        User.id.in_({delivery.sender_id for delivery in deliveries})).all())
    receipts = {receipt.delivery_id: receipt for receipt in db.query(DeliveryRecipient).filter(
        DeliveryRecipient.delivery_id.in_(ids), DeliveryRecipient.recipient_id == actor_id).all()}
    own_ids = [delivery.id for delivery in deliveries if delivery.sender_id == actor_id]
    counts = dict(db.query(DeliveryRecipient.delivery_id, func.count(DeliveryRecipient.recipient_id)).filter(
        DeliveryRecipient.delivery_id.in_(own_ids)).group_by(DeliveryRecipient.delivery_id).all()) if own_ids else {}
    return [_serialize(delivery, actor_id, senders.get(delivery.sender_id, '已停用成员'),
                       receipts.get(delivery.id), counts.get(delivery.id, 0))
            for delivery in deliveries]


def _serialize(delivery, actor_id, sender_name, recipient, recipient_count):
    result = dict(id=delivery.id, request_id=delivery.request_id, workspace_id=delivery.workspace_id,
                  sender_id=delivery.sender_id, sender_name=sender_name,
                  note=delivery.note, status=delivery.status, sent_at=delivery.sent_at,
                  recalled_at=delivery.recalled_at, read_at=recipient.read_at if recipient else None,
                  can_recall=delivery.sender_id == actor_id and delivery.status == 'sent',
                  can_download=bool(recipient) and delivery.status == 'sent', items=[])
    # DTO intentionally omits other recipients and source document/history identifiers.
    for item in delivery.items:
        result['items'].append(dict(id=item.id, filename=item.filename, doc_type=item.doc_type,
                                    content_hash=item.content_hash, manifest_hash=item.manifest_hash,
                                    file_size=item.file_size, file_count=item.file_count, total_size=item.total_size))
    if delivery.sender_id == actor_id:
        result['recipient_count'] = recipient_count
    return result
