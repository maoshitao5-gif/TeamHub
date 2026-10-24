"""
版本 API 路由(M2 不可变快照语义)

按 docs/MVP数据与接口契约.md §5.1:
- GET 版本列表(版本号倒序);
- POST 从工作位置创建快照新版本;内容与当前版一致 → 200 CONTENT_UNCHANGED;
- POST /{vid}/restore 恢复 = 替换工作文件并切换当前版本,保留历史;
- POST /{vid}/export 导出指定版本内容到目标目录,不覆写已有文件;
- DELETE 删除非当前版本记录；对象文件保留，等待引用安全的 GC。
"""
import hashlib

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import exists, func

from backend.app.database import get_db
from backend.app.models import Version, Document
from backend.app.core.file_utils import get_document_or_404
from backend.app.core.logger import get_logger
from backend.app.api.schemas import VersionCreate, VersionResponse
from backend.app.services import versions as version_service
from backend.app.services.versions import VersionServiceError

logger = get_logger("api.versions")
router = APIRouter(prefix="/api/documents/{document_id}/versions", tags=["versions"])



class VersionExportRequest(BaseModel):
    """导出指定版本请求"""
    target_dir: str = Field(..., min_length=1, description="导出目标目录(绝对路径)")


def _version_response(version, current_id):
    response = VersionResponse.model_validate(version)
    response.is_current = version.id == current_id
    return response


@router.get("", response_model=list[VersionResponse])
def list_versions(document_id: str, db: Session = Depends(get_db)):
    """获取文档的所有版本(按版本号倒序)"""
    doc = get_document_or_404(db, document_id)

    versions = db.query(Version).filter(
        Version.document_id == document_id
    ).order_by(Version.version_number.desc()).all()

    return [_version_response(version, doc.current_version_id) for version in versions]


@router.get("/working-state")
def working_state(document_id: str, db: Session = Depends(get_db)):
    """按需检查工作内容，不创建对象或修改已保存版本。"""
    from backend.app.services import snapshots
    doc = get_document_or_404(db, document_id)
    if doc.status == "trashed":
        return {"state": "unavailable", "message": "资料在回收站中"}
    try:
        source = version_service.working_path(doc)
        if source.is_symlink():
            raise snapshots.SnapshotError("不支持符号链接工作文件")
        if not source.exists():
            return {"state": "missing", "message": "工作文件已缺失，历史版本仍可导出"}
        if doc.is_folder:
            manifest, _ = snapshots._build_folder_manifest(source)
            digest = hashlib.sha256(manifest).hexdigest()
        else:
            # 检查前后身份，避免把读取中变化的内容宣告为已保存。
            before = source.stat()
            digest = snapshots._hash_file(source)
            if snapshots._file_identity(before) != snapshots._file_identity(source.stat()):
                raise snapshots.SnapshotError("检查期间文件发生变化，请重试")
        current = db.get(Version, doc.current_version_id) if doc.current_version_id else None
        changed = current is None or current.sha256_hash != digest
        return {"state": "changed" if changed else "unchanged",
                "message": "有尚未保存的修改" if changed else "与当前已保存版本一致"}
    except (snapshots.SnapshotError, VersionServiceError, OSError) as exc:
        return {"state": "unavailable", "message": str(exc)}


@router.post("")
def add_version(document_id: str, req: VersionCreate, db: Session = Depends(get_db)):
    """从工作位置创建快照新版本(契约 §5.1)"""
    doc = get_document_or_404(db, document_id)
    if doc.status == "trashed":
        raise HTTPException(400, "文档在回收站中,请先恢复")

    try:
        version, created = version_service.snapshot_working_copy(
            db, doc, note=req.note, source_path=req.file_path or None
        )
    except VersionServiceError as e:
        raise HTTPException(e.status_code, str(e)) from e

    db.commit()

    if not created:
        # 契约 CONTENT_UNCHANGED:返回当前版本,不产生新版本
        return JSONResponse({
            "error": {
                "code": "CONTENT_UNCHANGED",
                "message": "内容与当前版本一致,未产生新版本",
                "details": None,
            },
            "version": _version_response(version, doc.current_version_id).model_dump(mode="json"),
        })

    logger.info(f"文档 {doc.id} 新版本 v{version.version_number} 已创建")
    return _version_response(version, doc.current_version_id)


@router.post("/{version_id}/restore", response_model=VersionResponse)
def restore_version(document_id: str, version_id: str, db: Session = Depends(get_db)):
    """恢复历史版本到工作位置，未保存修改自动保护"""
    doc = get_document_or_404(db, document_id)
    if doc.status == "trashed":
        raise HTTPException(400, "文档在回收站中，请先恢复")
    target = db.get(Version, version_id)
    if not target or target.document_id != doc.id:
        raise HTTPException(404, "版本不存在")

    try:
        version = version_service.restore_working_version(db, doc, target)
    except VersionServiceError as e:
        raise HTTPException(e.status_code, str(e)) from e

    logger.info(f"文档 {doc.id} 已恢复到 v{version.version_number}")
    return _version_response(version, doc.current_version_id)


@router.post("/{version_id}/export")
def export_version(document_id: str, version_id: str, req: VersionExportRequest,
                   db: Session = Depends(get_db)):
    """导出指定版本内容到目标目录,不覆写已有文件"""
    doc = get_document_or_404(db, document_id)
    target = db.get(Version, version_id)
    if not target or target.document_id != doc.id:
        raise HTTPException(404, "版本不存在")

    try:
        exported = version_service.export_version(doc, target, req.target_dir)
    except VersionServiceError as e:
        raise HTTPException(e.status_code, str(e)) from e

    return {"message": "已导出", "path": str(exported)}


@router.delete("/{version_id}")
def delete_version(document_id: str, version_id: str, db: Session = Depends(get_db)):
    """删除历史记录，不删除底层可能被共用的不可变快照。"""
    from backend.app.core.changelog import record_change
    doc = get_document_or_404(db, document_id)
    version = db.query(Version).filter(Version.id == version_id,
                                      Version.document_id == document_id).first()
    if not version:
        raise HTTPException(404, "版本不存在")
    if doc.current_version_id == version_id:
        raise HTTPException(409, "当前版本不能删除，请先恢复到其他版本")
    number = version.version_number
    highest = db.query(func.max(Version.version_number)).filter(Version.document_id == document_id).scalar() or 0
    doc.version_sequence = max(doc.version_sequence or 0, highest)
    db.flush()
    # 即使界面信息过时，删除语句也不能清掉当前版本。
    referenced = exists().where(Document.current_version_id == Version.id)
    deleted = db.query(Version).filter(Version.id == version_id,
                                      Version.document_id == document_id,
                                      ~referenced).delete(synchronize_session=False)
    if not deleted:
        db.rollback()
        raise HTTPException(409, "版本状态已变化，请刷新后重试；当前版本不能删除")
    record_change(db, 'version', version_id, 'delete', {
        'document_id': document_id, 'version_number': number,
    })
    db.commit()
    return {"message": f"已删除 v{number}", "version_id": version_id,
            "snapshot_retained": True}
