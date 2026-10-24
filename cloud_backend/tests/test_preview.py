"""Fixed-version previews, ACL isolation and bounded untrusted Office parsing."""
import hashlib
from io import BytesIO
import stat
import struct
import uuid
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core.deps import get_current_user
from app.core.object_store import object_path
from app.database import SessionLocal
from app.models import User, Team, TeamMember, Workspace, CloudDocument, CloudVersion, DocumentAccess
from app.services import preview
from main import app

client = TestClient(app)
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"


def package(parts):
    content = BytesIO()
    with zipfile.ZipFile(content, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in parts.items():
            archive.writestr(name, text)
    return content.getvalue()


def word(body):
    return package({"word/document.xml": f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>'})


def workbook(sheet_xml, extra=None, target="worksheets/sheet1.xml", target_mode=""):
    return package({
        "xl/workbook.xml": f'<workbook xmlns="{S}" xmlns:r="{R}"><sheets><sheet name="设备台账" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": f'<Relationships xmlns="{REL}"><Relationship Id="rId1" Type="{R}/worksheet" Target="{target}" {target_mode}/></Relationships>',
        "xl/worksheets/sheet1.xml": f'<worksheet xmlns="{S}"><sheetData>{sheet_xml}</sheetData></worksheet>',
        **(extra or {}),
    })


def image_header(kind, width=1, height=1):
    if kind == "png":
        return b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR" + width.to_bytes(4, "big") + height.to_bytes(4, "big")
    if kind == "gif":
        return b"GIF89a" + width.to_bytes(2, "little") + height.to_bytes(2, "little")
    if kind == "bmp":
        return b"BM" + b"\0" * 12 + (40).to_bytes(4, "little") + width.to_bytes(4, "little") + height.to_bytes(4, "little")
    if kind == "webp":
        return b"RIFF\0\0\0\0WEBPVP8X\x0a\0\0\0\0\0\0\0" + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    return b"\xff\xd8\xff\xc0\x00\x0b\x08" + height.to_bytes(2, "big") + width.to_bytes(2, "big") + b"\x01\x01\x11\x00"


@pytest.fixture
def space(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path / "cloud-objects"))
    with SessionLocal() as db:
        team = Team(name="预览", slug=str(uuid.uuid4()))
        users = {name: User(email=f"{uuid.uuid4()}@preview.test", hashed_password="x",
                            display_name=name, is_superuser=name == "admin")
                 for name in ("creator", "reader", "outsider", "admin")}
        db.add_all([team, *users.values()]); db.flush()
        workspace = Workspace(team_id=team.id, name="预览")
        db.add(workspace); db.flush()
        db.add_all([TeamMember(team_id=team.id, user_id=user.id,
                              role="admin" if name == "admin" else "member")
                    for name, user in users.items() if name != "outsider"])
        db.commit()
        result = {**users, "workspace_id": workspace.id}
        yield result
    app.dependency_overrides.clear()


def as_user(user):
    app.dependency_overrides[get_current_user] = lambda: user


def seed(space, content, filename="test.txt", *, visibility="workspace", doc_type="file"):
    digest = hashlib.sha256(content).hexdigest()
    path = object_path(digest)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    with SessionLocal() as db:
        document = CloudDocument(workspace_id=space["workspace_id"], name=filename,
                                 created_by=space["creator"].id, visibility=visibility, doc_type=doc_type)
        db.add(document); db.flush()
        version = CloudVersion(document_id=document.id, version_number=1, content_hash=digest,
                               file_size=len(content), original_filename=filename)
        db.add(version); db.flush()
        document.current_version_id = version.id
        if visibility == "restricted":
            db.add_all([DocumentAccess(document_id=document.id, user_id=space[name].id, role=role)
                        for name, role in (("creator", "manager"), ("reader", "reader"))])
        db.commit()
        return document.id, version.id, path


def route(space, document, version):
    return f'/api/workspaces/{space["workspace_id"]}/documents/{document}/versions/{version}/preview'


def test_preview_fixed_history_and_no_cloud_mutation(space):
    document, version, _ = seed(space, "第一稿".encode())
    digest = hashlib.sha256(b"current").hexdigest()
    current_path = object_path(digest); current_path.parent.mkdir(parents=True, exist_ok=True)
    current_path.write_bytes(b"current")
    with SessionLocal() as db:
        latest = CloudVersion(document_id=document, version_number=2, content_hash=digest,
                              original_filename="test.txt", file_size=7)
        db.add(latest); db.flush()
        db.get(CloudDocument, document).current_version_id = latest.id
        db.commit()
        current_id = latest.id
        count = db.query(CloudVersion).count()
    as_user(space["reader"])
    result = client.get(route(space, document, version))
    assert result.status_code == 200, result.text
    assert result.json()["text"] == "第一稿"
    assert result.json()["version_id"] == version and result.json()["version_number"] == 1
    assert result.headers["cache-control"] == "no-store"
    assert result.headers["x-content-type-options"] == "nosniff"
    with SessionLocal() as db:
        assert db.get(CloudDocument, document).current_version_id == current_id
        assert db.query(CloudVersion).count() == count


@pytest.mark.parametrize("user", ["outsider", "admin"])
def test_restricted_permission_applies_to_metadata_and_bytes(space, user):
    document, version, _ = seed(space, b"%PDF-1.7\npreview", "private.pdf", visibility="restricted")
    as_user(space[user])
    for suffix in ("", "/content"):
        assert client.get(route(space, document, version) + suffix).status_code == 404
    as_user(space["reader"])
    assert client.get(route(space, document, version)).status_code == 200
    assert client.get(route(space, document, version) + "/content").status_code == 200


def test_revoke_then_trash_is_rechecked_for_each_request(space):
    document, version, _ = seed(space, b"%PDF-1.7\npreview", "private.pdf", visibility="restricted")
    as_user(space["reader"])
    assert client.get(route(space, document, version)).status_code == 200
    with SessionLocal() as db:
        db.query(DocumentAccess).filter_by(document_id=document, user_id=space["reader"].id).delete()
        db.commit()
    assert client.get(route(space, document, version) + "/content").status_code == 404
    as_user(space["creator"])
    from datetime import datetime, timezone
    with SessionLocal() as db:
        db.get(CloudDocument, document).trashed_at = datetime.now(timezone.utc)
        db.commit()
    assert client.get(route(space, document, version)).status_code == 404
    assert client.get(route(space, document, version) + "/content").status_code == 404


def test_workspace_and_version_cannot_be_swapped(space):
    document, version, _ = seed(space, b"%PDF-1.7\npreview", "test.pdf")
    another, other_version, _ = seed(space, b"other")
    as_user(space["reader"])
    assert client.get(route(space, document, other_version)).status_code == 404
    wrong_workspace = {**space, "workspace_id": str(uuid.uuid4())}
    assert client.get(route(wrong_workspace, document, version)).status_code == 404
    assert client.get(route(space, another, version) + "/content").status_code == 404


def test_auth_required(space):
    document, version, _ = seed(space, b"example")
    app.dependency_overrides.clear()
    assert client.get(route(space, document, version)).status_code in (401, 403)


@pytest.mark.parametrize("filename,data,mime", [
    ("test.pdf", b"%PDF-1.7\npreview", "application/pdf"),
    ("test.png", image_header("png"), "image/png"),
    ("test.jpg", image_header("jpg"), "image/jpeg"),
    ("test.gif", image_header("gif"), "image/gif"),
    ("test.webp", image_header("webp"), "image/webp"),
    ("test.bmp", image_header("bmp"), "image/bmp"),
])
def test_safe_binary_mime_magic_and_headers(space, filename, data, mime):
    document, version, _ = seed(space, data, filename)
    as_user(space["reader"])
    metadata = client.get(route(space, document, version)).json()
    assert metadata["mime_type"] == mime and metadata["kind"] in ("image", "pdf")
    content = client.get(route(space, document, version) + "/content")
    assert content.content == data and content.headers["content-type"] == mime
    assert content.headers["cache-control"] == "no-store"
    assert content.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("filename", ["fake.pdf", "fake.png"])
def test_disguised_html_is_rejected(space, filename):
    document, version, _ = seed(space, b"<script>alert(1)</script>", filename)
    as_user(space["reader"])
    for suffix in ("", "/content"):
        result = client.get(route(space, document, version) + suffix)
        assert result.status_code == 422
        assert "script" not in result.text


@pytest.mark.parametrize("filename", ["danger.html", "diagram.svg", "values.xml"])
def test_active_text_formats_are_json_text_only(space, filename):
    text = '<script>alert(1)</script><!DOCTYPE svg SYSTEM "https://example.invalid/">'
    document, version, _ = seed(space, text.encode(), filename)
    as_user(space["reader"])
    result = client.get(route(space, document, version))
    assert result.json()["kind"] == "text" and result.json()["text"] == text
    assert result.headers["content-type"].startswith("application/json")
    assert client.get(route(space, document, version) + "/content").status_code == 415


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "gb18030"])
def test_text_decodes_common_chinese_encodings(space, encoding):
    document, version, _ = seed(space, "电力资料".encode(encoding))
    as_user(space["reader"])
    assert client.get(route(space, document, version)).json()["text"] == "电力资料"


