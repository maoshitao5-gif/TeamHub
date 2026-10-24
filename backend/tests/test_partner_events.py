"""SSE与临时生成预览的本机边界。"""
import asyncio
import json
import threading

import pytest

import backend.app.database as database
from backend.app.partner_models import PartnerSession, PartnerTask
from backend.tests.test_partner_api import api
from backend.tests.test_partner_runtime import configure, partner_library


def test_event_endpoint_uses_same_local_and_desktop_capability_guard(api, monkeypatch):
    from backend.app.config import settings
    path = '/api/ai/partner/tasks/not-found/events'
    for headers in ({'X-TeamHub-AI': ''}, {'Origin': 'https://evil.invalid'}, {'Host': 'evil.invalid'}):
        assert api.get(path, headers=headers).status_code == 403
    monkeypatch.setattr(settings, 'is_electron', True)
    monkeypatch.setenv('TEAMHUB_AI_CAPABILITY', 'temporary-capability')
    assert api.get(path).status_code == 403
    assert api.get(path, headers={'X-TeamHub-AI-Capability': 'temporary-capability'}).status_code == 404


def test_completed_event_stream_has_snapshot_then_done(api):
    session = api.post('/api/ai/partner/sessions', json={}).json()
    with database.SessionLocal() as db:
        task = PartnerTask(session_id=session['id'], request_id='sse-done', preparation_id='p',
                           state='completed', message='消息', input_json='{}', result_json='{"answer":"最终回答"}')
        db.add(task); db.commit(); task_id = task.id
    response = api.get(f'/api/ai/partner/tasks/{task_id}/events')
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/event-stream')
    frames = response.text.split('\n\n')
    assert frames[0].startswith('event: task\ndata: ')
    snapshot = json.loads(frames[0].split('data: ', 1)[1])
    assert snapshot['id'] == task_id
    assert snapshot['answer'] == '最终回答'
    assert frames[1].startswith('event: done\ndata: ')


def test_prepare_api_accepts_only_explicit_thinking_modes(api, monkeypatch):
    configure(monkeypatch)
    session = api.post('/api/ai/partner/sessions', json={}).json()
    path = f'/api/ai/partner/sessions/{session["id"]}/prepare'
    preview = api.post(path, json={'message': '按轮选择', 'thinking_mode': 'disabled'})
    assert preview.status_code == 200
    assert preview.json()['service']['thinking_mode'] == 'disabled'
    assert api.post(path, json={'message': '默认值'}).json()['thinking_mode'] == 'enabled'
    for invalid in (None, True, 'other'):
        assert api.post(path, json={'message': '问题', 'thinking_mode': invalid}).status_code == 422


@pytest.mark.parametrize('terminal', ['completed', 'failed', 'cancelled'])
def test_preview_is_bounded_memory_only_and_cleared_at_terminal(partner_library, monkeypatch, terminal):
    runtime = partner_library
    configure(monkeypatch)
    session = runtime.create_session()
    prepared = runtime.prepare(session['id'], '流式生成')
    reached = asyncio.Event()
    release = asyncio.Event()
    task_id_holder = []
    async def fake_agent(**values):
        values['emit']('answer_preview', '未校验内容' * 4000)
        reached.set()
        await release.wait()
        if terminal == 'failed':
            raise RuntimeError('假失败')
        return {'answer': '最终校验结果', 'sources': [], 'artifacts': []}
    monkeypatch.setattr(runtime, 'run_agent', fake_agent)
    async def execute():
        task = await runtime.start(session['id'], prepared['preparation_id'], True, 'stream-preview')
        task_id_holder.append(task['id'])
        await reached.wait()
        snapshot = runtime.get_task(task['id'])
        assert len(snapshot['answer_preview']) == 18000
        assert snapshot['answer'] is None
        with database.SessionLocal() as db:
            stored = db.get(PartnerTask, task['id'])
            assert '未校验内容' not in stored.result_json
        assert [m['role'] for m in runtime.get_session(session['id'])['messages']] == ['user']
        if terminal == 'cancelled':
            await runtime.cancel(task['id'])
        release.set()
        for _ in range(100):
            await asyncio.sleep(.005)
            snapshot = runtime.get_task(task['id'])
            if snapshot['state'] not in {'queued', 'running'}:
                break
        assert snapshot['state'] == terminal
        assert snapshot['answer_preview'] is None
        return snapshot
    snapshot = asyncio.run(execute())
    assert snapshot['answer'] == ('最终校验结果' if terminal == 'completed' else None)


