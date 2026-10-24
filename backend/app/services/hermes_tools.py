"""Hermes 的宿主工具桥。资料、证据与草稿始终由 TeamHub 持有。"""
import asyncio
import base64
import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.app.core.partner_protocol import _draft_with_sources, _validate_draft
from backend.app.models import generate_uuid
from backend.app.services.partner_tools import PartnerTools, VisualResult
from backend.app.services.skill_resources import Method, read_reference
from backend.app.services.object_skills import OBJECT_SKILL_VERSION, object_instructions


class Arguments(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Search(Arguments):
    query: str = Field(min_length=1, max_length=120)
    limit: int = Field(8, ge=1, le=10)
    offset: int = Field(0, ge=0, le=10000)


class Document(Arguments):
    document_id: str = Field(min_length=1, max_length=100)


class SkillReference(Arguments):
    method: Method


class ObjectSkill(Arguments):
    kind: Literal['text', 'word', 'spreadsheet', 'presentation', 'pdf', 'image', 'folder', 'archive']


class Read(Document):
    offset: int = Field(0, ge=0)
    limit: int = Field(60, ge=1, le=100)


class Table(Document):
    sheet: str | None = Field(None, max_length=200)
    start_row: int = Field(1, ge=1)
    row_count: int = Field(80, ge=1, le=200)
    offset: int = Field(0, ge=0)


class Visual(Document):
    pages: list[int] | None = Field(None, max_length=3)


class Embedded(Document):
    image_ids: list[str] = Field(max_length=3)


class Draft(Arguments):
    name: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=30000)
    source_ids: list[str] = Field(default_factory=list, max_length=50)


TOOLS = {
    'search_documents': (Search, '在授权当前版本的名称、标签、原文片段和资料卡线索中按相关性找候选；可组合少量关键词、换词或翻页。返回 matched_passages 与阅读 next_call，仅是定位线索；关键内容需实际阅读核对，当前未启用向量语义检索。'),
    'document_overview': (Document, '查看已保存资料的结构、工作表、文字覆盖缺口与可读图片。'),
    'read_document_card': (Document, '读取当前版本的 AI 资料卡与人工补充，帮助筛选和定位；这是派生理解，不产生原文来源编号，关键事实须读原文核对。'),
    'read_skill_reference': (SkillReference, '按内容形态加载随应用发布的阅读方法：text 长文/条款；tables 明细/填报表/多区域；visuals 扫描/图纸/图表；mixed 图文混合。按需读取，不用每次加载全部；不接受路径或网址。'),
    'read_object_skill': (ObjectSkill, '按对象类型加载独立阅读与整理规则：text、word、spreadsheet、presentation、pdf、image、folder、archive。仅加载当前确需理解的类型；不接受路径或网址。'),
    'read_document': (Read, '分页读取已保存版本的原文与来源编号；遵循 next_call 补读。'),
    'read_table': (Table, '按工作表行块读取，保留行列坐标；可用 next_call 连续阅读。'),
    'read_visual': (Visual, '查看 PDF 原页或独立图片；页码从1开始，每次最多3页。'),
    'read_embedded_images': (Embedded, '查看 overview 返回的 Office 内嵌图片，每次最多3张。'),
    'create_draft': (Draft, '生成待确认收录的 Markdown 或文本草稿，最多3份；不改写原资料。'),
}


def tool_definitions():
    return [{'type': 'function', 'function': {'name': name, 'description': description,
             'parameters': model.model_json_schema()}} for name, (model, description) in TOOLS.items()]