def test_missing_and_corrupt_object_errors_are_fixed(space):
    document, version, path = seed(space, b"original")
    as_user(space["reader"])
    path.write_bytes(b"changed!")
    corrupt = client.get(route(space, document, version))
    assert corrupt.status_code == 422 and str(path) not in corrupt.text
    path.unlink()
    assert client.get(route(space, document, version)).status_code == 404


@pytest.mark.parametrize("filename,doc_type", [("old.doc", "file"), ("old.xls", "file"),
                                               ("archive.zip", "archive"), ("folder", "folder")])
def test_unsupported_format_is_friendly(space, filename, doc_type):
    document, version, _ = seed(space, b"content", filename, doc_type=doc_type)
    as_user(space["reader"])
    result = client.get(route(space, document, version))
    assert result.status_code == 200 and result.json()["kind"] == "unsupported"
    assert result.json()["message"]


def test_size_limit_precedes_full_hash(space, monkeypatch):
    document, version, _ = seed(space, b"bigger than a byte")
    as_user(space["reader"])
    monkeypatch.setattr(preview, "TEXT_MAX_BYTES", 1)
    monkeypatch.setattr(preview.object_store, "hash_file", lambda _: pytest.fail("oversized preview was hashed"))
    result = client.get(route(space, document, version))
    assert result.status_code == 200 and result.json()["kind"] == "unsupported"


