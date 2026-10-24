"""智能伙伴本机 API：直接发送、持久会话及明确保存成果。"""
from typing import Literal

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from backend.app.api.ai import SafeAIRoute, _local_request
from backend.app.services import partner_runtime as runtime
from backend.app.core.partner_budget import budget_policy

router = APIRouter(prefix='/api/ai/partner', tags=['智能伙伴'], route_class=SafeAIRoute,
                   dependencies=[Depends(_local_request)])


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class SessionCreate(StrictRequest):
    title: str = Field('新对话', min_length=1, max_length=120)


class BudgetRequest(StrictRequest):
    mode: Literal['quick', 'standard', 'deep', 'custom'] = 'standard'
    model_requests: StrictInt | None = None
    tool_calls: StrictInt | None = None
    output_tokens: StrictInt | None = None
    time_seconds: StrictInt | None = None


class ContinuePrepare(StrictRequest):
    budget: BudgetRequest | None = None
    thinking_mode: Literal['enabled', 'disabled'] | None = None


class TurnPrepare(ContinuePrepare):
    thinking_mode: Literal['enabled', 'disabled'] = 'enabled'
    message: str = Field(min_length=1, max_length=8000)
    scope_mode: Literal['selected', 'library'] = 'selected'
    document_ids: list[str] = Field(default_factory=list, max_length=100)


class TurnStart(StrictRequest):
    preparation_id: str = Field(min_length=1, max_length=100)
    consent: StrictBool = False
    request_id: str = Field(min_length=1, max_length=100)


class ChatResume(StrictRequest):
    request_id: str = Field(min_length=1, max_length=100)
    expected_service_revision: str = Field(min_length=1, max_length=100)
    consent: StrictBool = False
    budget: BudgetRequest | None = None


class ChatSend(ChatResume):
    message: str = Field(min_length=1, max_length=8000)
    scope_mode: Literal['selected', 'library'] = 'selected'
    document_ids: list[str] = Field(default_factory=list, max_length=100)
    thinking_mode: Literal['enabled', 'disabled'] = 'disabled'


class ArtifactSave(StrictRequest):
    consent: StrictBool = False
    request_id: str = Field(min_length=1, max_length=100)


@router.get('/sessions')
def list_sessions():
    return runtime.list_sessions()


@router.get('/budget-policy')
def get_budget_policy():
    return budget_policy()


@router.get('/chat-config')
def chat_config():
    return runtime.chat_config()


@router.post('/sessions/{session_id}/send', status_code=202)
async def send(session_id: str, body: ChatSend):
    values = body.model_dump(exclude={'budget'})
    values['budget'] = body.budget.model_dump(exclude_unset=True) if body.budget else None
    return await runtime.send(session_id, **values)


@router.post('/tasks/{task_id}/resume', status_code=202)
async def resume(task_id: str, body: ChatResume):
    values = body.model_dump(exclude={'budget'})
    values['budget'] = body.budget.model_dump(exclude_unset=True) if body.budget else None
    return await runtime.resume(task_id, **values)


@router.post('/sessions')
def create_session(body: SessionCreate):
    return runtime.create_session(body.title)


@router.get('/sessions/{session_id}')
def get_session(session_id: str):
    return runtime.get_session(session_id)


@router.get('/sessions/{session_id}/requests/{request_id}')
def get_request(session_id: str, request_id: str):
    return runtime.get_request(session_id, request_id)


@router.delete('/sessions/{session_id}')
def delete_session(session_id: str):
    return runtime.delete_session(session_id)


@router.post('/sessions/{session_id}/prepare')
def prepare(session_id: str, body: TurnPrepare):
    return runtime.prepare(session_id, body.message, body.scope_mode, body.document_ids,
                           body.budget.model_dump(exclude_unset=True) if body.budget else None,
                           thinking_mode=body.thinking_mode)


@router.post('/sessions/{session_id}/turns', status_code=202)
async def start(session_id: str, body: TurnStart):
    return await runtime.start(session_id, body.preparation_id, body.consent, body.request_id)


@router.get('/tasks/{task_id}')
def get_task(task_id: str):
    return runtime.get_task(task_id)


@router.get('/tasks/{task_id}/events')
def task_events(task_id: str):
    return StreamingResponse(runtime.event_stream(task_id), media_type='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@router.post('/tasks/{task_id}/cancel')
async def cancel(task_id: str):
    return await runtime.cancel(task_id)


@router.post('/tasks/{task_id}/prepare-continue')
def prepare_continue(task_id: str, body: ContinuePrepare):
    return runtime.prepare_continue(task_id, body.budget.model_dump(exclude_unset=True) if body.budget else None,
                                    thinking_mode=body.thinking_mode)


@router.post('/tasks/{task_id}/continue', status_code=202)
async def continue_task(task_id: str, body: TurnStart):
    return await runtime.continue_task(task_id, body.preparation_id, body.consent, body.request_id)


@router.post('/tasks/{task_id}/artifacts/{artifact_id}/save')
def save(task_id: str, artifact_id: str, body: ArtifactSave):
    return runtime.save_artifact(task_id, artifact_id, body.consent, body.request_id)
