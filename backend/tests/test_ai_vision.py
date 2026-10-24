"""A vision connection check must actually inspect a fresh synthetic image."""
import base64
from io import BytesIO
import json

import httpx
from PIL import Image
import pytest
from fastapi.testclient import TestClient

from backend.app.config import settings
from backend.app.core import ai_client, ai_settings
from backend.app.api import ai
from main import app


client = TestClient(app, headers={'X-TeamHub-AI': '1'})
CONFIG = {'base_url': 'https://api.deepseek.com', 'model': 'deepseek-flash'}
COLORS = {(226, 46, 46): 'red', (32, 158, 74): 'green', (42, 96, 224): 'blue',
          (242, 209, 32): 'yellow', (151, 63, 202): 'purple', (239, 132, 30): 'orange'}


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch, tmp_path):
    monkeypatch.setenv('TEAMHUB_AI_CONFIG_DIR', str(tmp_path / 'ai-profile'))
    monkeypatch.setattr(settings, 'is_electron', False)
    monkeypatch.setattr(ai_settings, 'get_connection', lambda: (dict(CONFIG), 'synthetic-test-key', 'revision-a'))


def answer(value):
    return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {
        'content': json.dumps(value),
    }}]})


def read_test_image(payload):
    """The fake model infers the target solely from visible PNG pixels."""
    user = payload['messages'][-1]['content']
    assert isinstance(user, list)
    assert [part['type'] for part in user] == ['text', 'image_url']
    assert user[1]['image_url']['url'].startswith('data:image/png;base64,')
    image_bytes = base64.b64decode(user[1]['image_url']['url'].split(',', 1)[1], validate=True)
    assert len(image_bytes) < 32_000
    with Image.open(BytesIO(image_bytes)) as source:
        image = source.convert('RGB')
        assert image.size == (480, 180)
    result = {}
    for index, position in enumerate(('left', 'middle', 'right')):
        center = 80 + index * 160
        color = COLORS[image.getpixel((center, 90))]
        # Sample corners, the upper vertical edge, and the middle of that edge.
        corner = image.getpixel((center - 42, 48)) != (255, 255, 255)
        top = image.getpixel((center + 20, 42)) != (255, 255, 255)
        lower = image.getpixel((center + 38, 120)) != (255, 255, 255)
        shape = 'square' if corner else 'circle' if top else 'triangle' if lower else 'diamond'
        result[position] = {'shape': shape, 'color': color}
    return result, image_bytes, user[0]['text']


def test_vision_probe_uses_actual_fresh_png_and_returns_only_a_safe_verdict(monkeypatch):
    images = []
    def handler(request):
        payload = json.loads(request.content)
        result, image, prompt = read_test_image(payload)
        images.append(image)
        assert request.headers['Authorization'] == 'Bearer synthetic-test-key'
        assert 'synthetic-test-key' not in request.content.decode()
        assert 'expected' not in prompt and 'answer' not in prompt
        assert payload['model'] == 'deepseek-flash'
        assert payload['max_tokens'] <= 500
        return answer(result)
    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        monkeypatch.setattr(ai_client, '_http', transport)
        for _ in range(4):
            response = client.post('/api/ai/test', json={'mode': 'vision'})
            assert response.status_code == 200, response.text
            assert response.json() == {
                'ok': True, 'mode': 'vision',
                'message': '本次测试成功识别合成图中的颜色和形状。复杂图纸和资料仍需结合实际内容核对。',
            }
            assert 'synthetic-test-key' not in response.text
    assert len(set(images)) > 1, '探针必须重新随机生成，不能只询问模型自称能力'


