"""共享标签关联 / 回收站 DTO(M5,契约 §3.4/§5.2)"""
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator


class TagLinkUpdate(BaseModel):
    """按关联增删共享标签;add=标签名,remove=关联的 tag_id"""
    add: List[str] = Field(default_factory=list, max_length=100)
    remove: List[str] = Field(default_factory=list, max_length=100)


    @field_validator('add')
    @classmethod
    def normalize_names(cls, names):
        normalized = [name.strip() for name in names]
        if any(not name or len(name) > 100 for name in normalized):
            raise ValueError('标签名称应为 1—100 个字符')
        return list(dict.fromkeys(normalized))


class TagLinkItem(BaseModel):
    tag_id: str
    name: str
    added_by: Optional[str] = None       # 贡献者(按关联行判断"本人贡献",契约 §3.3)
    created_at: Optional[datetime] = None
    contributor_name: Optional[str] = None
    can_remove: bool = False

