"""单份资料的预整理工具桥：读取快照，保存建议，不执行整理。"""
import asyncio
import base64
from copy import deepcopy
import json
from pathlib import Path, PurePosixPath
import unicodedata
from typing import Annotated, Literal

from pydantic import Field, ValidationError, field_validator

from backend.app.core.ai_client import AIError
from backend.app.services.hermes_tools import Arguments, Document, HermesTools, TOOLS
from backend.app.services.card_schema import CardContent
from backend.app.services.object_skills import object_kind
from backend.app.services.image_formats import IMAGE_SUFFIXES


def _has_control(value: str) -> bool:
    return any(unicodedata.category(char).startswith('C') for char in value)


def _relative_folder(value: str) -> str:
    if (value and (value.startswith(('/', '.')) or '\\' in value
                   or ':' in value or _has_control(value)
                   or any(part in {'', '.', '..'} for part in value.split('/')))):
        raise ValueError('分类位置必须为已有的相对目录。')
    return value


class OrganizationContext(Arguments):
    document_id: str = Field(min_length=1, max_length=100)
    tags: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(max_length=500)
    folders: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(max_length=200)
    existing_card: dict | None = None
    annotation: dict | None = None

    @field_validator('tags')
    @classmethod
    def valid_tags(cls, values):
        if any(not value.strip() or _has_control(value) for value in values):
            raise ValueError('标签名称无效。')
        return list(dict.fromkeys(values))

    @field_validator('folders')
    @classmethod
    def valid_folders(cls, values):
        return list(dict.fromkeys(_relative_folder(value) for value in values))


class OrganizationProposal(Arguments):
    name: str = Field(min_length=1, max_length=200)
    tags: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(max_length=20)
    target_dir: str = Field(max_length=500)
    summary: str = Field(min_length=1, max_length=2000)
    related_clues: list[Annotated[str, Field(min_length=1, max_length=300)]] = Field(max_length=20)
    gaps: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(max_length=30)
    source_ids: list[Annotated[str, Field(pattern=r'^S[1-9]\d{0,8}$')]] = Field(min_length=1, max_length=50)
    card: CardContent | None = None

    @field_validator('name')
    @classmethod
    def valid_name(cls, value):
        if (value != value.strip() or value in {'.', '..'}
                or any(char in value for char in '/\\:') or _has_control(value)
                or len(value.encode('utf-8')) > 255):
            raise ValueError('资料名称不能包含路径、控制字符或超出文件名长度。')
        return value

    @field_validator('target_dir')
    @classmethod
    def valid_folder(cls, value):
        return _relative_folder(value)

    @field_validator('tags')
    @classmethod
    def valid_tags(cls, values):
        if any(value != value.strip() or not value or _has_control(value) for value in values):
            raise ValueError('标签名称不能为空或包含控制字符。')
        return list(dict.fromkeys(values))

    @field_validator('summary')
    @classmethod
    def nonblank_summary(cls, value):
        if not value.strip():
            raise ValueError('请提供用于核对的简短说明。')
        return value


class OrganizationSubmission(OrganizationProposal):
    """New model submissions include a card; persisted old proposals stay readable."""
    card: CardContent = Field(...)