def test_docx_paragraph_table_and_no_external_loading(space):
    data = word('<w:p><w:r><w:t>检修</w:t><w:tab/><w:t>方案</w:t></w:r></w:p>'
                '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>设备</w:t></w:r></w:p></w:tc>'
                '<w:tc><w:p><w:r><w:t>110kV</w:t></w:r></w:p></w:tc></w:tr></w:tbl>')
    document, version, _ = seed(space, data, "方案.docx")
    as_user(space["reader"])
    result = client.get(route(space, document, version))
    assert result.status_code == 200, result.text
    assert result.json()["blocks"] == [{"type": "paragraph", "text": "检修\t方案"},
                                       {"type": "table", "rows": [["设备", "110kV"]]}]
    assert result.json()["kind"] == "document"


def test_docx_truncation_is_explicit(space, monkeypatch):
    monkeypatch.setattr(preview, "MAX_BLOCKS", 1)
    data = word('<w:p><w:r><w:t>first</w:t></w:r></w:p><w:p><w:r><w:t>last</w:t></w:r></w:p>')
    document, version, _ = seed(space, data, "test.docx")
    as_user(space["reader"])
    result = client.get(route(space, document, version)).json()
    assert result["truncated"] is True and len(result["blocks"]) == 1


def test_xlsx_values_shared_inline_and_cached_formula(space):
    data = workbook('<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="inlineStr"><is><t>名称</t></is></c>'
                    '<c r="C1"><f>SUM(1,2)</f><v>3</v></c><c r="D1"><f>2+2</f></c>'
                    '<c r="E1" t="b"><v>1</v></c></row>',
                    {"xl/sharedStrings.xml": f'<sst xmlns="{S}"><si><t>主变</t></si></sst>'})
    document, version, _ = seed(space, data, "台账.xlsx")
    as_user(space["reader"])
    result = client.get(route(space, document, version))
    assert result.status_code == 200, result.text
    assert result.json()["sheets"] == [{"name": "设备台账", "rows": [["主变", "名称", "3", "=2+2", "TRUE"]], "truncated": False}]


