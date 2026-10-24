"""列表读取只加载响应需要的数据，同时守住标签、版本归属和分类边界。"""
from contextlib import contextmanager

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

import backend.app.database as database
from backend.app.models import ChangeLog, Document, Favorite, Tag, Version
from backend.tests.test_organize_workflow import client, library, _pending


@contextmanager
def read_metrics():
    metrics = {"selects": 0, "versions": 0}

    def count_select(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            metrics["selects"] += 1

    def count_version(_session, instance):
        if isinstance(instance, Version):
            metrics["versions"] += 1

    event.listen(database.engine, "before_cursor_execute", count_select)
    event.listen(Session, "loaded_as_persistent", count_version)
    try:
        yield metrics
    finally:
        event.remove(database.engine, "before_cursor_execute", count_select)
        event.remove(Session, "loaded_as_persistent", count_version)


@pytest.fixture
def catalog(library):
    with database.SessionLocal() as db:
        for number in range(20):
            doc = Document(name=f"资料{number}.txt", status="organized",
                           storage_path=f"资料{number}.txt", tags=[Tag(name=f"标签{number}")])
            db.add(doc)
            db.flush()
            versions = [Version(document_id=doc.id, version_number=index,
                                file_path=f".teamhub/objects/{number}-{index}",
                                original_filename=doc.name, is_current=index == 4)
                        for index in range(1, 11)]
            db.add_all(versions)
            db.flush()
            doc.current_version_id = versions[3].id
            db.add(Favorite(target_type="local", local_document_id=doc.id))
        db.commit()
    return library


@pytest.mark.parametrize("endpoint,limit", [("tags", 2), ("favorites", 4), ("search", 4)])
def test_list_query_and_version_load_budget(catalog, endpoint, limit):
    with read_metrics() as metrics:
        response = (client.post("/api/documents/search", json={"status": "organized", "page_size": 20})
                    if endpoint == "search" else client.get(f"/api/{endpoint}"))
    assert response.status_code == 200, response.text
    body = response.json()
    records = body["documents"] if endpoint == "search" else body
    assert len(records) == 20
    if endpoint == "tags":
        assert all(tag["document_count"] == tag["usage_count"] == 1 for tag in records)
    else:
        docs = [row["document"] for row in records] if endpoint == "favorites" else records
        assert all(doc["current_version"]["version_number"] == 4 for doc in docs)
    print(f"{endpoint}: {metrics}")
    assert metrics["selects"] <= limit, metrics
    assert metrics["versions"] <= (0 if endpoint == "tags" else 20), metrics


def test_tag_counts_keep_all_states_and_distinct_totals(library):
    with database.SessionLocal() as db:
        common, unused = Tag(name="共同"), Tag(name="未使用")
        db.add_all([common, unused])
        db.add_all([Document(name=status, status=status, tags=[common])
                    for status in ("organized", "pending", "missing", "trashed")])
        db.commit()
    result = client.get("/api/tags?include_stats=true").json()
    tags = {tag["name"]: tag for tag in result["tags"]}
    assert tags["共同"]["document_count"] == 1
    assert tags["共同"]["usage_count"] == 4
    assert tags["共同"]["usage_by_status"] == dict.fromkeys(
        ("organized", "pending", "missing", "trashed"), 1)
    assert tags["未使用"]["usage_count"] == 0
    assert tags["未使用"]["usage_by_status"] == {}
    assert result["total_unique_documents"] == 1
    assert result["total_associated_documents"] == 4


def test_normalized_duplicate_tags_do_not_duplicate_association(library, tmp_path):
    doc = _pending(tmp_path)
    response = client.put(f"/api/documents/{doc['id']}/tags", json=["设备", " 设备 ", "", "检修"])
    assert response.status_code == 200, response.text
    assert {tag["name"] for tag in response.json()["tags"]} == {"设备", "检修"}
    assert client.put(f"/api/documents/{doc['id']}/tags", json=[" 设备", "设备 "]).status_code == 200


def test_create_tag_records_its_assigned_identifier(library):
    response = client.post("/api/tags", json={"name": "设备"})
    assert response.status_code == 200, response.text
    with database.SessionLocal() as db:
        changes = db.query(ChangeLog).filter_by(entity_type="tag", operation="create").all()
        assert [change.entity_id for change in changes] == [response.json()["id"]]


def test_current_version_read_never_uses_another_documents_pointer(catalog):
    from backend.app.api.documents import _build_document_response

    with database.SessionLocal() as db:
        first, second = db.query(Document).limit(2).all()
        first.current_version_id = second.current_version_id
        # 数据库还有归属触发器；这里独立验证响应层不因读取优化绕开原有归属语义。
        with db.no_autoflush:
            assert _build_document_response(first)["current_version"] is None


def test_dirtree_fetches_folder_boundaries_once(library):
    for number in range(20):
        (library / f"分类{number}").mkdir()
    (library / "整体资料" / "内部").mkdir(parents=True)
    (library / "待整理").mkdir(exist_ok=True)
    (library / "链接").symlink_to(library / "分类0", target_is_directory=True)
    with database.SessionLocal() as db:
        db.add(Document(name="整体资料", is_folder=True, status="organized", storage_path="整体资料"))
        db.commit()
    with read_metrics() as metrics:
        response = client.get("/api/settings/library/dirtree")
    assert response.status_code == 200, response.text
    assert {row["name"] for row in response.json()["items"]} == {f"分类{number}" for number in range(20)}
    print(f"dirtree: {metrics}")
    assert metrics["selects"] <= 1, metrics
    assert client.get("/api/settings/library/dirtree", params={"parent": "整体资料/内部"}).status_code == 409
    assert client.get("/api/settings/library/dirtree", params={"parent": "../"}).status_code == 400


def test_dirtree_preserves_legacy_index_folder_boundary(library):
    (library / "分类").mkdir()
    whole = library / "整体资料"
    (whole / "内部").mkdir(parents=True)
    with database.SessionLocal() as db:
        db.add(Document(name="整体资料", is_folder=True, status="organized",
                        storage_path="", storage_mode="index", original_path=str(whole)))
        db.commit()
    response = client.get("/api/settings/library/dirtree")
    assert response.status_code == 200, response.text
    assert "分类" in {row["name"] for row in response.json()["items"]}
    assert "整体资料" not in {row["name"] for row in response.json()["items"]}
    response = client.get("/api/settings/library/dirtree", params={"parent": "整体资料/内部"})
    assert response.status_code == 409, response.text