class HermesTools:
    def __init__(self, *, engine, root, scope, guard, emit, checkpoint=None, artifacts=None):
        self.tools = PartnerTools(engine=engine, root=root, scope=scope, guard=guard, emit=emit)
        if checkpoint:
            self.tools.restore_checkpoint(checkpoint)
        self.guard, self.emit = guard, emit
        self.artifacts = list(artifacts or [])
        self.last_checkpoint = self.tools.export_checkpoint()
        self.calls = 0
        self._blocked_reads = set()
        self._evidence_full = False
        self._loaded_methods = set()
        self._loaded_object_skills = set()

    def definitions(self):
        return tool_definitions()

    def verify(self):
        self.guard()
        for document_id in self.tools.exposed_document_ids.copy():
            self.tools._assert_current(document_id)

    async def call(self, name, arguments):
        await asyncio.to_thread(self.verify)
        self.calls += 1
        if not isinstance(name, str) or name not in TOOLS:
            return {'result': {'ok': False, 'error': '此工具不在本轮可用范围内。'}}
        try:
            values = TOOLS[name][0].model_validate(arguments).model_dump()
        except ValidationError:
            return {'result': {'ok': False, 'error': '工具参数不符合要求，请按工具定义修正。'}}
        if name == 'create_draft':
            return {'result': self._draft(**values)}
        if name == 'read_object_skill':
            kind = values['kind']
            if kind in self._loaded_object_skills:
                return {'result': {'ok': True, 'kind': kind, 'already_loaded': True,
                    'notice': '本任务已加载此对象规则，请沿用，无需重复读取。'}}
            try:
                content = await asyncio.to_thread(object_instructions, kind)
            except ValueError as exc:
                return {'result': {'ok': False, 'error': str(exc)}}
            await asyncio.to_thread(self.verify)
            self._loaded_object_skills.add(kind)
            return {'result': {'ok': True, 'kind': kind, 'version': OBJECT_SKILL_VERSION, 'content': content}}
        if name == 'read_skill_reference':
            method = values['method']
            if method in self._loaded_methods:
                return {'result': {'ok': True, 'method': method, 'already_loaded': True,
                    'notice': '本任务已加载此方法，请沿用前文规则，无需重复读取。'}}
            try:
                result = await asyncio.to_thread(read_reference, method)
            except ValueError as exc:
                return {'result': {'ok': False, 'error': str(exc)}}
            await asyncio.to_thread(self.verify)
            self._loaded_methods.add(method)
            return {'result': result}
        signature = json.dumps([name, values], sort_keys=True, ensure_ascii=False)
        if signature in self._blocked_reads or (self._evidence_full and name in {
                'read_document', 'read_table', 'read_visual', 'read_embedded_images'}):
            return {'result': {'ok': False, 'code': 'USE_EXISTING_EVIDENCE',
                'error': '此读取未能增加可用依据，已跳过重复处理。',
                'next_steps': '请用已有内容回答并说明缺口；仅在有新的明确线索时换一种方法。'}}
        try:
            value = await asyncio.to_thread(getattr(self.tools, name), **values)
        except ValueError as exc:
            from backend.app.services.partner_tools import PartnerNoProgress, PartnerEvidenceLimit
            if isinstance(exc, (PartnerNoProgress, PartnerEvidenceLimit)):
                await asyncio.to_thread(self.verify)
                self._blocked_reads.add(signature)
                self._evidence_full = isinstance(exc, PartnerEvidenceLimit)
                self.last_checkpoint = self.tools.export_checkpoint()
                code = 'EVIDENCE_CAPACITY' if self._evidence_full else 'NO_NEW_EVIDENCE'
                self.emit('status', '正在根据已有阅读结果调整方法。')
                return {'result': {'ok': False, 'code': code,
                    'error': '本次读取没有增加可保存的依据。' if self._evidence_full else '本次读取未增加新依据。',
                    'next_steps': '使用已有依据回答并说明缺口；如确有新线索，可换关键词、资料或未读范围，勿重复同一调用。'}}
            await asyncio.to_thread(self.verify)
            self.emit('warning', '本次读取未完成，可以调整阅读方式或说明缺口。')
            return {'result': {'ok': False, 'error': str(exc)[:250],
                'next_steps': '查看资料结构、换关键词或读取原页；未取得内容时明确说明。'}}
        await asyncio.to_thread(self.verify)
        self.last_checkpoint = self.tools.export_checkpoint()
        if isinstance(value, VisualResult):
            return {'result': value.metadata, 'images': [
                {'media_type': image.media_type, 'data': base64.b64encode(image.data).decode('ascii')}
                for image in value.images]}
        return {'result': value}

    def _draft(self, name, content, source_ids):
        source_ids = list(dict.fromkeys([*source_ids, *re.findall(r'\[(S\d+)\]', content)]))
        try:
            _validate_draft(name, content, self.tools.sources, source_ids)
            content = _draft_with_sources(content, source_ids, self.tools.sources)
        except ValueError as exc:
            return {'ok': False, 'error': str(exc)}
        digest = hashlib.sha256(content.encode()).hexdigest()
        for existing in self.artifacts:
            if existing['name'] == name and existing['content_hash'] == digest:
                return {key: existing[key] for key in ('id', 'name', 'content_hash', 'source_ids')}
        if len(self.artifacts) >= 3:
            return {'ok': False, 'error': '本任务最多生成3份草稿；已有草稿仍可查看和保存。'}
        artifact = {'id': generate_uuid(), 'name': name, 'content': content,
            'media_type': 'text/markdown' if name.lower().endswith('.md') else 'text/plain',
            'content_hash': hashlib.sha256(content.encode()).hexdigest(), 'source_ids': source_ids}
        self.artifacts.append(artifact)
        self.emit('artifact', f'已生成草稿《{name}》，等待确认收录。')
        return {key: artifact[key] for key in ('id', 'name', 'content_hash', 'source_ids')}
