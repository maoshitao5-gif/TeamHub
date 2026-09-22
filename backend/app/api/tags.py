"""
标签 API 路由
包含标签的 CRUD、合并等操作
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from backend.app.database import get_db
from backend.app.models import Tag, Document, document_tag
from backend.app.core.logger import get_logger
from backend.app.core.changelog import record_change
from backend.app.api.schemas import TagCreate, TagUpdate, TagMerge, TagResponse

logger = get_logger("api.tags")
router = APIRouter(prefix="/api/tags", tags=["tags"])


def _tag_with_count(tag: Tag, db: Session) -> TagResponse:
    """构建带文档数量的标签响应"""
    # 只统计状态为 organized 的文档，与文档库页面保持一致（文档库页面只显示 organized 状态的文档）
    count = db.query(func.count(Document.id)).select_from(document_tag).join(
        Document, Document.id == document_tag.c.document_id
    ).filter(
        document_tag.c.tag_id == tag.id,
        Document.status == "organized"
    ).scalar() or 0
    
    return TagResponse(id=tag.id, name=tag.name, color=tag.color, document_count=count)


@router.get("")
def list_tags(db: Session = Depends(get_db), include_stats: bool = False):
    """获取所有标签（含关联文档数）
    
    Args:
        include_stats: 如果为 True，返回包含总唯一文档数的对象格式；否则返回标签数组（默认）
    """
    tags = db.query(Tag).order_by(Tag.name).all()
    tag_responses = [_tag_with_count(t, db) for t in tags]
    
    # 如果请求包含统计信息，计算总唯一文档数
    if include_stats:
        # 计算总唯一文档数（所有标签关联的唯一文档数，不重复计算）
        # 只统计状态为 organized 的文档，与文档库页面保持一致（文档库页面只显示 organized 状态的文档）
        unique_doc_count = db.query(func.count(func.distinct(Document.id))).select_from(document_tag).join(
            Document, Document.id == document_tag.c.document_id
        ).filter(
            Document.status == "organized"
        ).scalar() or 0
        
        # 返回包含标签列表和总唯一文档数的响应
        return {
            "tags": tag_responses,
            "total_unique_documents": unique_doc_count
        }
    
    # 默认返回标签数组（向后兼容）
    return tag_responses


@router.post("", response_model=TagResponse)
def create_tag(req: TagCreate, db: Session = Depends(get_db)):
    """创建标签"""
    existing = db.query(Tag).filter(Tag.name == req.name).first()
    if existing:
        raise HTTPException(400, f"标签 '{req.name}' 已存在")

    tag = Tag(name=req.name, color=req.color)
    db.add(tag)
    record_change(db, "tag", tag.id, "create", {"name": req.name})
    db.commit()
    db.refresh(tag)
    return _tag_with_count(tag, db)


@router.get("/suggest")
def suggest_tags(
    q: str = Query(""),
    limit: int = Query(10, ge=1, le=20),
    db: Session = Depends(get_db)
):
    """标签名联想搜索，用于标签模式搜索框输入联想"""
    if q.strip():
        tags = db.query(Tag).filter(Tag.name.ilike(f"%{q}%")).order_by(Tag.name).limit(limit).all()
    else:
        # 无关键词时返回使用最多的标签
        tags = (
            db.query(Tag)
            .join(document_tag, Tag.id == document_tag.c.tag_id)
            .join(Document, Document.id == document_tag.c.document_id)
            .filter(Document.status == "organized")
            .group_by(Tag.id)
            .order_by(func.count(document_tag.c.document_id).desc())
            .limit(limit)
            .all()
        )
    return [{"id": t.id, "name": t.name, "color": t.color} for t in tags]


@router.get("/related")
def related_tags(
    selected: str = Query(""),
    limit: int = Query(8, ge=1, le=20),
    db: Session = Depends(get_db)
):
    """查询与已选标签共同出现的其他标签，用于渐进式标签筛选"""
    if not selected.strip():
        return []
    id_list = [i.strip() for i in selected.split(",") if i.strip()]
    if not id_list:
        return []
    count = len(id_list)

    # 找到同时含有所有已选标签的文档（交集）
    doc_ids_subq = (
        db.query(document_tag.c.document_id)
        .join(Document, Document.id == document_tag.c.document_id)
        .filter(
            document_tag.c.tag_id.in_(id_list),
            Document.status == "organized"
        )
        .group_by(document_tag.c.document_id)
        .having(func.count(func.distinct(document_tag.c.tag_id)) == count)
        .subquery()
    )

    # 统计这些文档中其他标签的共现频率
    results = (
        db.query(Tag, func.count(document_tag.c.document_id).label("cnt"))
        .join(document_tag, Tag.id == document_tag.c.tag_id)
        .filter(
            document_tag.c.document_id.in_(doc_ids_subq),
            Tag.id.notin_(id_list)
        )
        .group_by(Tag.id)
        .order_by(func.count(document_tag.c.document_id).desc())
        .limit(limit)
        .all()
    )
    return [{"id": t.id, "name": t.name, "color": t.color, "count": cnt} for t, cnt in results]


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

    record_change(db, "tag", tag.id, "update", {"name": tag.name, "color": tag.color})
    db.commit()
    db.refresh(tag)
    return _tag_with_count(tag, db)


@router.delete("/{tag_id}")
def delete_tag(tag_id: str, db: Session = Depends(get_db)):
    """删除标签"""
    tag = db.query(Tag).filter(Tag.id == tag_id).first()
    if not tag:
        raise HTTPException(404, "标签不存在")

    record_change(db, "tag", tag.id, "delete", {"name": tag.name})
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
        record_change(db, "tag", source.id, "delete", {"name": source.name, "merged_into": target.id})
        db.delete(source)
        merged_count += 1

    db.commit()
    return {"message": f"已合并 {merged_count} 个标签到 '{target.name}'"}
