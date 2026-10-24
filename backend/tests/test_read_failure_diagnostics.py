"""读取失败只产生受控诊断，不能伪装成已经阅读后的方案提交问题。"""
import asyncio
from io import BytesIO
import json

from PIL import Image
import pytest

from backend.tests.test_partner_tools import library, saved  # noqa: F401
from backend.tests.test_hermes_partner import _scripted_worker
from backend.tests.test_hermes_preorganize import arguments


def image_document(library):
    output = BytesIO()
    Image.new('RGB', (8, 8), 'red').save(output, 'JPEG')
    return saved(library, '合成现场.jpeg', output.getvalue())


def execute(library, tmp_path, monkeypatch, document, meta, actions):
    from backend.app.core.ai_client import AIError
    events = []
    agent = _scripted_worker(tmp_path, monkeypatch, actions)
    args = arguments(library, document, meta)
    args['emit'] = lambda *item: events.append(item)
    with pytest.raises(AIError) as caught:
        asyncio.run(agent.run_agent(**args))
    report = [value for kind, value in events if kind == 'structured_diagnostic'][-1]
    return caught.value, report, events


@pytest.mark.parametrize(('read_error', 'code', 'message'), [
    ('无法读取原页：原页图片超过 4 MiB 输出上限，请选择更小的资料。',
     'IMAGE_OUTPUT_TOO_LARGE', '图片转换后的数据超出读取上限'),
    ('无法读取原页：原页图片经有限编码与缩放后仍超过 4 MiB 输出上限，请裁剪所需区域后重试。',
     'IMAGE_OUTPUT_TOO_LARGE', '图片转换后的数据超出读取上限'),
    ('无法读取原页：图片内容无法解码，格式或编码可能不受支持，也可能资料已损坏。',
     'IMAGE_RENDER_FAILED', '图片解码或原页转换未完成'),
    ('SECRET /private/real-library/image.jpg', 'READ_FAILED', '未能读取资料内容'),
])
def test_failed_image_read_has_precise_safe_terminal_diagnostic(
        library, tmp_path, monkeypatch, read_error, code, message):
    from backend.app.services.partner_tools import PartnerTools
    document, meta = image_document(library)
    def fail(*_args, **_kwargs):
        raise ValueError(read_error)
    monkeypatch.setattr(PartnerTools, 'read_visual', fail)
    error, report, events = execute(library, tmp_path, monkeypatch, document, meta, [
        {'type': 'tool_call', 'id': 'read-1', 'name': 'read_visual',
         'arguments': {'document_id': document}},
        {'type': 'structured_recovery', 'terminal_tool': 'propose_organization'},
        {'type': 'completed', 'answer': 'SECRET model analysis'},
    ])
    assert error.code == 'AI_ORGANIZATION_INCOMPLETE'
    assert message in str(error)
    assert '补交' not in str(error) and '未提交' not in str(error)
    assert report['read_sources'] == 0 and report['read_failures'] == 1
    assert report['last_read_error'] == {'tool': 'read_visual', 'code': code}
    assert report['recovery_attempts'] == 1
    assert 'SECRET' not in json.dumps(report) and str(library) not in json.dumps(report)
    status = [value for kind, value in events if kind == 'status'][-1]
    assert '已保留阅读结果' not in status


def test_successful_read_after_failure_is_not_reported_as_unread(library, tmp_path, monkeypatch):
    from backend.app.services.partner_tools import PartnerTools
    document, meta = saved(library, '合成规程.txt', '培训周期两周。'.encode())
    def fail(*_args, **_kwargs):
        raise ValueError('无法读取原页：原页图片超过 4 MiB 输出上限，请选择更小的资料。')
    monkeypatch.setattr(PartnerTools, 'read_visual', fail)
    error, report, _events = execute(library, tmp_path, monkeypatch, document, meta, [
        {'type': 'tool_call', 'id': 'failed-read', 'name': 'read_visual',
         'arguments': {'document_id': document}},
        {'type': 'tool_call', 'id': 'good-read', 'name': 'read_document',
         'arguments': {'document_id': document}},
        {'type': 'completed', 'answer': 'SECRET model analysis'},
    ])
    assert report['read_sources'] == 1 and report['read_failures'] == 1
    assert report['last_read_error']['code'] == 'IMAGE_OUTPUT_TOO_LARGE'
    assert '未提交整理方案' in str(error)
    assert '超出读取上限' not in str(error)


@pytest.mark.parametrize('tool', ['read_document', 'read_table', 'read_visual',
    'read_embedded_images', 'document_overview', 'list_folder_members',
    'read_folder_member', 'list_archive_members', 'read_archive_member'])
def test_only_top_level_read_failure_is_classified(tool):
    from backend.app.services.structured_diagnostics import read_failure
    assert read_failure(tool, {'result': {'ok': False, 'error': 'SECRET'}}) == {
        'tool': tool, 'code': 'READ_FAILED'}
    assert read_failure(tool, {'result': {'error': 'SECRET'}})['code'] == 'READ_FAILED'
    assert read_failure(tool, {'result': {'ok': True, 'members': [{'error': 'SECRET'}]}}) is None
    assert read_failure(tool, {'result': {'ok': True}}) is None


@pytest.mark.parametrize('tool', ['SECRET', '/private/user-library', '../read_visual',
                                'propose_organization', 'read_object_skill', [], {}])
def test_unknown_tools_are_not_copied_to_read_diagnostics(tool):
    from backend.app.services.structured_diagnostics import read_failure
    assert read_failure(tool, {'result': {'ok': False, 'error': 'SECRET'}}) is None


def test_raw_error_and_supplied_code_never_enter_diagnostic():
    from backend.app.services.structured_diagnostics import read_failure
    result = read_failure('read_visual', {'result': {'ok': False,
        'code': 'IMAGE_OUTPUT_TOO_LARGE', 'error': 'SECRET /private/photo.jpg',
        'arguments': {'path': 'SECRET'}}})
    assert result == {'tool': 'read_visual', 'code': 'READ_FAILED'}


def test_card_failure_uses_the_same_reading_diagnostic(library, tmp_path, monkeypatch):
    from backend.app.core.ai_client import AIError
    from backend.app.services.partner_tools import PartnerTools
    document, meta = image_document(library)
    def fail(*_args, **_kwargs):
        raise ValueError('图片内容无法解码，格式或编码可能不受支持，也可能资料已损坏。')
    monkeypatch.setattr(PartnerTools, 'read_visual', fail)
    agent = _scripted_worker(tmp_path, monkeypatch, [
        {'type': 'tool_call', 'id': 'read-card', 'name': 'read_visual',
         'arguments': {'document_id': document}},
        {'type': 'completed', 'answer': 'SECRET'},
    ])
    events = []
    args = arguments(library, document, meta)
    args.update(purpose='card', purpose_context={'document_id': document},
                emit=lambda *item: events.append(item))
    with pytest.raises(AIError) as caught:
        asyncio.run(agent.run_agent(**args))
    assert caught.value.code == 'AI_CARD_INCOMPLETE'
    assert '图片解码或原页转换未完成' in str(caught.value)
    assert '生成资料卡' in str(caught.value)
    report = [value for kind, value in events if kind == 'structured_diagnostic'][-1]
    assert report['last_read_error']['code'] == 'IMAGE_RENDER_FAILED'
