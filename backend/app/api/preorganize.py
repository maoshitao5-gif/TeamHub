"""本机待整理 AI 预处理入口；读接口不发起外部调用。"""
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from backend.app.api.ai import SafeAIRoute, _local_request
from backend.app.services import preorganize as service

router = APIRouter(prefix='/api/ai/preorganize', tags=['AI预整理'], route_class=SafeAIRoute,
                   dependencies=[Depends(_local_request)])


class LibraryRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    library_token: str = Field(min_length=1, max_length=100)


class StartRequest(LibraryRequest):
    service_revision: str = Field(min_length=1, max_length=100)
    consent: StrictBool = False
    request_id: str = Field(min_length=1, max_length=100)
    document_ids: list[str] = Field(min_length=1, max_length=100)


class PolicyRequest(LibraryRequest):
    service_revision: str | None = Field(None, max_length=100)
    consent: StrictBool = False
    enabled: StrictBool


class CancelRequest(LibraryRequest):
    document_ids: list[str] = Field(min_length=1, max_length=100)


class ApplyItem(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    document_id: str = Field(min_length=1, max_length=100)
    proposal_id: str = Field(min_length=1, max_length=100)
    request_id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    tags: list[str] = Field(max_length=20)
    target_dir: str = Field('', max_length=500)


class ApplyRequest(LibraryRequest):
    items: list[ApplyItem] = Field(min_length=1, max_length=100)


@router.get('/config')
def config():
    return service.get_config()


@router.get('/documents')
def documents(search: str = Query('', max_length=500),
              state: Literal['', 'waiting', 'processing', 'ready', 'failed'] = '',
              page: int = Query(1, ge=1), page_size: int = Query(30, ge=1, le=100)):
    return service.list_documents(search, state, page, page_size)


@router.get('/documents/{document_id}')
def document(document_id: str):
    return service.get_document(document_id)


@router.post('/start')
def start(body: StartRequest):
    return service.start(**body.model_dump())


@router.put('/policy')
def policy(body: PolicyRequest):
    return service.set_policy(**body.model_dump())


@router.post('/cancel')
def cancel(body: CancelRequest):
    return service.cancel(**body.model_dump())


@router.post('/apply')
def apply(body: ApplyRequest):
    return service.apply(body.library_token, [item.model_dump() for item in body.items])


@router.get('/operations')
def operations():
    return service.operations()


@router.post('/operations/{operation_id}/undo')
def undo(operation_id: str, body: LibraryRequest):
    return service.undo(body.library_token, operation_id)
