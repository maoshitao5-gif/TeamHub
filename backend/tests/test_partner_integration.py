"""伙伴作为真实应用功能注册，兼容已有个人资料库。"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect

import backend.app.database as database
from backend.app.config import settings
from main import app


@pytest.fixture
def partner_library(tmp_path, monkeypatch):
    previous = settings.library_path
    monkeypatch.setenv('TEAMHUB_SANDBOX', '1')
    monkeypatch.setenv('TEAMHUB_AI_CONFIG_DIR', str(tmp_path / 'ai-profile'))
    monkeypatch.setattr(settings, 'library_path', str(tmp_path / 'library'))
    database.rebind_engine()
    database.init_db()
    yield tmp_path / 'library'
    settings.library_path = previous
    database.rebind_engine()


def test_real_app_exposes_partner_without_requiring_model_key(partner_library):
    with TestClient(app, headers={'X-TeamHub-AI': '1'}) as client:
        response = client.get('/api/ai/partner/sessions')
        assert response.status_code == 200, response.text
        assert response.json()['items'] == []
        session = client.post('/api/ai/partner/sessions', json={'title': '分析项目资料'})
        assert session.status_code in {200, 201}, session.text
        session_id = session.json()['id']
        database.init_db()
        assert client.get(f'/api/ai/partner/sessions/{session_id}').status_code == 200


def test_partner_schema_is_additive_and_keeps_existing_tables(partner_library):
    tables = set(inspect(database.engine).get_table_names())
    assert {'documents', 'versions', 'document_indexes'} <= tables
    assert {'partner_sessions', 'partner_tasks'} <= tables


def test_partner_routes_inherit_local_ai_security(partner_library):
    with TestClient(app) as client:
        response = client.get('/api/ai/partner/sessions')
        assert response.status_code == 403, response.text