@pytest.mark.parametrize('value', [
    {'summary': 'I support images', 'tags': []},
    {'left': {'shape': 'unknown', 'color': 'unknown'}},
    {'left': {'shape': 'square', 'color': 'red'}, 'middle': 'private-provider-response'},
    {position: {'shape': 'unknown', 'color': 'unknown'} for position in ('left', 'middle', 'right')},
    {position: 'private-provider-response' for position in ('left', 'middle', 'right')},
    {position: {'shape': 123, 'color': False} for position in ('left', 'middle', 'right')},
])
def test_vision_does_not_accept_self_report_or_unverifiable_content(monkeypatch, value):
    calls = []
    with httpx.Client(transport=httpx.MockTransport(lambda request: calls.append(request) or answer(value))) as transport:
        monkeypatch.setattr(ai_client, '_http', transport)
        response = client.post('/api/ai/test', json={'mode': 'vision'})
    assert response.status_code == 200, response.text
    assert response.json()['ok'] is False
    assert response.json()['mode'] == 'vision'
    assert '本次未能正确识别' in response.json()['message']
    assert 'private-provider-response' not in response.text
    assert len(calls) == 1


@pytest.mark.parametrize('status,code', [(401, 'AI_AUTH_FAILED'), (429, 'AI_RATE_LIMITED'),
                                       (500, 'AI_UNAVAILABLE'), (400, 'AI_REQUEST_FAILED')])
def test_vision_network_and_auth_failures_are_not_misreported_as_model_inability(monkeypatch, status, code):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(status, text='sensitive upstream'))) as transport:
        monkeypatch.setattr(ai_client, '_http', transport)
        response = client.post('/api/ai/test', json={'mode': 'vision'})
    assert response.json()['detail']['error']['code'] == code
    assert '不支持' not in response.text and 'sensitive upstream' not in response.text


def test_vision_result_from_changed_configuration_is_discarded(monkeypatch):
    def handler(request):
        result, _, _ = read_test_image(json.loads(request.content))
        monkeypatch.setattr(ai_settings, 'get_connection', lambda: (dict(CONFIG), 'other-test-key', 'revision-b'))
        return answer(result)
    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        monkeypatch.setattr(ai_client, '_http', transport)
        response = client.post('/api/ai/test', json={'mode': 'vision'})
    assert response.status_code == 409
    assert response.json()['detail']['error']['code'] == 'AI_CONFIGURATION_CHANGED'


def test_vision_timeout_is_not_retried_or_reported_as_unsupported(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout('sensitive upstream error', request=request)
    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        monkeypatch.setattr(ai_client, '_http', transport)
        response = client.post('/api/ai/test', json={'mode': 'vision'})
    assert response.status_code == 504
    assert response.json()['detail']['error']['code'] == 'AI_TIMEOUT'
    assert len(calls) == 1
    assert '可能已产生费用' in response.text
    assert 'sensitive upstream' not in response.text and '不支持' not in response.text


@pytest.mark.parametrize('body', [{}, {'mode': 'connection'}, None])
def test_existing_connection_check_remains_compatible(monkeypatch, body):
    with httpx.Client(transport=httpx.MockTransport(lambda _: answer({'summary': '连接成功', 'tags': []}))) as transport:
        monkeypatch.setattr(ai_client, '_http', transport)
        response = client.post('/api/ai/test', **({'json': body} if body is not None else {}))
    assert response.status_code == 200, response.text
    assert response.json() == {'ok': True, 'message': '连接成功，AI 服务已正常响应'}


def test_vision_has_local_request_capability_validation_and_shared_paid_call_lock(monkeypatch):
    with httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail('不得调用付费服务'))) as transport:
        monkeypatch.setattr(ai_client, '_http', transport)
        assert client.post('/api/ai/test', headers={'X-TeamHub-AI': ''}, json={'mode': 'vision'}).status_code == 403
        assert client.post('/api/ai/test', json={'mode': 'vision', 'path': 'private-secret-value'}).status_code == 422
        assert 'private-secret-value' not in client.post('/api/ai/test', json={'mode': 'bad-private-value'}).text
        monkeypatch.setattr(settings, 'is_electron', True)
        monkeypatch.setenv('TEAMHUB_AI_CAPABILITY', 'isolated-vision-capability')
        assert client.post('/api/ai/test', json={'mode': 'vision'}).status_code == 403
        monkeypatch.setattr(settings, 'is_electron', False)
        assert ai._paid_call.acquire(blocking=False)
        try:
            response = client.post('/api/ai/test', json={'mode': 'vision'})
            assert response.status_code == 429
            assert response.json()['detail']['error']['code'] == 'AI_BUSY'
        finally:
            ai._paid_call.release()