# ValidationError messages, inputs and unknown loc segments may contain model
# supplied secrets or paths. Only schema-owned field names and fixed explanations
# are ever returned to the model or retained for task diagnostics.
_CARD_FIELDS = {
    'summary': None, 'document_type': None, 'topics': [None],
    'sections': [{'title': None, 'summary': None, 'source_ids': [None]}],
    'facts': [{'label': None, 'value': None, 'source_ids': [None]}],
    'search_terms': [None], 'uses': [None], 'gaps': [None], 'source_ids': [None],
}
_PROPOSAL_FIELDS = {
    'name': None, 'tags': [None], 'target_dir': None, 'summary': None,
    'related_clues': [None], 'gaps': [None], 'source_ids': [None], 'card': _CARD_FIELDS,
}
_SCHEMA_MESSAGES = {
    'required': '缺少必填字段。',
    'invalid_type': '字段类型不正确，请按工具定义提交。',
    'too_short': '字段内容或项目数量不足，请按工具定义补全。',
    'too_long': '字段内容或项目数量超出限制，请按工具定义缩减。',
    'invalid_format': '字段格式不正确，请按工具定义修正。',
    'unexpected_field': '存在工具定义以外的字段，请移除后重新提交。',
    'invalid_value': '字段内容不符合要求，请按工具定义修正。',
}
_BUSINESS_MESSAGES = {
    'extension_mismatch': ('SUBMISSION_RULE_INVALID', '建议名称必须保留原文件的完整扩展名。'),
    'directory_unavailable': ('SUBMISSION_RULE_INVALID', '分类只能选择本次提供的已有目录，或留空。'),
    'source_unavailable': ('SUBMISSION_EVIDENCE_INVALID',
        '必须先实际阅读当前资料，再使用本任务返回的来源编号提交方案。'),
}
_EXPECTED_SUFFIXES = frozenset({
    '.txt', '.md', '.csv', '.tsv', '.json', '.yaml', '.yml', '.log', '.rst', '.xml', '.html', '.htm',
    '.doc', '.docx', '.dot', '.dotx', '.wps', '.odt', '.xls', '.xlsx', '.xlsm', '.et', '.ods',
    '.ppt', '.pptx', '.dps', '.odp', '.pdf', '.png', '.apng', '.jpg', '.jpeg', '.jpe', '.jfif',
    '.gif', '.webp', '.bmp', '.dib', '.tif', '.tiff', '.avif', '.avifs', '.jp2', '.j2k', '.jpc',
    '.jpf', '.jpx', '.j2c', '.ico', '.icns', '.pbm', '.pgm', '.ppm', '.pnm', '.tga', '.icb',
    '.vda', '.vst', '.pcx', '.heic', '.heif', '.svg', '.zip', '.7z', '.rar', '.tar', '.gz',
    '.bz2', '.xz', '.tgz', '.tbz', '.tbz2', '.txz', '.tar.gz', '.tar.bz2', '.tar.xz',
})


def _schema_field(location, *, card_only):
    node = _CARD_FIELDS if card_only else _PROPOSAL_FIELDS
    path = []
    for token in location:
        if isinstance(node, dict) and isinstance(token, str) and token in node:
            path.append(token)
            node = node[token]
        elif isinstance(node, list) and type(token) is int and token >= 0:
            path.append(str(token))
            node = node[0]
        else:
            break
    return '.'.join(path) or '$'


def _schema_code(kind):
    if kind == 'missing':
        return 'required'
    if kind == 'extra_forbidden':
        return 'unexpected_field'
    if kind in {'too_short', 'string_too_short'}:
        return 'too_short'
    if kind in {'too_long', 'string_too_long'}:
        return 'too_long'
    if kind in {'string_pattern_mismatch', 'literal_error', 'enum'}:
        return 'invalid_format'
    if kind in {'string_type', 'list_type', 'dict_type', 'model_type',
                'int_type', 'float_type', 'bool_type', 'none_required'}:
        return 'invalid_type'
    return 'invalid_value'


class SubmissionError(ValueError):
    """Only host-authored rule names and field paths cross the tool boundary."""

    def __init__(self, rule, field, *, expected_suffix=None):
        self.code, message = _BUSINESS_MESSAGES[rule]
        if (rule == 'extension_mismatch' and isinstance(expected_suffix, str)
                and expected_suffix.lower() in _EXPECTED_SUFFIXES):
            message += f' 当前必须使用 {expected_suffix}，不能改成其他后缀。'
        self.validation_errors = [{'field': field, 'code': rule, 'message': message}]
        super().__init__(message)


READ_TOOLS = {name: item for name, item in TOOLS.items()
              if name not in {'search_documents', 'create_draft', 'read_document_card'}}


class FolderList(Document):
    offset: int = Field(0, ge=0, le=10000)
    limit: int = Field(100, ge=1, le=100)


