"""A delivery freezes one attachment and an explicit recipient set."""
from typing import Literal, Optional
from pydantic import BaseModel, Field, model_validator


class DeliveryCreate(BaseModel):
    request_id: str = Field(min_length=1, max_length=128)
    recipient_ids: list[str] = Field(min_length=1, max_length=100)
    note: str = Field(default='', max_length=4000)
    upload_id: Optional[str] = None
    source_document_id: Optional[str] = None
    source_version_id: Optional[str] = None
    doc_type: Literal['file', 'folder', 'archive'] = 'file'
    original_filename: Optional[str] = Field(None, min_length=1, max_length=255)
    content_hash: Optional[str] = Field(None, pattern=r'^[0-9a-f]{64}$')
    manifest_hash: Optional[str] = Field(None, pattern=r'^[0-9a-f]{64}$')

    @model_validator(mode='after')
    def attachment(self):
        if self.upload_id:
            if self.source_document_id or self.source_version_id or not self.content_hash or not self.original_filename:
                raise ValueError('上传附件必须指定摘要和文件名，不能同时指定源资料')
        elif not (self.source_document_id and self.source_version_id):
            raise ValueError('必须选择上传附件或确定的云端资料版本')
        self.recipient_ids = sorted(set(self.recipient_ids))
        return self
