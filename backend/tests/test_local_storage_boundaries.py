"""本机目录边界与回收站提交失败时的工作内容保护。"""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

import backend.app.database as database
from backend.app.models import Document, utc_now
from backend.tests.test_organize_workflow import library, _pending, _organize
from main import app

client = TestClient(app, raise_server_exceptions=False)


def test_subdir_listing_cannot_escape_library(library, tmp_path):
    (tmp_path / "private-sibling").mkdir()
    response = client.get("/api/settings/library/subdirs", params={"parent": ".."})
    assert response.status_code == 400


def test_subdir_listing_hides_metadata_pending_and_linked_directories(library, tmp_path):
    (library / "category").mkdir()
    (library / "linked").symlink_to(tmp_path)
    whole = _pending(tmp_path, name="whole", folder=True)
    assert _organize(whole).status_code == 200
    response = client.get("/api/settings/library/subdirs")
    assert response.status_code == 200
    assert response.json()["dirs"] == [{"name": "category", "path": "category"}]


@pytest.mark.parametrize("operation", ["ingest", "organize"])
def test_whole_folder_cannot_become_classification_target(library, tmp_path, operation):
    parent = _pending(tmp_path, name="whole", folder=True)
    assert _organize(parent).status_code == 200
    source = tmp_path / "child.txt"
    if operation == "ingest":
        source.write_text("distinct child")
        response = client.post("/api/documents", json={
            "name": source.name, "source_path": str(source), "storage_mode": "copy",
            "tags": ["分类"], "target_dir": "whole",
        })
    else:
        child = _pending(tmp_path, name=source.name, content="distinct child")
        response = _organize(child, target_dir="whole")
    assert response.status_code == 409
    assert not (library / "whole" / source.name).exists()


@pytest.mark.parametrize("permanent", [False, True])
@pytest.mark.parametrize("target", ["child", "parent"])
def test_legacy_nested_document_deletion_preserves_records_and_content(
    library, tmp_path, permanent, target
):
    from backend.app.services.versions import snapshot_working_copy

    parent = _pending(tmp_path, name="whole", folder=True)
    assert _organize(parent).status_code == 200
    nested = library / "whole" / "图纸" / "接线.txt"
    with database.SessionLocal() as db:
        child = Document(
            name="legacy child", storage_path="whole/图纸/接线.txt",
            storage_mode="copy", storage_area="library", status="organized",
        )
        db.add(child)
        db.flush()
        version, _ = snapshot_working_copy(db, child)
        db.commit()
        child_id = child.id
        version_id = version.id
    document_id = child_id if target == "child" else parent["id"]
    response = client.delete(f"/api/documents/{document_id}", params={"permanent": permanent})
    assert response.status_code == 409
    assert nested.read_text(encoding="utf-8") == "说明内容"
    assert client.get(f"/api/documents/{parent['id']}").json()["status"] == "organized"
    child_response = client.get(f"/api/documents/{child_id}").json()
    assert child_response["status"] == "organized"
    assert child_response["current_version"]["id"] == version_id
    assert client.get(f"/api/documents/{child_id}/versions").json()[0]["id"] == version_id
    assert not (library / ".teamhub/trash" / document_id).exists()


@pytest.mark.parametrize("automatic", [False, True])
def test_trash_cleanup_commit_failure_keeps_unsaved_work(library, tmp_path, monkeypatch, automatic):
    doc = _pending(tmp_path)
    (library / doc["storage_path"]).write_text("unsaved work must survive")
    assert client.delete(f"/api/documents/{doc['id']}").status_code == 200
    trash = library / ".teamhub" / "trash" / doc["id"]
    with database.SessionLocal() as db:
        db.get(Document, doc["id"]).trashed_at = utc_now() - timedelta(days=60)
        db.commit()

    def fail_commit(self):
        raise SQLAlchemyError("injected commit failure")

    with monkeypatch.context() as patch:
        patch.setattr(database.TrackedSession, "commit", fail_commit)
        if automatic:
            from backend.app.core.trash_cleaner import cleanup_old_trash
            assert cleanup_old_trash(30, str(library)) == 0
        else:
            assert client.post("/api/settings/trash/clean").status_code == 500

    assert trash.read_text() == "unsaved work must survive"
    with database.SessionLocal() as db:
        assert db.get(Document, doc["id"]).status == "trashed"