class FolderMember(Document):
    member_path: str = Field(min_length=1, max_length=4000)
    action: Literal['overview', 'text', 'table', 'visual', 'images'] = 'overview'
    offset: int = Field(0, ge=0)
    limit: int = Field(60, ge=1, le=100)
    sheet: str | None = Field(None, max_length=31)
    start_row: int = Field(1, ge=1, le=1048576)
    row_count: int = Field(80, ge=1, le=200)
    pages: list[int] | None = Field(None, max_length=3)
    image_ids: list[str] | None = Field(None, max_length=3)


FOLDER_READ_TOOLS = {
    'read_skill_reference': READ_TOOLS['read_skill_reference'],
    'read_object_skill': READ_TOOLS['read_object_skill'],
    'list_folder_members': (FolderList, '分页查看当前已保存文件夹的成员相对路径、类型与大小；'
        '目录清单不是正文依据。沿 next_call 翻页，选有代表性的成员读取。'),
    'read_folder_member': (FolderMember, '读取清单中一个成员：overview 结构，text 原文，table 表格，'
        'visual PDF原页或图片，images Office嵌图。只接受当前文件夹快照中的 member_path；'
        '保留成员相对路径和来源编号，按 next_call 或 next_offset 补读。'),
}
ARCHIVE_READ_TOOLS = {
    'read_skill_reference': READ_TOOLS['read_skill_reference'],
    'read_object_skill': READ_TOOLS['read_object_skill'],
    'list_archive_members': (FolderList, '分页查看当前压缩包已保存版本的成员相对路径、类型和大小；清单不是正文依据，不实际解压到文档库。'),
    'read_archive_member': (FolderMember, '读取压缩包清单中的一个成员：overview结构，text原文，table表格，visual原图，images办公嵌图；保留成员路径和来源，按 next_call 继续。'),
}
ORGANIZATION_TOOLS = {**READ_TOOLS, 'propose_organization': (
    OrganizationSubmission, '提交有本任务阅读依据的整理方案及必填 card 资料卡并结束任务；不修改原资料。'
    '文件名称保留原扩展名；文件夹整体命名，不改动成员。target_dir只选本次已有目录或空；准确列出未读和未识别内容。')}


def organization_instructions(is_folder=False, kind=None):
    skill = Path(__file__).resolve().parents[1] / 'skills' / 'document-organization' / 'SKILL.md'
    if not skill.is_file():
        raise AIError(503, 'ORGANIZATION_SKILL_MISSING', 'AI 预整理工作规则未完整安装，请重新构建应用。')
    from backend.app.services.card_tools import card_instructions
    return card_instructions(is_folder=is_folder, kind=kind) + '\n\n' + skill.read_text(encoding='utf-8')


