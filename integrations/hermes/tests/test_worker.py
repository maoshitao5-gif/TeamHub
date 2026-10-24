"""独立 worker 的通信边界；不使用真实模型、用户资料或凭据。"""
import base64
import importlib.util
import io
import json
from pathlib import Path

import pytest


def worker():
    path = Path(__file__).parents[1] / 'worker.py'
    spec = importlib.util.spec_from_file_location('teamhub_hermes_worker', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def frames(*items):
    return io.StringIO(''.join(json.dumps({'v': 1, **item}) + '\n' for item in items))


def test_transport_relays_actual_response_chunks_without_network():
    module = worker()
    output = io.StringIO()
    channel = module.PipeChannel(frames(
        {'type': 'model_start', 'id': 'rpc-1', 'status': 200, 'headers': {'content-type': 'text/event-stream'}},
        {'type': 'model_chunk', 'id': 'rpc-1', 'data': base64.b64encode(b'data: one\n\n').decode()},
        {'type': 'model_end', 'id': 'rpc-1'},
    ), output)
    transport = module.PipeTransport(channel)
    import httpx
    with httpx.Client(transport=transport) as client:
        with client.stream('POST', 'https://teamhub.invalid/v1/chat/completions', json={'messages': []}) as response:
            assert list(response.iter_raw()) == [b'data: one\n\n']
    event = json.loads(output.getvalue())
    assert event['type'] == 'model_request'
    assert event['body'] == {'messages': []}


@pytest.mark.parametrize('reply', [
    {'type': 'model_start', 'id': 'wrong', 'status': 200, 'headers': {}},
    {'type': 'model_chunk', 'id': 'rpc-1', 'data': 'evil'},
    {'type': 'model_start', 'id': 'rpc-1', 'status': True, 'headers': {}},
])
def test_model_protocol_does_not_accept_wrong_request_or_invalid_start(reply):
    module = worker()
    channel = module.PipeChannel(frames(reply), io.StringIO())
    import httpx
    with pytest.raises(module.ProtocolError):
        module.PipeTransport(channel).handle_request(httpx.Request('POST', 'https://teamhub.invalid/v1/chat/completions', json={}))


def test_tool_bridge_keeps_source_metadata_and_real_image_bytes():
    module = worker()
    output = io.StringIO()
    channel = module.PipeChannel(frames({'type': 'tool_result', 'id': 'rpc-1',
        'result': {'source_id': 'S1'}, 'images': [{'media_type': 'image/png', 'data': 'aW1hZ2U='}]}), output)
    result = module.call_tool(channel, 'read_visual', {'document_id': 'doc'})
    assert json.loads(result['content'][0]['text']) == {'source_id': 'S1'}
    assert result['content'][1] == {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,aW1hZ2U='}}
    assert json.loads(output.getvalue())['arguments'] == {'document_id': 'doc'}


def test_sdk_early_stream_close_drains_protocol_end_before_next_tool():
    module = worker()
    channel = module.PipeChannel(frames(
        {'type': 'model_start', 'id': 'rpc-1', 'status': 200, 'headers': {}},
        {'type': 'model_chunk', 'id': 'rpc-1', 'data': base64.b64encode(b'data: [DONE]\n\n').decode()},
        {'type': 'model_end', 'id': 'rpc-1'},
        {'type': 'tool_result', 'id': 'rpc-2', 'result': {'source_id': 'S1'}},
    ), io.StringIO())
    import httpx
    response = module.PipeTransport(channel).handle_request(
        httpx.Request('POST', 'https://teamhub.invalid/v1/chat/completions', json={}))
    iterator = iter(response.stream)
    assert next(iterator) == b'data: [DONE]\n\n'
    response.close()
    assert json.loads(module.call_tool(channel, 'read_document', {})) == {'source_id': 'S1'}


def test_start_allows_only_teamhub_tools_and_plain_authorized_history():
    module = worker()
    start = {'v': 1, 'type': 'start', 'instructions': 'rules', 'question': '{}', 'history': [],
             'tools': [{'type': 'function', 'function': {'name': 'terminal', 'parameters': {'type': 'object'}}}],
             'model': 'deepseek-chat', 'max_iterations': 4, 'max_tokens': 4000, 'thinking_mode': 'enabled'}
    with pytest.raises(module.ProtocolError):
        module.validate_start(start)


def test_protocol_rejects_eof_and_non_object_frames():
    module = worker()
    for raw in ('', '[]\n', '{"v":2,"type":"start"}\n', '{"v":1,"type":"start","value":NaN}\n'):
        with pytest.raises(module.ProtocolError):
            module.PipeChannel(io.StringIO(raw), io.StringIO()).receive()


def test_native_history_export_removes_upstream_metadata_but_preserves_protocol():
    module = worker()
    messages = [{'role': 'system', 'content': 'host rules'},
        {'role': 'user', 'content': 'read'},
        {'role': 'assistant', 'content': '', 'reasoning_content': 'original reasoning',
         'tool_calls': [{'id': 'call-1', 'call_id': 'internal', 'response_item_id': 'fc_internal',
                        'type': 'function', 'function': {'name': 'read_document', 'arguments': '{}'}}]},
        {'role': 'tool', 'tool_call_id': 'call-1', 'name': 'read_document', 'content': 'result'}]
    exported = module._export_history(messages)
    assert exported[1]['reasoning_content'] == 'original reasoning'
    assert set(exported[1]['tool_calls'][0]) == {'id', 'type', 'function'}
    assert all(item['role'] != 'system' for item in exported)
    assert 'call_id' in messages[2]['tool_calls'][0]
    assert module._export_history([{'role': 'tool', 'content': [{'type': 'image_url'}]}]) == []


@pytest.mark.parametrize('result, expected', [
    ({'error': 'Model output entered a repetition loop and the stream was cut mid-loop; discarding the degenerate response.'}, 'HERMES_REPETITION_STOPPED'),
    ({'compression_exhausted': True, 'error': 'private document text'}, 'HERMES_CONTEXT_LIMIT'),
    ({'interrupted': True, 'error': 'private provider response'}, 'HERMES_INTERRUPTED'),
    ({'error': 'private model content and token'}, 'HERMES_INCOMPLETE'),
    ({'error': {'nested': 'private object'}}, 'HERMES_INCOMPLETE'),
    (None, 'HERMES_INCOMPLETE'),
])
def test_incomplete_result_is_mapped_without_echoing_raw_upstream_content(result, expected):
    assert worker()._incomplete_code(result) == expected
