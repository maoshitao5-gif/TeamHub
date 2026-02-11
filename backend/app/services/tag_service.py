"""
标签服务模块
处理标签相关的业务逻辑
"""
from sqlalchemy.orm import Session

from backend.app.models import Tag


def get_or_create_tag(db: Session, tag_name: str) -> Tag:
    """
    获取或创建标签
    如果标签已存在则返回，不存在则创建
    
    Args:
        db: 数据库会话
        tag_name: 标签名称
        
    Returns:
        Tag 对象
    """
    # 规范化标签名称：去除首尾空格，转为小写
    tag_name = tag_name.strip().lower()
    
    # 查找是否已存在该标签
    tag = db.query(Tag).filter(Tag.name == tag_name).first()
    
    if not tag:
        # 如果不存在则创建新标签
        tag = Tag(name=tag_name)
        db.add(tag)
        db.commit()
        db.refresh(tag)
    
    return tag
