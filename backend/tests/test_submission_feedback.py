"""Submission repair feedback is actionable without echoing model input."""
import json

import pytest

from backend.tests.test_partner_tools import library, saved  # noqa: F401
from backend.tests.test_organization_tools import bridge_for, invoke, proposal
from backend.tests.test_card_tools import card, card_bridge


def ready(library, *, card_only=False):
    document_id, metadata = saved(library, '培训.txt', '培训周期两周。'.encode())
    bridge = (card_bridge if card_only else bridge_for)(library, document_id, metadata)
    invoke(bridge, 'read_document', {'document_id': document_id})
    return bridge


def test_missing_card_reports_exact_required_field_and_remembers_attempt(library):
    bridge = ready(library)
    value = proposal()
    value.pop('card')
    result = invoke(bridge, 'propose_organization', value)
    assert result['code'] == 'SUBMISSION_SCHEMA_INVALID'
    assert result['validation_errors'] == [
        {'field': 'card', 'code': 'required', 'message': '缺少必填字段。'}]
    assert bridge.submission_attempts == 1
    assert bridge.last_submission_error == result
    assert bridge.proposal is None


@pytest.mark.parametrize('card_only', [False, True])
def test_nested_schema_feedback_uses_exact_known_field_path(library, card_only):
    bridge = ready(library, card_only=card_only)
    value = card(sections=[{'title': '培训', 'summary': '说明', 'source_ids': []}])
    tool = 'submit_document_card' if card_only else 'propose_organization'
    result = invoke(bridge, tool, value if card_only else proposal(card=value))
    prefix = '' if card_only else 'card.'
    assert result['code'] == 'SUBMISSION_SCHEMA_INVALID'
    assert result['validation_errors'] == [
        {'field': prefix + 'sections.0.source_ids', 'code': 'too_short',
         'message': '字段内容或项目数量不足，请按工具定义补全。'}]


@pytest.mark.parametrize('card_only', [False, True])
def test_feedback_never_echoes_inputs_extra_field_names_or_validator_context(library, card_only):
    bridge = ready(library, card_only=card_only)
    secret = 'sk-private-test-only-NOT-A-REAL-KEY'
    path = '/private/user/SENSITIVE-CONTENT'
    value = card(summary='\x00' + secret, facts=[{
        'label': '周期', 'value': '两周', 'source_ids': ['S1'], path: secret}])
    value[secret] = path
    if not card_only:
        value = proposal(name='../' + secret, card=value)
        value[path] = secret
    result = invoke(bridge, 'submit_document_card' if card_only else 'propose_organization', value)
    serialized = json.dumps(result, ensure_ascii=False)
    assert secret not in serialized and path not in serialized
    assert 'input' not in result and 'ctx' not in serialized and 'https://' not in serialized
    assert result['code'] == 'SUBMISSION_SCHEMA_INVALID'
    prefix = '' if card_only else 'card.'
    assert any(item['field'] == prefix + 'summary' for item in result['validation_errors'])
    assert any(item['field'] == prefix + 'facts.0'
               and item['code'] == 'unexpected_field' for item in result['validation_errors'])
    assert all(set(item) == {'field', 'code', 'message'} for item in result['validation_errors'])


def test_feedback_is_bounded_even_when_many_fields_are_invalid(library):
    bridge = ready(library)
    value = proposal(card=card(facts=[{'label': '', 'value': '', 'source_ids': []}] * 20))
    result = invoke(bridge, 'propose_organization', value)
    assert result['code'] == 'SUBMISSION_SCHEMA_INVALID'
    assert len(result['validation_errors']) == 8


@pytest.mark.parametrize(('changes', 'field', 'code'), [
    ({'name': '培训.pdf'}, 'name', 'SUBMISSION_RULE_INVALID'),
    ({'target_dir': '未授权分类'}, 'target_dir', 'SUBMISSION_RULE_INVALID'),
    ({'source_ids': ['S99']}, 'source_ids', 'SUBMISSION_EVIDENCE_INVALID'),
    ({'card': card(sections=[{'title': '培训', 'summary': '说明', 'source_ids': ['S99']}])},
     'card.sections.0.source_ids', 'SUBMISSION_EVIDENCE_INVALID'),
])
def test_business_validation_gives_controlled_field_and_never_weakens_rules(
        library, changes, field, code):
    bridge = ready(library)
    result = invoke(bridge, 'propose_organization', proposal(**changes))
    assert result['ok'] is False and result['code'] == code
    assert result['validation_errors'][0]['field'] == field
    assert bridge.proposal is None and bridge.card is None


@pytest.mark.parametrize('card_only', [False, True])
def test_repaired_submission_clears_error_but_retains_attempt_count(library, card_only):
    bridge = ready(library, card_only=card_only)
    name = 'submit_document_card' if card_only else 'propose_organization'
    bad = card(source_ids=['S99']) if card_only else proposal(source_ids=['S99'])
    assert invoke(bridge, name, bad)['ok'] is False
    assert bridge.last_submission_error['code'] == 'SUBMISSION_EVIDENCE_INVALID'
    good = card() if card_only else proposal()
    assert invoke(bridge, name, good)['ok'] is True
    assert bridge.submission_attempts == 2 and bridge.last_submission_error is None


def test_card_nested_evidence_error_is_relative_to_card_tool_schema(library):
    bridge = ready(library, card_only=True)
    result = invoke(bridge, 'submit_document_card', card(facts=[{
        'label': '周期', 'value': '两周', 'source_ids': ['S99']}]))
    assert result['code'] == 'SUBMISSION_EVIDENCE_INVALID'
    assert result['validation_errors'][0]['field'] == 'facts.0.source_ids'


def test_extension_feedback_disambiguates_jpeg_from_jpg_without_relaxing_matching(library):
    from io import BytesIO
    from PIL import Image
    output = BytesIO()
    Image.new('RGB', (16, 16), 'red').save(output, 'JPEG')
    document_id, metadata = saved(library, '照片.jpeg', output.getvalue())
    bridge = bridge_for(library, document_id, metadata)
    result = invoke(bridge, 'propose_organization', proposal(name='照片.jpg'))
    assert result['code'] == 'SUBMISSION_RULE_INVALID'
    assert result['validation_errors'][0]['code'] == 'extension_mismatch'
    assert '.jpeg' in result['validation_errors'][0]['message']
    assert bridge.proposal is None


def test_extension_feedback_never_echoes_unknown_sensitive_suffix(library):
    bridge = ready(library)
    bridge.original_suffix = '.sk-private-DO-NOT-ECHO'
    result = invoke(bridge, 'propose_organization', proposal(name='培训.jpg'))
    assert result['code'] == 'SUBMISSION_RULE_INVALID'
    assert 'sk-private' not in json.dumps(result, ensure_ascii=False)


def test_unknown_business_exception_is_not_echoed(library, monkeypatch):
    bridge = ready(library)
    def unavailable(_value):
        raise ValueError('/private/user/secret-key-do-not-echo')
    monkeypatch.setattr(bridge, '_validate_proposal', unavailable)
    result = invoke(bridge, 'propose_organization', proposal())
    assert result['code'] == 'SUBMISSION_RULE_INVALID'
    assert '/private/user' not in json.dumps(result)
    assert result['validation_errors'][0]['field'] == '$'
