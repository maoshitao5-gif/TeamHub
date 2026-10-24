"""共用整理事务：移动工作副本并提交，提交失败时补偿文件位置。"""
from contextlib import contextmanager
from pathlib import Path
import shutil

from fastapi import HTTPException
from sqlalchemy.orm import Session, lazyload, object_session

from backend.app.config import settings
from backend.app.models import Tag
from backend.app.core.logger import get_logger
from backend.app.core.storage_paths import (
    classification_directory, document_path, pending_root, working_location,
)

logger = get_logger('services.preorganize_storage')


@contextmanager
def working_changes(db: Session):
    """文件移动与数据库提交的失败补偿；历史对象始终不参与移动。"""
    moves = []
    def move(source: Path, target: Path):
        if source == target:
            return
        if not source.exists():
            raise HTTPException(409, "工作文件缺失，请重新定位后重试")
        if target.exists() or target.is_symlink():
            raise HTTPException(400, f"目标位置已存在: {target.name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(source, target)
        moves.append((source, target))
    try:
        yield move
        db.commit()
    except Exception as exc:
        db.rollback()
        for source, target in reversed(moves):
            try:
                if source.exists():
                    raise OSError("原位置已被占用，拒绝覆盖")
                source.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(target, source)
            except OSError as rollback_error:
                logger.exception("移动回滚失败: %s -> %s", target, source)
                raise HTTPException(500, f"操作失败且无法回滚，文件保留在 {target}，请核对后重试") from rollback_error
        if isinstance(exc, HTTPException):
            raise
        logger.exception("文件操作或数据库提交失败，已执行回滚")
        from backend.app.services.snapshots import SnapshotChangedError
        if isinstance(exc, SnapshotChangedError):
            raise HTTPException(409, "文件夹仍在变化，请等待编辑或同步完成后再收录；本次操作已回滚") from exc
        raise HTTPException(500, "文件操作或提交失败，已回滚") from exc



def organize_working(doc, target_dir, name, tags, move):
    if not doc.storage_path:
        raise HTTPException(400, "资料没有受管工作副本")
    if not tags:
        raise HTTPException(400, "请至少添加一个有效标签后完成整理")
    source = document_path(doc)
    new_name = name or doc.name
    if not doc.is_folder:
        from backend.app.services.archive_formats import ARCHIVE_SUFFIXES
        archive_suffix = next((source.name[-len(ending):] for ending in
                               sorted(ARCHIVE_SUFFIXES, key=len, reverse=True)
                               if source.name.lower().endswith(ending)), None)
        if archive_suffix and not new_name.lower().endswith(archive_suffix.lower()):
            # The form edits the stem; a dot in that stem is not a new format.
            new_name += archive_suffix
        elif not Path(new_name).suffix:
            new_name += source.suffix
    if Path(new_name).name != new_name or "\\" in new_name:
        raise HTTPException(400, "名称不能包含目录")
    directory = classification_directory(target_dir or "", object_session(doc), allow_root=True)
    target = working_location(str(directory.relative_to(Path(settings.library_path).resolve()) / new_name))
    pending = pending_root()
    if pending.is_relative_to(Path(settings.library_path).resolve()) and target.is_relative_to(pending):
        raise HTTPException(400, "整理后的资料不能放入待整理目录，请不选文件夹或选择其他工作目录")
    if not source.exists():
        raise HTTPException(409, "工作文件缺失，请重新定位后重试")
    move(source, target)
    doc.storage_path = str(target.relative_to(Path(settings.library_path).resolve()))
    if name:
        doc.name = new_name
    doc.storage_area = "library"
    doc.status = "organized"
    doc.tags = list({tag.name: tag for tag in tags}.values())


def get_or_create_tags(db: Session, tag_names: list[str]) -> list[Tag]:
    """按归一化名称去重，再批量读取；一个标签只建立一次关联。"""
    names = list(dict.fromkeys(name.strip() for name in tag_names if name.strip()))
    if not names:
        return []
    existing = db.query(Tag).options(lazyload(Tag.documents)).filter(Tag.name.in_(names)).all()
    by_name = {tag.name: tag for tag in existing}
    new_tags = [Tag(name=name) for name in names if name not in by_name]
    if new_tags:
        db.add_all(new_tags)
        db.flush()
        by_name.update((tag.name, tag) for tag in new_tags)
    return [by_name[name] for name in names]

