"""标签优先的整理流程：工作目录可选，文件移动与资料状态保持一致。"""

import pytest
from fastapi.testclient import TestClient

import backend.app.database as database
from backend.app.config import settings
from main import app


client = TestClient(app)


@pytest.fixture
def library(tmp_path):
    previous_library = settings.library_path
    library = tmp_path / "个人库"
    library.mkdir()
    settings.library_path = str(library)
    database.rebind_engine()
    database.init_db()
    yield library
    settings.library_path = previous_library
    database.rebind_engine()


def _pending(tmp_path, name="设备说明.txt", content="说明内容", folder=False):
    source = tmp_path / name
    if folder:
        (source / "图纸").mkdir(parents=True)
        (source / "图纸" / "接线.txt").write_text(content, encoding="utf-8")
        (source / "空目录").mkdir()
        (source / ".说明").write_text("隐藏说明", encoding="utf-8")
    else:
        source.write_text(content, encoding="utf-8")
    response = client.post("/api/documents/quick-add", json={"source_path": str(source)})
    assert response.status_code == 200, response.text
    return response.json()


def _organize(doc, batch=False, **payload):
    payload.setdefault("tags", ["设备", "检修"])
    if batch:
        payload["document_ids"] = [doc["id"]]
        return client.post("/api/documents/batch/organize", json=payload)
    return client.post(f"/api/documents/{doc['id']}/organize", json=payload)


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("target", [{}, {"target_dir": ""}, {"target_dir": None}])
@pytest.mark.parametrize("folder", [False, True])
def test_tags_only_organizes_into_root_without_changing_snapshot(library, tmp_path, batch, target, folder):
    name = "改造工程" if folder else "设备说明.txt"
    doc = _pending(tmp_path, name=name, folder=folder)
    version_before = client.get(f"/api/documents/{doc['id']}/versions").json()

    response = _organize(doc, batch=batch, **target)
    assert response.status_code == 200, response.text
    current = client.get(f"/api/documents/{doc['id']}").json()
    assert current["status"] == "organized"
    assert current["storage_path"] == name
    assert {tag["name"] for tag in current["tags"]} == {"设备", "检修"}
    assert not (library / doc["storage_path"]).exists()
    assert client.get("/api/documents/pending/count").json()["count"] == 0
    assert client.get(f"/api/documents/{doc['id']}/versions").json() == version_before
    if folder:
        assert (library / name / "图纸" / "接线.txt").read_text(encoding="utf-8") == "说明内容"
        assert (library / name / "空目录").is_dir()
        assert (library / name / ".说明").read_text(encoding="utf-8") == "隐藏说明"
    else:
        assert (library / name).read_text(encoding="utf-8") == "说明内容"


def test_batch_appends_common_tags_and_preserves_individual_tags(library, tmp_path):
    docs = [_pending(tmp_path, name=f"资料{i}.txt", content=f"内容{i}") for i in range(2)]
    for i, doc in enumerate(docs):
        response = client.put(f"/api/documents/{doc['id']}/tags", json=[f"设备{i}", "检修"])
        assert response.status_code == 200, response.text

    response = client.post("/api/documents/batch/organize", json={
        "document_ids": [doc["id"] for doc in docs], "tags": ["检修", "项目", "项目"],
    })
    assert response.status_code == 200, response.text
    for i, doc in enumerate(docs):
        current = client.get(f"/api/documents/{doc['id']}").json()
        assert {tag["name"] for tag in current["tags"]} == {f"设备{i}", "检修", "项目"}
        assert len(current["tags"]) == 3


@pytest.mark.parametrize("batch", [False, True])
def test_explicit_working_folder_remains_supported(library, tmp_path, batch):
    doc = _pending(tmp_path)
    response = _organize(doc, batch=batch, target_dir="工程/交付资料")
    assert response.status_code == 200, response.text
    current = client.get(f"/api/documents/{doc['id']}").json()
    assert current["storage_path"] == "工程/交付资料/设备说明.txt"
    assert (library / current["storage_path"]).is_file()


@pytest.mark.parametrize("batch", [False, True])
def test_root_collision_keeps_existing_file_and_pending_document(library, tmp_path, batch):
    doc = _pending(tmp_path)
    (library / doc["name"]).write_text("已有资料", encoding="utf-8")
    response = _organize(doc, batch=batch)
    assert response.status_code == 400, response.text
    assert (library / doc["name"]).read_text(encoding="utf-8") == "已有资料"
    assert (library / doc["storage_path"]).read_text(encoding="utf-8") == "说明内容"
    current = client.get(f"/api/documents/{doc['id']}").json()
    assert current["status"] == "pending"
    assert current["storage_path"] == doc["storage_path"]
    assert current["tags"] == []


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("target", ["待整理", "待整理/项目"])
def test_cannot_mark_document_organized_inside_pending_directory(library, tmp_path, batch, target):
    doc = _pending(tmp_path)
    response = _organize(doc, batch=batch, target_dir=target)
    assert response.status_code == 400, response.text
    assert client.get(f"/api/documents/{doc['id']}").json()["status"] == "pending"
    assert (library / doc["storage_path"]).is_file()


@pytest.mark.parametrize("batch", [False, True])
def test_blank_tags_cannot_complete_organization(library, tmp_path, batch):
    doc = _pending(tmp_path)
    response = _organize(doc, batch=batch, tags=[" ", "\t"])
    assert response.status_code == 400, response.text
    assert client.get(f"/api/documents/{doc['id']}").json()["status"] == "pending"
    assert (library / doc["storage_path"]).is_file()


def test_batch_root_commit_failure_restores_all_files_and_original_tags(library, tmp_path, monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError

    docs = [_pending(tmp_path, name=f"资料{i}.txt", content=f"内容{i}") for i in range(2)]
    for doc in docs:
        assert client.put(f"/api/documents/{doc['id']}/tags", json=["原有标签"]).status_code == 200

    def fail_commit(self):
        raise SQLAlchemyError("injected commit failure")

    with monkeypatch.context() as patch:
        patch.setattr(database.TrackedSession, "commit", fail_commit)
        response = client.post("/api/documents/batch/organize", json={
            "document_ids": [doc["id"] for doc in docs], "tags": ["新标签"],
        })
        assert response.status_code == 500, response.text
    for i, doc in enumerate(docs):
        current = client.get(f"/api/documents/{doc['id']}").json()
        assert current["status"] == "pending"
        assert current["storage_path"] == doc["storage_path"]
        assert [tag["name"] for tag in current["tags"]] == ["原有标签"]
        assert (library / doc["storage_path"]).read_text(encoding="utf-8") == f"内容{i}"
        assert not (library / doc["name"]).exists()
