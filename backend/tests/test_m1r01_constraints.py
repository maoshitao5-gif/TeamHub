"""
M1-R01 个人库版本约束测试

- (document_id, version_number) 唯一:空库(模型约束)拒绝同资料重复版本号;
- current_version_id 存在性(FK):不存在或指向其他资料版本的当前版本不能通过
  受支持的写入路径落库;
- 不同资料可用相同版本号与内容哈希。
"""
import uuid

import pytest
from sqlalchemy.exc import IntegrityError

import backend.app.database as database
from backend.app.config import settings
from backend.app.models import Document, Version
from backend.app.services import versions as version_service
from backend.app.services.versions import VersionServiceError


@pytest.fixture
def library(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    settings.library_path = str(lib)
    database.rebind_engine()
    database.init_db()
    yield lib
    settings.library_path = str(tmp_path / "lib-rest")
    database.rebind_engine()
    database.init_db()


def test_duplicate_version_number_rejected(library):
    """空库模型约束:同资料重复版本号必须被拒绝"""
    db = database.SessionLocal()
    try:
        doc = Document(name="资料X")
        db.add(doc)
        db.flush()
        db.add(Version(document_id=doc.id, version_number=1,
                       file_path="/x", original_filename="a.txt"))
        db.commit()
        db.add(Version(document_id=doc.id, version_number=1,
                       file_path="/y", original_filename="b.txt"))
        with pytest.raises(IntegrityError):
            db.commit()
    finally:
        db.rollback()
        db.close()


def test_different_documents_share_version_number_and_hash(library):
    """不同资料可以使用相同版本号与内容哈希(约束不全局化)"""
    db = database.SessionLocal()
    try:
        d1 = Document(name="甲")
        d2 = Document(name="乙")
        db.add_all([d1, d2])
        db.flush()
        same_hash = "ab" * 32
        db.add(Version(document_id=d1.id, version_number=1,
                       file_path="/a", original_filename="a.txt", sha256_hash=same_hash))
        db.add(Version(document_id=d2.id, version_number=1,
                       file_path="/b", original_filename="b.txt", sha256_hash=same_hash))
        db.commit()  # 不应触发约束
    finally:
        db.close()


def test_dangling_current_version_rejected(library):
    """current_version_id 指向不存在的版本 ID,必须被 FK 拒绝"""
    db = database.SessionLocal()
    try:
        db.add(Document(name="资料X", current_version_id=str(uuid.uuid4())))
        with pytest.raises(IntegrityError):
            db.commit()
    finally:
        db.rollback()
        db.close()


def test_mark_current_rejects_foreign_document_version(library):
    """写入路径:不得把其他资料的版本绑定为当前版(服务层归属校验)"""
    db = database.SessionLocal()
    try:
        d1 = Document(name="甲")
        d2 = Document(name="乙")
        db.add_all([d1, d2])
        db.flush()
        v1 = Version(document_id=d1.id, version_number=1,
                     file_path="/a", original_filename="a.txt")
        db.add(v1)
        db.commit()

        # 把 d1 的版本 v1 绑定为 d2 的当前版 → 服务层拒绝
        with pytest.raises(VersionServiceError):
            version_service._mark_current(db, d2, v1)
    finally:
        db.rollback()
        db.close()


def test_valid_current_version_binds(library):
    """正常当前版本绑定成功,且 current_version_id 为唯一事实来源"""
    db = database.SessionLocal()
    try:
        doc = Document(name="资料X")
        db.add(doc)
        db.flush()
        v = Version(document_id=doc.id, version_number=1,
                    file_path="/a", original_filename="a.txt")
        db.add(v)
        db.flush()
        version_service._mark_current(db, doc, v)
        db.commit()
        assert doc.current_version_id == v.id
        assert v.is_current is True
    finally:
        db.rollback()
        db.close()