def test_sparse_sheet_boundaries_never_allocate_huge_grid(space):
    data = workbook('<row r="1"><c r="ZZZ1"><v>1</v></c></row><row r="1048576"><c r="A1048576"><v>2</v></c></row>')
    document, version, _ = seed(space, data, "large.xlsx")
    as_user(space["reader"])
    result = client.get(route(space, document, version)).json()
    assert result["sheets"][0]["rows"] == [[]]
    assert result["truncated"] is True


@pytest.mark.parametrize("bad", [
    b'<!DOCTYPE x [<!ENTITY secret SYSTEM "file:///etc/passwd">]><x>&secret;</x>',
    '<!DOCTYPE x [<!ENTITY secret "x">]><x>&secret;</x>'.encode("utf-16"),
    b'<document><broken>',
])
def test_office_xml_attack_and_damage_are_rejected(space, bad):
    document, version, _ = seed(space, package({"word/document.xml": bad}), "bad.docx")
    as_user(space["reader"])
    result = client.get(route(space, document, version))
    assert result.status_code == 422
    assert "/etc/passwd" not in result.text and "Traceback" not in result.text


@pytest.mark.parametrize("entry", ["../outside.xml", "/outside.xml", "word\\outside.xml"])
def test_zip_unsafe_paths_are_rejected(space, entry):
    document, version, _ = seed(space, package({entry: "bad", "word/document.xml": "bad"}), "bad.docx")
    as_user(space["reader"])
    assert client.get(route(space, document, version)).status_code == 422


def test_zip_duplicate_members_are_rejected(space):
    content = BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        archive.writestr("word/document.xml", "first")
        with pytest.warns(UserWarning):
            archive.writestr("word/document.xml", "second")
    document, version, _ = seed(space, content.getvalue(), "bad.docx")
    as_user(space["reader"])
    assert client.get(route(space, document, version)).status_code == 422


@pytest.mark.parametrize("target,mode", [("../../outside.xml", ""), ("https://example.invalid/data", 'TargetMode="External"')])
def test_xlsx_external_and_traversal_references_are_never_loaded(space, target, mode):
    document, version, _ = seed(space, workbook("", target=target, target_mode=mode), "bad.xlsx")
    as_user(space["reader"])
    assert client.get(route(space, document, version)).status_code == 422


def test_xml_member_limit_is_friendly(space, monkeypatch):
    monkeypatch.setattr(preview, "XML_MAX_BYTES", 10)
    document, version, _ = seed(space, word(""), "large.docx")
    as_user(space["reader"])
    result = client.get(route(space, document, version))
    assert result.status_code == 200 and result.json()["kind"] == "unsupported"


def test_zip_bomb_ratio_is_rejected(space):
    document, version, _ = seed(space, package({"word/document.xml": b"x" * (2 * 1024 * 1024)}), "bomb.docx")
    as_user(space["reader"])
    assert client.get(route(space, document, version)).status_code == 422


