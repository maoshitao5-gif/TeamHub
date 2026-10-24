"""资料卡内容契约；独立于工具桥，供生成、持久化和读取共同校验。"""
import unicodedata
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1
SKILL_VERSION = '2'


def readable(value: str) -> str:
    if not value.strip() or any(unicodedata.category(char).startswith('C')
                                and char not in '\n\t' for char in value):
        raise ValueError('文本不能为空或包含不可见控制字符。')
    return value


def bounded_text(limit: int):
    return Annotated[str, Field(min_length=1, max_length=limit), AfterValidator(readable)]


SourceID = Annotated[str, Field(pattern=r'^S[1-9]\d{0,8}$')]
SourceIDs = Annotated[list[SourceID], Field(min_length=1, max_length=200)]


class CardBase(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')


class CardSection(CardBase):
    title: bounded_text(200)
    summary: bounded_text(1000)
    source_ids: SourceIDs


class CardFact(CardBase):
    label: bounded_text(120)
    value: bounded_text(1000)
    source_ids: SourceIDs


class CardContent(CardBase):
    summary: bounded_text(2000)
    document_type: bounded_text(80)
    topics: list[bounded_text(200)] = Field(max_length=20)
    sections: list[CardSection] = Field(max_length=80)
    facts: list[CardFact] = Field(max_length=80)
    search_terms: list[bounded_text(300)] = Field(max_length=40)
    # 用途是 AI 给出的适用性线索，不能混作原文事实。
    uses: list[bounded_text(500)] = Field(max_length=12)
    gaps: list[bounded_text(500)] = Field(max_length=30)
    source_ids: SourceIDs
