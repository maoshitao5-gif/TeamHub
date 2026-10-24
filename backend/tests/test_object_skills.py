"""Object families route safely without claiming parser/model capabilities."""
import pytest

from backend.app.services import object_skills


@pytest.mark.parametrize(('filename', 'expected'), [
    ('规程.TXT', 'text'), ('记录.csv', 'spreadsheet'), ('记录.tsv', 'spreadsheet'),
    ('配置.yaml', 'text'), ('网页.html', 'text'), ('网页.htm', 'text'),
    ('结构.xml', 'text'), ('结构.json', 'text'),
    ('目录/说明.DOCM', 'word'), ('template.dotx', 'word'), ('资料.odt', 'word'),
    ('legacy.doc', 'word'), ('审批.rtf', 'word'),
    ('台账.XLSM', 'spreadsheet'), ('模板.xltm', 'spreadsheet'), ('数据.ods', 'spreadsheet'),
    ('汇报.PPSX', 'presentation'), ('模板.potm', 'presentation'), ('课件.odp', 'presentation'),
    ('图纸.PDF', 'pdf'), ('扫描.jpeg', 'image'), ('照片.HEIC', 'image'),
    ('照片.heif', 'image'), ('矢量.svg', 'image'), ('图片.avif', 'image'),
    ('资料.ZIP', 'archive'), ('目录/报告.docx.zip', 'archive'), ('Office.docx', 'word'),
    ('资料.tar.gz', 'archive'), ('资料.TAR.BZ2', 'archive'), ('资料.tar.xz', 'archive'),
    ('资料.tgz', 'archive'), ('资料.tbz2', 'archive'), ('资料.txz', 'archive'),
    ('资料.tar', 'archive'), ('资料.7z', 'archive'), ('资料.rar', 'archive'),
    ('资料.gz', 'archive'), ('资料.bz2', 'archive'), ('资料.xz', 'archive'),
    ('C:\\资料\\报告.docx', 'word'), ('raw.bin', 'unknown'), ('README', 'unknown'),
    ('file.docx.exe', 'unknown'), ('report.zip.backup', 'unknown'),
    ('', 'unknown'), ('bad\x00.pdf', 'unknown'), (None, 'unknown'),
])
def test_saved_filename_routing_handles_aliases_and_compound_archives(filename, expected):
    assert object_skills.object_kind(filename) == expected


def test_folder_identity_wins_over_its_name_extension():
    assert object_skills.object_kind('项目.zip', is_folder=True) == 'folder'


def test_all_decoder_image_families_share_the_same_skill_routing():
    from backend.app.services.image_formats import IMAGE_SUFFIXES
    for suffix in IMAGE_SUFFIXES:
        assert object_skills.object_kind('保存版本' + suffix.upper()) == 'image'


def test_catalog_discovers_families_without_reading_their_files(tmp_path, monkeypatch):
    monkeypatch.setattr(object_skills, 'SKILL_ROOT', tmp_path / 'not-installed')
    catalog = object_skills.object_skill_catalog()
    assert {item['kind'] for item in catalog} == {
        'text', 'word', 'spreadsheet', 'presentation', 'pdf', 'image', 'folder', 'archive'}
    assert all(item['description'] for item in catalog)


def test_only_the_selected_bundled_skill_is_required(tmp_path, monkeypatch):
    target = tmp_path / 'text-understanding' / 'SKILL.md'
    target.parent.mkdir()
    target.write_text('selected-only-resource', encoding='utf-8')
    monkeypatch.setattr(object_skills, 'SKILL_ROOT', tmp_path)
    assert object_skills.object_instructions('text') == 'selected-only-resource'
    with pytest.raises(ValueError):
        object_skills.object_instructions('image')


@pytest.mark.parametrize('kind', ['../text-understanding', '/etc/passwd',
                                   'file:///etc/passwd', 'https://example.test/rules',
                                   'text/../image', '', None, ['text']])
def test_loader_does_not_accept_paths_or_unlisted_names(kind):
    with pytest.raises(ValueError):
        object_skills.object_instructions(kind)


def test_unknown_type_can_explain_the_gap_without_reading_resources(tmp_path, monkeypatch):
    monkeypatch.setattr(object_skills, 'SKILL_ROOT', tmp_path / 'not-installed')
    assert isinstance(object_skills.object_instructions('unknown'), str)


@pytest.mark.parametrize('link_level', ['root', 'directory', 'file'])
def test_resource_symlinks_cannot_import_outside_instructions(tmp_path, monkeypatch, link_level):
    root, outside = tmp_path / 'skills', tmp_path / 'outside'
    root.mkdir()
    (outside / 'text-understanding').mkdir(parents=True)
    external = outside / 'text-understanding' / 'SKILL.md'
    external.write_text('outside instruction', encoding='utf-8')
    if link_level == 'root':
        linked_root = tmp_path / 'linked-skills'
        linked_root.symlink_to(outside, target_is_directory=True)
        root = linked_root
    elif link_level == 'directory':
        (root / 'text-understanding').symlink_to(external.parent, target_is_directory=True)
    else:
        (root / 'text-understanding').mkdir()
        (root / 'text-understanding' / 'SKILL.md').symlink_to(external)
    monkeypatch.setattr(object_skills, 'SKILL_ROOT', root)
    with pytest.raises(ValueError):
        object_skills.object_instructions('text')


@pytest.mark.parametrize('content', [b'', b' \n ', b'\xff\xfeinvalid', b'x' * 16001])
def test_missing_empty_undecodable_or_oversized_rules_fail_closed(tmp_path, monkeypatch, content):
    target = tmp_path / 'text-understanding' / 'SKILL.md'
    target.parent.mkdir()
    target.write_bytes(content)
    monkeypatch.setattr(object_skills, 'SKILL_ROOT', tmp_path)
    with pytest.raises(ValueError):
        object_skills.object_instructions('text')


def test_each_catalog_entry_has_an_independently_loadable_bundled_skill():
    for item in object_skills.object_skill_catalog():
        content = object_skills.object_instructions(item['kind'])
        assert content.startswith('---\n')
        assert 200 < len(content) <= object_skills.MAX_SKILL_CHARS
