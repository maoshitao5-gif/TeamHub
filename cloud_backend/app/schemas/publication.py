"""发布 / 版本提交 DTO(M4,契约 §5.2)"""
from typing import List, Optional
from pydantic import BaseModel, Field
from typing import Literal


class AccessGrant(BaseModel):
    user_id: str
    role: Literal['reader', 'contributor', 'manager'] = 'reader'


class AccessUpdate(BaseModel):
    visibility: Literal['workspace', 'restricted']
    base_access_revision: int = Field(..., ge=1)
    access: List[AccessGrant] = Field(default_factory=list, max_length=1000)


class UploadResponse(BaseModel):
    upload_id: str
    sha256: str
    file_size: int


class PublicationCreate(BaseModel):
    visibility: Optional[Literal['workspace', 'restricted']] = None
    access: List[AccessGrant] = Field(default_factory=list, max_length=1000)
    request_id: str = Field(..., min_length=1, max_length=128)
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    force_new: bool = False
    doc_type: str = Field("file", pattern="^(file|folder|archive)$")
    tags: List[str] = Field(default_factory=list)
    upload_id: str
    content_hash: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    note: Optional[str] = None
    original_filename: Optional[str] = Field(None, min_length=1, max_length=255)
    manifest_hash: Optional[str] = Field(None, pattern=r"^[0-9a-f]{64}$")
    confirmed: bool = False


class VersionSubmit(BaseModel):
    request_id: str = Field(..., min_length=1, max_length=128)
    base_version_id: str  # 提交时声明的基准版本(冲突检测)
    upload_id: str
    content_hash: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    note: Optional[str] = None
    original_filename: Optional[str] = Field(None, min_length=1, max_length=255)
    manifest_hash: Optional[str] = Field(None, pattern=r"^[0-9a-f]{64}$")
    confirmed: bool = False


class DuplicateCheckRequest(BaseModel):
    doc_type: str = Field("file", pattern="^(file|folder|archive)$")
    content_hash: str = Field(..., pattern=r"^[0-9a-f]{64}$")