def test_event_generator_does_not_keep_a_database_session_while_waiting(partner_library, monkeypatch):
    runtime = partner_library
    session = runtime.create_session()
    with database.SessionLocal() as db:
        task = PartnerTask(session_id=session['id'], request_id='sse-live', preparation_id='p',
                           state='running', message='消息', input_json='{}')
        db.add(task); db.commit(); task_id = task.id
    calls = []
    original_sleep = asyncio.sleep
    async def observed_sleep(delay):
        assert database.active_session_count() == 0
        calls.append(delay)
        with database.SessionLocal() as db:
            db.get(PartnerTask, task_id).state = 'completed'
            db.commit()
        await original_sleep(0)
    monkeypatch.setattr(runtime.asyncio, 'sleep', observed_sleep)
    async def collect():
        stream = runtime.task_events(task_id)
        frames = []
        async for frame in stream:
            frames.append(frame)
        return frames
    frames = asyncio.run(collect())
    assert calls == [.2]
    assert sum(frame.startswith('event: task') for frame in frames) == 2
    assert frames[-1].startswith('event: done')


@pytest.mark.parametrize('entry_point', ['initial', 'refresh'])
def test_sse_switch_between_binding_and_session_never_reads_cloned_task_from_other_library(
        partner_library, tmp_path, monkeypatch, entry_point):
    """两个真实SQLite库含相同task UUID；在取SessionLocal前确定性交错切库。"""
    from backend.app.config import settings
    runtime = partner_library
    session = runtime.create_session('A会话')
    with database.SessionLocal() as db:
        task = PartnerTask(session_id=session['id'], request_id='same-task', preparation_id='p',
                           state='completed', message='A库原始内容', input_json='{}')
        db.add(task); db.commit(); task_id = task.id
    second_root = tmp_path / 'library-B'
    second_engine = database._create_engine(f'sqlite:///{second_root / ".teamhub" / "db.sqlite"}')
    database.Base.metadata.create_all(second_engine)
    with database.TrackedSession(bind=second_engine) as db:
        db.add(PartnerSession(id=session['id'], title='B会话'))
        db.flush()
        db.add(PartnerTask(id=task_id, session_id=session['id'], request_id='same-task',
            preparation_id='p', state='completed', message='B库私密内容绝不能进入A流', input_json='{}'))
        db.commit()
    second_engine.dispose()
    original_factory = database.SessionLocal
    original_close = database.TrackedSession.close
    original_json = runtime._json
    original_task_json = runtime._task_json
    initial_binding = runtime._binding()
    attempted, closed, switched = threading.Event(), threading.Event(), threading.Event()
    errors, workers, read_messages = [], [], []

    def switch_library():
        attempted.set()
        try:
            with database._active_sessions_lock:
                has_session = bool(database.active_session_count())
            if has_session:
                if not closed.wait(3):
                    raise AssertionError('快照没有及时关闭短会话')
            with database._active_sessions_lock:
                with database.library_change():
                    settings.library_path = str(second_root)
                    database.rebind_engine()
        except BaseException as error:
            errors.append(error)
        finally:
            switched.set()

    def interleaved_factory():
        if not workers:
            worker = threading.Thread(target=switch_library)
            workers.append(worker)
            worker.start()
            assert attempted.wait(3)
            # 有外层原子绑定锁时让切库线程等待；旧代码在此确定性切入B后才建会话。
            if not database._active_sessions_lock._is_owned():
                assert switched.wait(3)
        return original_factory()

    def close_and_signal(db):
        original_close(db)
        closed.set()

    def serialize_after_switch(value):
        if isinstance(value, dict) and value.get('id') == task_id:
            assert switched.wait(3)
        return original_json(value)

    def record_snapshot(task):
        read_messages.append(task.message)
        return original_task_json(task)

    monkeypatch.setattr(database, 'SessionLocal', interleaved_factory)
    monkeypatch.setattr(database.TrackedSession, 'close', close_and_signal)
    monkeypatch.setattr(runtime, '_json', serialize_after_switch)
    monkeypatch.setattr(runtime, '_task_json', record_snapshot)
    async def collect():
        stream = (runtime.event_stream(task_id) if entry_point == 'initial' else
                  runtime.task_events(task_id, binding=initial_binding))
        return [frame async for frame in stream]
    try:
        frames = asyncio.run(collect())
    finally:
        for worker in workers:
            worker.join(3)
    assert workers and switched.is_set(), '本用例必须实际完成A到B的切换'
    assert not errors
    assert not any(worker.is_alive() for worker in workers)
    assert database.active_session_count() == 0
    assert all('B库私密内容' not in message for message in read_messages)
    assert 'B库私密内容' not in ''.join(frames)
    assert frames == ['event: done\ndata: {"reason":"PARTNER_LIBRARY_CHANGED"}\n\n']