class OrganizationTools(HermesTools):
    """每个实例只服务一个新任务；不接受历史证据或可变的分类语境。"""

    def __init__(self, *, engine, root, scope, guard, emit, purpose_context):
        context = OrganizationContext.model_validate(purpose_context)
        if (set(scope) != {context.document_id}
                or type(scope[context.document_id].get('is_folder')) is not bool):
            raise ValueError('预整理只支持当前授权的一项资料。')
        self.context = context.model_dump()
        self.document_id = context.document_id
        self.is_folder = scope[self.document_id]['is_folder']
        self.original_suffix = PurePosixPath(scope[self.document_id]['name']).suffix
        self.proposal = None
        self.card = None
        self.submission_attempts = 0
        self.last_submission_error = None
        self._observed_gaps: list[str] = []
        self._unit_count = None
        self._visual_count = None
        self._asset_count = None
        super().__init__(engine=engine, root=root, scope=scope, guard=guard, emit=emit)
        with self.tools._session() as db:
            _, version = self.tools._document(db, self.document_id)
            filename = version.original_filename
        self.object_kind = object_kind(filename, is_folder=self.is_folder)
        self.is_container = self.object_kind in {'folder', 'archive'}
        self.context['object_kind'] = self.object_kind
        self.read_tools = (FOLDER_READ_TOOLS if self.is_folder else ARCHIVE_READ_TOOLS
                           if self.object_kind == 'archive' else READ_TOOLS)
        self._loaded_object_skills.add(self.object_kind)
        if self.is_folder:
            from backend.app.services.folder_reader import FolderReader
            self.tools = FolderReader(engine=engine, root=root, scope=scope, guard=guard, emit=emit)
            return
        if self.object_kind == 'archive':
            from backend.app.services.archive_reader import ArchiveReader
            self.tools = ArchiveReader(engine=engine, root=root, scope=scope, guard=guard, emit=emit)
            # Preserve compound suffixes such as .tar.gz when proposing a name.
            display_name = scope[self.document_id]['name']
            for ending in ('.tar.gz', '.tar.bz2', '.tar.xz'):
                if display_name.lower().endswith(ending):
                    self.original_suffix = display_name[-len(ending):]
                    break
            return
        # Naming follows the current display name, but parsing and coverage must
        # follow the immutable version's filename, exactly as PartnerTools does.
        data, filename = self.tools._snapshot(self.document_id)
        self.snapshot_suffix = PurePosixPath(filename).suffix.lower()
        self._snapshot_signature = data[:8]

    def preflight(self) -> None:
        """在模型调用前识别不能解析的 OLE 内容；显示后缀不能证明实际格式。"""
        self.verify()
        if self.is_container:
            try:
                self.tools.preflight()
            except ValueError as exc:
                raise AIError(422, 'AI_CONTAINER_UNREADABLE', str(exc)) from None
            return
        if self._snapshot_signature == bytes.fromhex('d0cf11e0a1b11ae1'):
            raise AIError(422, 'AI_ORGANIZATION_FORMAT_UNREADABLE',
                '已保存版本是旧版或加密的 Office/WPS 二进制格式，当前无法读取。'
                '请在 WPS/Office 中另存为未加密的标准 DOCX、XLSX 或 PPTX 后重新收录；'
                '仅修改文件名后缀不会转换格式。')

    def definitions(self):
        return [{'type': 'function', 'function': {'name': name, 'description': description,
                'parameters': model.model_json_schema()}}
                for name, (model, description) in self._tool_map().items()]

    def _tool_map(self):
        return {**self.read_tools, 'propose_organization': ORGANIZATION_TOOLS['propose_organization']}

    async def call(self, name, arguments):
        if not isinstance(name, str) or name not in self._tool_map():
            await asyncio.to_thread(self.verify)
            self.calls += 1
            return {'result': {'ok': False, 'error': '此工具不在预整理的可用范围内。'}}
        if name == 'propose_organization':
            await asyncio.to_thread(self.verify)
            self.calls += 1
            self.submission_attempts += 1
            try:
                value = OrganizationSubmission.model_validate(arguments).model_dump()
                self._validate_proposal(value)
                value['card'] = self.validate_card(value['card'])
            except (ValueError, ValidationError) as exc:
                return {'result': self.submission_failure(exc)}
            value['gaps'] = list(dict.fromkeys([*self._coverage_gaps(), *value['gaps']]))[:30]
            self.proposal = value
            self.card = value['card']
            self.last_submission_error = None
            self.emit('status', 'AI 预整理方案已生成，等待用户确认。')
            return {'result': {'ok': True, 'status': 'proposal_ready'}}
        if self.is_container and name in self.read_tools and name not in {'read_skill_reference', 'read_object_skill'}:
            return await self._call_folder(name, arguments)
        result = await super().call(name, arguments)
        metadata = result['result']
        if metadata.get('document_id') == self.document_id:
            self._observe(metadata)
        return result

    def submission_failure(self, exc, *, card_only=False):
        error = ('资料卡格式不符合要求，请按工具定义修正。' if card_only else
                 '方案格式不符合要求，请按工具定义修正，并包含有效的 card 资料卡。')
        if isinstance(exc, ValidationError):
            details = []
            for item in exc.errors(include_input=False, include_url=False, include_context=False)[:8]:
                code = _schema_code(item['type'])
                details.append({'field': _schema_field(item['loc'], card_only=card_only),
                                'code': code, 'message': _SCHEMA_MESSAGES[code]})
            result = {'ok': False, 'error': error, 'code': 'SUBMISSION_SCHEMA_INVALID',
                      'validation_errors': details}
        elif isinstance(exc, SubmissionError):
            result = {'ok': False, 'error': str(exc), 'code': exc.code,
                      'validation_errors': exc.validation_errors}
        else:
            # Unexpected implementation errors must not echo filesystem paths or
            # exception text; the host can still distinguish this from schema repair.
            result = {'ok': False, 'error': error, 'code': 'SUBMISSION_RULE_INVALID',
                      'validation_errors': [{'field': '$', 'code': 'invalid_value',
                                             'message': _SCHEMA_MESSAGES['invalid_value']}]}
        self.last_submission_error = deepcopy(result)
        return result

    async def _call_folder(self, name, arguments):
        from backend.app.services.partner_tools import PartnerEvidenceLimit, PartnerNoProgress, VisualResult
        await asyncio.to_thread(self.verify)
        self.calls += 1
        try:
            values = self.read_tools[name][0].model_validate(arguments).model_dump()
        except ValidationError:
            return {'result': {'ok': False, 'error': '成员读取参数不符合要求，请按工具定义修正。'}}
        signature = json.dumps([name, values], sort_keys=True, ensure_ascii=False)
        if signature in self._blocked_reads or (self._evidence_full and name in {'read_folder_member', 'read_archive_member'}):
            return {'result': {'ok': False, 'code': 'USE_EXISTING_EVIDENCE',
                'error': '此读取未能增加可用依据，已跳过重复处理。',
                'next_steps': '使用已读成员的依据提交当前方案或资料卡，并列明未读范围。'}}
        try:
            value = await asyncio.to_thread(getattr(self.tools, name), **values)
        except ValueError as exc:
            await asyncio.to_thread(self.verify)
            if isinstance(exc, (PartnerNoProgress, PartnerEvidenceLimit)):
                self._blocked_reads.add(signature)
                self._evidence_full = isinstance(exc, PartnerEvidenceLimit)
                self.last_checkpoint = self.tools.export_checkpoint()
                return {'result': {'ok': False,
                    'code': 'EVIDENCE_CAPACITY' if self._evidence_full else 'NO_NEW_EVIDENCE',
                    'error': '本次成员读取未增加可保存的新依据。',
                    'next_steps': '利用已有依据提交并保留缺口；若仍有容量，可选择其他未读成员，勿重复同一范围。'}}
            return {'result': {'ok': False, 'error': str(exc)[:500],
                'next_steps': '依据成员清单调整阅读方式；无法读取的成员记录为缺口，使用已有依据完成当前方案。'}}
        await asyncio.to_thread(self.verify)
        self.last_checkpoint = self.tools.export_checkpoint()
        if isinstance(value, VisualResult):
            return {'result': value.metadata, 'images': [
                {'media_type': item.media_type, 'data': base64.b64encode(item.data).decode('ascii')}
                for item in value.images]}
        return {'result': value}

    def _validate_proposal(self, value):
        if not self.is_folder:
            suffix_matches = (value['name'].endswith(self.original_suffix)
                if self.object_kind == 'archive' and self.original_suffix.lower().startswith('.tar.')
                else PurePosixPath(value['name']).suffix == self.original_suffix)
            if not suffix_matches:
                raise SubmissionError('extension_mismatch', 'name', expected_suffix=self.original_suffix)
        if value['target_dir'] and value['target_dir'] not in self.context['folders']:
            raise SubmissionError('directory_unavailable', 'target_dir')
        self._validate_sources(value['source_ids'])

    def _current_source_ids(self):
        frozen = self.tools.scope[self.document_id]
        return {source['source_id'] for source in self.tools.sources
                if source['document_id'] == self.document_id
                and all(source.get(key) == frozen.get(key)
                        for key in ('version_id', 'content_hash'))}

    def _validate_sources(self, requested, field='source_ids', *, available=None):
        sources = self._current_source_ids() if available is None else available
        if not sources or not set(requested).issubset(sources):
            raise SubmissionError('source_unavailable', field)

    def validate_card(self, value, *, field_prefix='card.'):
        card = CardContent.model_validate(value).model_dump()
        available = self._current_source_ids()
        self._validate_sources(card['source_ids'], field_prefix + 'source_ids', available=available)
        for key in ('sections', 'facts'):
            for index, item in enumerate(card[key]):
                self._validate_sources(item['source_ids'],
                    f'{field_prefix}{key}.{index}.source_ids', available=available)
        card['gaps'] = list(dict.fromkeys([*self._coverage_gaps(), *card['gaps']]))[:30]
        return card

    def coverage(self):
        """Reading coverage is measured by host tools, never supplied by the model."""
        if self.is_container:
            return self.tools.coverage()
        progress = self.tools.progress
        gaps = self._coverage_gaps()
        suffix = self.snapshot_suffix
        # Plain text has no page rendering or embedded images. Office layout and
        # uninspected PDF/image modalities stay unknown rather than inventing zero.
        plain = suffix in {'.txt', '.md', '.csv', '.json', '.yaml', '.yml', '.log', '.rst'}
        image = suffix in IMAGE_SUFFIXES
        totals = (0 if image else self._unit_count,
                  0 if plain else self._visual_count,
                  0 if plain or image or suffix == '.pdf' else self._asset_count)
        values = (progress['read_units'], progress['visual_pages'], progress['embedded_images'])
        complete = (not gaps and bool(self.tools.sources)
                    and all(total is not None and count >= total for count, total in zip(values, totals)))
        return {'read_units': values[0], 'total_units': totals[0],
                'visual_pages': values[1], 'total_visual_pages': totals[1],
                'embedded_images': values[2], 'total_embedded_images': totals[2],
                'gaps': gaps, 'complete': complete}

    def _observe(self, metadata):
        for field, attribute in (('unit_count', '_unit_count'), ('page_count', '_visual_count'),
                                 ('visual_asset_count', '_asset_count'), ('asset_count', '_asset_count')):
            if type(metadata.get(field)) is int:
                setattr(self, attribute, metadata[field])
        # 视觉工具没有解析 state/gaps；缩放与识别限制只放在 notes 中。
        if (metadata.get('images') or self.object_kind != 'image' and
                (metadata.get('gaps') or metadata.get('state') in {'partial', 'failed'})):
            self._observed_gaps.extend(str(note)[:500] for note in metadata.get('notes', []))
            if not metadata.get('notes') and not metadata.get('images'):
                self._observed_gaps.append('部分内容未能解析，需结合原件核对。')
        if metadata.get('visual_notes'):
            self._observed_gaps.extend(str(note)[:500] for note in metadata['visual_notes'])

    def _coverage_gaps(self):
        if self.is_container:
            return self.tools.coverage_gaps()
        progress = self.tools.progress
        gaps = list(dict.fromkeys(self._observed_gaps))
        if self._unit_count is not None and progress['read_units'] < self._unit_count:
            gaps.append(f'已读原文单元 {progress["read_units"]} / {self._unit_count}，其余文字尚未阅读。')
        if self._visual_count is not None and progress['visual_pages'] < self._visual_count:
            gaps.append(f'已查看原图页 {progress["visual_pages"]} / {self._visual_count}，其余原页尚未查看。')
        if self._asset_count is not None and progress['embedded_images'] < self._asset_count:
            gaps.append(f'已查看内嵌图片 {progress["embedded_images"]} / {self._asset_count}，其余图片尚未查看。')
        suffix = self.snapshot_suffix
        if suffix == '.pdf' and not progress['visual_pages']:
            gaps.append('尚未核对 PDF 原页图形与版式，文字读取不代表理解所有图形。')
        if suffix in {'.docx', '.xlsx', '.pptx', '.doc', '.xls', '.ppt', '.odt', '.ods', '.odp'}:
            gaps.append('未还原整份办公资料的页面布局、图表和样式，需结合原件核对。')
        if self._unit_count is None and self._visual_count is None:
            gaps.append('尚未核对整份资料的结构与完整覆盖范围。')
        return gaps