def test_binary_size_limit_has_download_fallback(space, monkeypatch):
    monkeypatch.setattr(preview, "PDF_MAX_BYTES", 5)
    document, version, _ = seed(space, b"%PDF-1.7\npreview", "large.pdf")
    as_user(space["reader"])
    assert client.get(route(space, document, version)).json()["kind"] == "unsupported"
    assert client.get(route(space, document, version) + "/content").status_code == 413


@pytest.mark.parametrize("kind", ["png", "gif", "bmp", "webp", "jpg"])
def test_pixel_budget_rejects_large_decoding_allocation(space, kind):
    document, version, _ = seed(space, image_header(kind, 10000, 10000), "huge." + kind)
    as_user(space["reader"])
    assert client.get(route(space, document, version)).json()["kind"] == "unsupported"
    assert client.get(route(space, document, version) + "/content").status_code == 413


def test_shared_string_expansion_is_bounded_during_construction(space, monkeypatch):
    monkeypatch.setattr(preview, "MAX_OUTPUT_CHARS", 30)
    monkeypatch.setattr(preview, "MAX_CELL_CHARS", 8)
    cells = "".join(f'<c r="{chr(65 + i)}1" t="s"><v>0</v></c>' for i in range(20))
    data = workbook('<row r="1">' + cells + '</row>',
                    {"xl/sharedStrings.xml": f'<sst xmlns="{S}"><si><t>{"a" * 50000}</t></si></sst>'})
    document, version, _ = seed(space, data, "expansion.xlsx")
    as_user(space["reader"])
    result = client.get(route(space, document, version)).json()
    values = result["sheets"][0]["rows"][0]
    assert sum(map(len, values)) == 30 and max(map(len, values)) <= 8
    assert result["truncated"] is True


def test_word_and_text_total_character_budget(space, monkeypatch):
    monkeypatch.setattr(preview, "MAX_OUTPUT_CHARS", 10)
    as_user(space["reader"])
    document, version, _ = seed(space, word('<w:p><w:r><w:t>12345678</w:t></w:r></w:p>'
                                          '<w:p><w:r><w:t>abcdefgh</w:t></w:r></w:p>'), "budget.docx")
    result = client.get(route(space, document, version)).json()
    assert sum(len(block["text"]) for block in result["blocks"]) == 10 and result["truncated"]
    document, version, _ = seed(space, b"12345678901")
    result = client.get(route(space, document, version)).json()
    assert len(result["text"]) == 10 and result["truncated"]


def test_encrypted_zip_is_rejected_before_content_read(space):
    content = bytearray(word(""))
    central = content.index(b"PK\x01\x02")
    flags = int.from_bytes(content[central + 8:central + 10], "little") | 1
    content[central + 8:central + 10] = flags.to_bytes(2, "little")
    document, version, _ = seed(space, bytes(content), "encrypted.docx")
    as_user(space["reader"])
    assert client.get(route(space, document, version)).status_code == 422


def test_symlink_zip_entry_is_rejected(space):
    content = BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        entry = zipfile.ZipInfo("word/document.xml")
        entry.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(entry, "/private/etc/passwd")
    document, version, _ = seed(space, content.getvalue(), "linked.docx")
    as_user(space["reader"])
    assert client.get(route(space, document, version)).status_code == 422


def test_corrupt_deflate_is_fixed_422(space):
    content = bytearray(word(""))
    filename_size, extra_size = struct.unpack_from("<HH", content, 26)
    body_offset = 30 + filename_size + extra_size
    content[body_offset:body_offset + 5] = b"\xff" * 5
    document, version, _ = seed(space, bytes(content), "damaged.docx")
    as_user(space["reader"])
    response = client.get(route(space, document, version))
    assert response.status_code == 422 and "Traceback" not in response.text
