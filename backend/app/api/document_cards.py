"""本机资料卡入口；读取不发送资料，生成使用逐资料任务授权。"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy.orm import Session

from backend.app.api.ai import SafeAIRoute, _error, _local_request
from backend.app.database import get_db
from backend.app.services import document_cards

router = APIRouter(prefix='/api/ai/cards', tags=['AI资料卡'], route_class=SafeAIRoute,
                   dependencies=[Depends(_local_request)])


class LibraryRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    library_token: str = Field(min_length=1, max_length=100)


class GenerateRequest(LibraryRequest):
    service_revision: str = Field(min_length=1, max_length=100)
    consent: StrictBool = False
    request_id: str = Field(min_length=1, max_length=100)
    auto_update: StrictBool = False


class PolicyRequest(LibraryRequest):
    enabled: StrictBool
    service_revision: str | None = Field(None, max_length=100)
    consent: StrictBool = False


class AnnotationRequest(LibraryRequest):
    version_id: str = Field(min_length=1, max_length=100)
    expected_revision: int = Field(ge=0)
    text: str = Field(max_length=4000)


@router.get('/config')
def config():
    from backend.app.services import card_jobs
    return card_jobs.get_config()


@router.get('/{document_id}')
def get_card(document_id: str, db: Session = Depends(get_db)):
    from backend.app.services import card_jobs
    try:
        result = document_cards.status(db, document_id)
    except ValueError:
        raise _error(404, 'CARD_DOCUMENT_UNAVAILABLE', '资料不存在或暂不可用') from None
    return {**result, **card_jobs.get_job_status(document_id)}


@router.post('/{document_id}/generate')
def generate(document_id: str, body: GenerateRequest):
    from backend.app.services import card_jobs
    return card_jobs.start(document_id=document_id, **body.model_dump())


@router.post('/{document_id}/cancel')
def cancel(document_id: str, body: LibraryRequest):
    from backend.app.services import card_jobs
    return card_jobs.cancel(document_id=document_id, library_token=body.library_token)


@router.put('/{document_id}/policy')
def policy(document_id: str, body: PolicyRequest):
    from backend.app.services import card_jobs
    return card_jobs.set_policy(document_id=document_id, **body.model_dump())


@router.put('/{document_id}/annotation')
def annotation(document_id: str, body: AnnotationRequest, db: Session = Depends(get_db)):
    from backend.app.services import preorganize
    preorganize._begin(db, body.library_token)
    try:
        return document_cards.save_annotation(db, document_id, body.version_id,
                                               body.expected_revision, body.text, commit=True)
    except ValueError as exc:
        raise _error(409, 'CARD_ANNOTATION_CONFLICT', str(exc)) from None
