"""共享个人资料响应构建逻辑；列表只读取当前版本，不加载完整历史。"""
from sqlalchemy.orm import Session, object_session

from backend.app.api.schemas import TagResponse, VersionResponse
from backend.app.core.storage_paths import document_path
from backend.app.models import Document, Version


_NOT_LOADED = object()


def build_document_responses(documents: list[Document], db: Session) -> list[dict]:
    """批量读取当前版本；ID 与所属资料均匹配才可出现在响应中。"""
    version_ids = {doc.current_version_id for doc in documents if doc.current_version_id}
    versions = (db.query(Version).filter(Version.id.in_(version_ids)).all()
                if version_ids else [])
    by_id = {version.id: version for version in versions}
    return [build_document_response(doc, by_id.get(doc.current_version_id)) for doc in documents]


def build_document_response(doc: Document, current_version=_NOT_LOADED) -> dict:
    """单条响应按当前指针读取，写入后的字段与列表响应保持一致。"""
    if current_version is _NOT_LOADED:
        db = object_session(doc)
        if db is not None:
            current_version = (db.query(Version).filter(
                Version.id == doc.current_version_id, Version.document_id == doc.id,
            ).first() if doc.current_version_id else None)
        else:
            current_version = next((version for version in doc.versions
                                    if version.id == doc.current_version_id), None)
    if current_version is not None and (current_version.document_id != doc.id
                                       or current_version.id != doc.current_version_id):
        current_version = None

    response = {
        "id": doc.id,
        "name": doc.name,
        "description": doc.description,
        "is_folder": doc.is_folder,
        "storage_path": doc.storage_path,
        "storage_area": doc.storage_area,
        "working_path": str(document_path(doc)) if doc.storage_path or doc.original_path else None,
        "storage_mode": doc.storage_mode,
        "original_path": doc.original_path,
        "status": doc.status,
        "file_count": doc.file_count,
        "total_size": doc.total_size,
        "created_at": doc.created_at,
        "updated_at": doc.updated_at,
        "tags": [TagResponse(id=tag.id, name=tag.name, color=tag.color, document_count=0)
                 for tag in doc.tags],
        "current_version": None,
        "sync_status": doc.sync_status,
        "cloud_sync_enabled": doc.cloud_sync_enabled,
        "last_synced_at": doc.last_synced_at,
        "local_modified": doc.local_modified,
        "cloud_updated_at": doc.cloud_updated_at,
        "cloud_source": doc.cloud_source,
        "cloud_doc_id": doc.cloud_doc_id,
        "cloud_pushed_at": doc.cloud_pushed_at,
        "cloud_hash": doc.cloud_hash,
        "workspace_id": doc.workspace_id,
    }
    if current_version:
        response["current_version"] = VersionResponse(
            id=current_version.id,
            version_number=current_version.version_number,
            file_path=current_version.file_path,
            relative_path=current_version.relative_path,
            sha256_hash=current_version.sha256_hash,
            manifest_hash=current_version.manifest_hash,
            file_size=current_version.file_size,
            original_filename=current_version.original_filename,
            note=current_version.note,
            is_current=True,
            created_at=current_version.created_at,
        )
    return response
