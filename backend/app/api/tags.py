"""
标签 API 路由
包含标签的 CRUD、合并等操作
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func

from backend.app.database import get_db
from backend.app.models import Tag, Document, document_tag
from backend.app.core.logger import get_logger
from backend.app.api.schemas import TagCreate, TagUpdate, TagMerge, TagResponse

logger = get_logger("api.tags")
router = APIRouter(prefix="/api/tags", tags=["tags"])


def _tag_with_count(tag: Tag, db: Session) -> TagResponse:
    """构建带文档数量的标签响应"""
    count = db.query(func.count(document_tag.c.document_id)).filter(
        document_tag.c.tag_id == tag.id
    ).scalar() or 0
    return TagResponse(id=tag.id, name=tag.name, color=tag.color, document_count=count)


@router.get("", response_model=list[TagResponse])
def list_tags(db: Session = Depends(get_db)):
    """获取所有标签（含关联文档数）"""
    tags = db.query(Tag).order_by(Tag.name).all()
    return [_tag_with_count(t, db) for t in tags]


@router.post("", response_model=TagResponse)
def create_tag(req: TagCreate, db: Session = Depends(get_db)):
    """创建标签"""
    existing = db.query(Tag).filter(Tag.name == req.name).first()
    if existing:
        raise HTTPException(400, f"标签 '{req.name}' 已存在")

    tag = Tag(name=req.name, color=req.color)
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return _tag_with_count(tag, db)


@router.get("/{tag_id}", response_model=TagResponse)
def get_tag(tag_id: str, db: Session = Depends(get_db)):
    """获取标签详情"""
    tag = db.query(Tag).filter(Tag.id == tag_id).first()
    if not tag:
        raise HTTPException(404, "标签不存在")
    return _tag_with_count(tag, db)


@router.put("/{tag_id}", response_model=TagResponse)
def update_tag(tag_id: str, req: TagUpdate, db: Session = Depends(get_db)):
    """更新标签"""
    tag = db.query(Tag).filter(Tag.id == tag_id).first()
    if not tag:
        raise HTTPException(404, "标签不存在")

    if req.name is not None:
        # 检查名称是否冲突
        conflict = db.query(Tag).filter(Tag.name == req.name, Tag.id != tag_id).first()
        if conflict:
            raise HTTPException(400, f"标签名 '{req.name}' 已存在")
        tag.name = req.name

    if req.color is not None:
        tag.color = req.color

    db.commit()
    db.refresh(tag)
    return _tag_with_count(tag, db)


@router.delete("/{tag_id}")
def delete_tag(tag_id: str, db: Session = Depends(get_db)):
    """删除标签"""
    tag = db.query(Tag).filter(Tag.id == tag_id).first()
    if not tag:
        raise HTTPException(404, "标签不存在")

    db.delete(tag)
    db.commit()
    return {"message": f"标签 '{tag.name}' 已删除"}


@router.post("/merge")
def merge_tags(req: TagMerge, db: Session = Depends(get_db)):
    """合并标签：将源标签的文档关联转移到目标标签，然后删除源标签"""
    target = db.query(Tag).filter(Tag.id == req.target_tag_id).first()
    if not target:
        raise HTTPException(404, "目标标签不存在")

    sources = db.query(Tag).filter(Tag.id.in_(req.source_tag_ids)).all()
    if not sources:
        raise HTTPException(404, "源标签不存在")

    merged_count = 0
    for source in sources:
        if source.id == target.id:
            continue
        # 将源标签关联的文档转移到目标标签
        for doc in source.documents:
            if target not in doc.tags:
                doc.tags.append(target)
        # 删除源标签
        db.delete(source)
        merged_count += 1

    db.commit()
    return {"message": f"已合并 {merged_count} 个标签到 '{target.name}'"}
