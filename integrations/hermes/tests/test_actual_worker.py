"""真实 Hermes 源码/SDK 的进程测试，上游响应由本机内存生成。"""
import base64
import json
import os
from pathlib import Path
import select
import subprocess
import time

import pytest

ROOT = Path(__file__).resolve().parents[3]
PYTHON = ROOT / '.mvp-sandbox/hermes-venv/bin/python'


def receive(process, deadline):
    remaining = deadline - time.monotonic()
    assert remaining > 0 and select.select([process.stdout], [], [], remaining)[0], 'worker timeout'
    raw = process.stdout.readline()
    assert raw, f'worker exited ({process.poll()})'
    return json.loads(raw)


def send(process, frame):
    process.stdin.write(json.dumps({'v': 1, **frame}, ensure_ascii=False) + '\n')
    process.stdin.flush()


def model_reply(process, request_id, message):
    send(process, {'type': 'model_start', 'id': request_id, 'status': 200,
                   'headers': {'content-type': 'text/event-stream'}})
    delta = {'role': 'assistant', **message}
    body = {'id': 'synthetic', 'object': 'chat.completion.chunk', 'created': 0,
            'model': 'deepseek-chat', 'choices': [{'index': 0, 'delta': delta,
            'finish_reason': 'tool_calls' if message.get('tool_calls') else 'stop'}],
            'usage': {'prompt_tokens': 20, 'completion_tokens': 10, 'total_tokens': 30}}
    raw = ('data: ' + json.dumps(body) + '\n\ndata: [DONE]\n\n').encode()
    send(process, {'type': 'model_chunk', 'id': request_id, 'data': base64.b64encode(raw).decode()})
    send(process, {'type': 'model_end', 'id': request_id})


@pytest.mark.skipif(not PYTHON.exists(), reason='run scripts/prepare-hermes.py first')
def test_actual_hermes_model_tool_model_loop_preserves_evidence(tmp_path):
    process = subprocess.Popen([str(PYTHON), '-I', str(ROOT / 'integrations/hermes/worker.py')],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        encoding='utf-8', cwd=tmp_path,
        env={'PATH': os.defpath, 'HERMES_HOME': str(tmp_path / 'profile')})
    deadline = time.monotonic() + 60
    try:
        ready = receive(process, deadline)
        assert ready == {'v': 1, 'type': 'ready', 'engine': 'hermes', 'version': 'v0.22.2', 'protocol': 1, 'adapter_revision': 4}
        send(process, {'type': 'start', 'instructions': 'Only use authorized tools. Answer in Chinese.',
            'question': 'Read doc and report its title with source.', 'history': [
                {'role': 'user', 'content': '用户原始目标'},
                {'role': 'assistant', 'content': '此前回答，仅有最终文本，没有原始推理'}], 'model': 'deepseek-chat',
            'max_iterations': 4, 'max_tokens': 4000, 'thinking_mode': 'enabled',
            'tools': [{'type': 'function', 'function': {'name': 'read_document', 'description': 'Read saved text',
                'parameters': {'type': 'object', 'properties': {'document_id': {'type': 'string'}},
                               'required': ['document_id']}}}]})
        first = receive(process, deadline)
        assert first['type'] == 'model_request', first
        assert [tool['function']['name'] for tool in first['body']['tools']] == ['read_document']
        assert first['body']['stream'] is True
        assert not any(message['role'] == 'assistant' for message in first['body']['messages'])
        assert any('此前回答' in str(message.get('content')) for message in first['body']['messages'])
        assert all('User home directory:' not in str(message.get('content'))
                   and 'Active Hermes profile:' not in str(message.get('content'))
                   for message in first['body']['messages'])
        model_reply(process, first['id'], {'content': '我先读取资料。', 'tool_calls': [{'index': 0, 'id': 'call-real-hermes',
            'type': 'function', 'function': {'name': 'read_document', 'arguments': '{"document_id":"doc"}'}}],
            'reasoning_content': 'synthetic-private-native-reasoning'})
        while True:
            event = receive(process, deadline)
            if event['type'] == 'tool_call':
                break
            assert event['type'] == 'answer_preview', event
        assert event['name'] == 'read_document'
        send(process, {'type': 'tool_result', 'id': event['id'], 'result': {'units': [
            {'source_id': 'S1', 'text': '真实测试资料标题：验收规范'}]}})
        second = receive(process, deadline)
        assert second['type'] == 'model_request', second
        assert any('验收规范' in str(message.get('content')) for message in second['body']['messages']), second['body']['messages']
        assert any(message.get('reasoning_content') == 'synthetic-private-native-reasoning'
                   for message in second['body']['messages'])
        model_reply(process, second['id'], {'content': '资料标题为验收规范。[S1]'})
        events = []
        while True:
            event = receive(process, deadline)
            events.append(event)
            if event['type'] in {'completed', 'error'}:
                break
        assert events[-1]['type'] == 'completed', events
        assert events[-1]['answer'] == '资料标题为验收规范。[S1]'
        assert any(item.get('reasoning_content') == 'synthetic-private-native-reasoning'
                   for item in events[-1]['engine_history'])
        assert any(event['type'] == 'answer_preview' for event in events)
        assert [event['text'] for event in events if event['type'] == 'answer_preview'][-1] .endswith('资料标题为验收规范。[S1]')
        assert 'synthetic-private-native-reasoning' not in json.dumps([event for event in events if event['type'] == 'answer_preview'])
        assert process.wait(timeout=10) == 0
        assert process.stderr.read() == ''
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)


@pytest.mark.skipif(not PYTHON.exists(), reason='run scripts/prepare-hermes.py first')
def test_actual_hermes_iteration_budget_pauses_without_hidden_summary(tmp_path):
    process = subprocess.Popen([str(PYTHON), '-I', str(ROOT / 'integrations/hermes/worker.py')],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        encoding='utf-8', cwd=tmp_path,
        env={'PATH': os.defpath, 'HERMES_HOME': str(tmp_path / 'profile')})
    deadline = time.monotonic() + 30
    try:
        assert receive(process, deadline)['type'] == 'ready'
        send(process, {'type': 'start', 'instructions': 'Use tools to finish the task.',
            'question': 'Read the source.', 'history': [], 'model': 'deepseek-chat',
            'max_iterations': 1, 'max_tokens': 4000, 'thinking_mode': 'enabled',
            'tools': [{'type': 'function', 'function': {'name': 'read_document', 'description': 'Read saved text',
                'parameters': {'type': 'object', 'properties': {'document_id': {'type': 'string'}},
                               'required': ['document_id']}}}]})
        model = receive(process, deadline)
        assert model['type'] == 'model_request'
        model_reply(process, model['id'], {'tool_calls': [{'index': 0, 'id': 'read-last',
            'type': 'function', 'function': {'name': 'read_document', 'arguments': '{"document_id":"doc"}'}}]})
        tool = receive(process, deadline)
        assert tool['type'] == 'tool_call'
        send(process, {'type': 'tool_result', 'id': tool['id'], 'result': {'text': 'kept evidence'}})
        event = receive(process, deadline)
        assert event['type'] == 'paused' and event['reason'] == 'model_requests', event
        assert any(item.get('tool_call_id') == 'read-last' for item in event['engine_history'])
        assert process.wait(timeout=10) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
