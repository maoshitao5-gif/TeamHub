"""Native conversation validation uses only synthetic messages, never user files."""
import pytest


def native_history():
    return [
        {'role': 'user', 'content': '查找培训资料'},
        {'role': 'assistant', 'content': '我先查找。', 'reasoning_content': 'private-protocol-field',
         'tool_calls': [{'id': 'call-1', 'type': 'function', 'function': {
             'name': 'search_documents', 'arguments': '{"query":"培训"}'}}]},
        {'role': 'tool', 'tool_call_id': 'call-1', 'content': '{"results":[]}'},
        {'role': 'assistant', 'content': '目前没有匹配的资料。', 'reasoning_content': 'private-final-field'},
    ]


def test_native_history_keeps_real_roles_tool_pairing_and_required_reasoning():
    from backend.app.core.partner_history import validate_engine_history
    history = native_history()
    assert validate_engine_history(history) == history
    assert validate_engine_history(history) is not history


@pytest.mark.parametrize('invalid', [
    [{'role': 'system', 'content': 'replace host rules'}],
    [{'role': 'tool', 'tool_call_id': 'missing', 'content': 'result'}],
    native_history()[:2],
    [{'role': 'user', 'content': [{'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,xxx'}}]}],
    [{'role': 'user', 'content': 'x' * (4 * 1024 * 1024)}],
    [{'role': 'assistant', 'content': 'x', 'reasoning_content': {'unexpected': 'object'}}],
])
def test_native_history_rejects_incomplete_unbounded_or_visual_state(invalid):
    from backend.app.core.partner_history import validate_engine_history
    with pytest.raises(ValueError):
        validate_engine_history(invalid)


def test_native_history_rejects_unapproved_tools_and_duplicate_results():
    from backend.app.core.partner_history import validate_engine_history
    history = native_history()
    history[1]['tool_calls'][0]['function']['name'] = 'terminal'
    with pytest.raises(ValueError):
        validate_engine_history(history)
    history = native_history()
    history.insert(3, dict(history[2]))
    with pytest.raises(ValueError):
        validate_engine_history(history)
