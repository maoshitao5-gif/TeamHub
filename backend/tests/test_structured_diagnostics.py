"""结构化任务只保留受控诊断元数据，不保留模型正文或验证输入。"""
import json

import pytest

from backend.app.services.structured_diagnostics import StructuredDiagnostics, sanitize_diagnostics


def report(**changes):
    return {'purpose': 'preorganize', 'terminal_tool': 'propose_organization',
            'outcome': 'incomplete', 'model_requests': 3, 'tool_calls': 4,
            'read_sources': 1, 'visual_pages': 1, 'submission_attempts': 2,
            'recovery_attempts': 1,
            'last_error': {'code': 'SUBMISSION_SCHEMA_INVALID',
                           'validation_errors': [{'field': 'card.facts.0.source_ids',
                                                  'code': 'missing', 'message': 'SECRET'}]},
            **changes}


def test_only_fixed_metadata_survives_and_validation_input_is_dropped():
    value = report(secret='SECRET', answer='SECRET', image_url='SECRET')
    value['last_error'].update(input='SECRET', message='SECRET')
    result = sanitize_diagnostics(value, 'preorganize')
    assert result['last_error'] == {'code': 'SUBMISSION_SCHEMA_INVALID', 'validation_errors': [
        {'field': 'card.facts.0.source_ids', 'code': 'missing'}]}
    assert result['model_requests'] == 3
    assert 'SECRET' not in json.dumps(result)


@pytest.mark.parametrize('change', [None, [], 'SECRET', {'purpose': 'card'},
    {'purpose': 'preorganize', 'terminal_tool': 'submit_document_card'},
    {'purpose': 'preorganize', 'terminal_tool': 'propose_organization', 'outcome': 'SECRET'}])
def test_untrusted_or_mismatched_envelope_is_rejected(change):
    assert sanitize_diagnostics(change, 'preorganize') == {}


def test_counts_and_error_fields_have_fixed_bounds_and_no_free_strings():
    value = report(model_requests=True, tool_calls=-2, read_sources=10**100,
                   visual_pages='SECRET', submission_attempts=1.2)
    value['last_error'] = {'code': 'SECRET', 'validation_errors': [
        {'field': 'SECRET', 'code': 'missing'},
        {'field': 'card.summary', 'code': 'SECRET'},
        {'field': 'card.facts.1000000.value', 'code': 'string_too_long'},
        *[{'field': 'card.summary', 'code': 'string_too_long', 'input': 'SECRET'}] * 10000]}
    result = sanitize_diagnostics(value, 'preorganize')
    assert result['model_requests'] == result['tool_calls'] == result['visual_pages'] == 0
    assert result['read_sources'] == 1000000
    assert result['submission_attempts'] == 0
    assert result['last_error']['code'] == 'SUBMISSION_INVALID'
    assert len(result['last_error']['validation_errors']) <= 8
    assert 'SECRET' not in json.dumps(result)
    assert len(json.dumps(result)) < 2500


def test_capture_ignores_normal_events_and_keeps_latest_valid_metadata():
    capture = StructuredDiagnostics('preorganize')
    capture('answer', {'text': 'SECRET'})
    capture('status', report())
    assert capture.value == {}
    capture('structured_diagnostic', report(outcome='recovery'))
    capture('structured_diagnostic', {'answer': 'SECRET'})
    assert capture.value['outcome'] == 'recovery'
    capture.accept_output({'diagnostics': report(outcome='submitted')})
    assert capture.value['outcome'] == 'submitted'


def test_read_failure_fields_are_bounded_and_old_diagnostics_remain_compatible():
    result = sanitize_diagnostics(report(read_failures=10**100,
        last_read_error={'tool': 'read_visual', 'code': 'IMAGE_OUTPUT_TOO_LARGE',
                         'error': 'SECRET', 'arguments': {'path': 'SECRET'}}), 'preorganize')
    assert result['read_failures'] == 1000000
    assert result['last_read_error'] == {'tool': 'read_visual', 'code': 'IMAGE_OUTPUT_TOO_LARGE'}
    assert 'SECRET' not in json.dumps(result)
    assert sanitize_diagnostics(report(), 'preorganize')['read_failures'] == 0


@pytest.mark.parametrize('last_read_error', [None, [], 'SECRET',
    {'tool': [], 'code': {}}, {'tool': 'SECRET', 'code': 'READ_FAILED'},
    {'tool': 'read_visual', 'code': 'SECRET'},
    {'tool': '/private/user', 'code': 'IMAGE_RENDER_FAILED'}])
def test_read_failure_strings_are_strict_enumerations(last_read_error):
    result = sanitize_diagnostics(report(read_failures=True, last_read_error=last_read_error), 'preorganize')
    assert result['read_failures'] == 0
    assert 'last_read_error' not in result
    assert 'SECRET' not in json.dumps(result)


@pytest.mark.parametrize(('sources', 'code'), [(0, 'IMAGE_RENDER_FAILED'),
                                             (1, 'SUBMISSION_SCHEMA_INVALID')])
def test_logs_read_code_only_when_no_content_was_obtained(sources, code):
    from backend.app.services.structured_diagnostics import log_diagnostics
    class Logger:
        def warning(self, _format, value):
            self.value = value
    logger = Logger()
    log_diagnostics(logger, 'preorganize', 'failed', report(read_sources=sources,
        read_failures=1, last_read_error={'tool': 'read_visual', 'code': 'IMAGE_RENDER_FAILED',
                                        'error': 'SECRET', 'path': '/private/SECRET'}))
    value = json.loads(logger.value)
    assert value['diagnostic_code'] == code
    assert 'SECRET' not in logger.value


@pytest.mark.parametrize('error', [{'code': []}, {'code': {}},
    {'code': 'SUBMISSION_RULE_INVALID', 'validation_errors': [{'field': [], 'code': {}}]},
    {'code': 'SECRET' * 10000, 'validation_errors': 'SECRET' * 10000}])
def test_malformed_nested_metadata_never_raises_or_copies_arbitrary_values(error):
    result = sanitize_diagnostics(report(last_error=error), 'preorganize')
    assert result['last_error']['code'] in {'SUBMISSION_RULE_INVALID', 'SUBMISSION_INVALID'}
    assert result['last_error']['validation_errors'] == []
    assert 'SECRET' not in json.dumps(result)
