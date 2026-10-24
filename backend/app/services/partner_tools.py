"""AI 伙伴的受控资料工具：冻结授权、固定快照、原文出处和真实原页。

此模块不访问模型，不修改资料。渲染子进程只接收快照字节，不接收任意路径。
"""
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
from io import BytesIO
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
import unicodedata
from typing import Callable
import warnings

from backend.app.services.image_formats import IMAGE_SUFFIXES

MAX_INPUT_BYTES = 32 * 1024 * 1024
MAX_VISUAL_PAGES = 3
MAX_VISUAL_PIXELS = 12_000_000
MAX_PAGE_PIXELS = 4_000_000
MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_TASK_IMAGES = 24
MAX_TASK_IMAGE_BYTES = 12 * 1024 * 1024
MAX_RENDER_OUTPUT_BYTES = MAX_IMAGE_BYTES * 4 + 65536
MAX_TEXT_RETURN = 16_000
MAX_CHECKPOINT_TEXT = 180_000
MAX_CHECKPOINT_BYTES = 1024 * 1024
MAX_CHECKPOINT_SOURCES = 1000
MAX_CONTEXT_SOURCES = 100
MAX_CONTEXT_TEXT = 60_000
# 来源号跨普通轮次保持不变；限制数字长度，避免恶意检查点制造无界编号。
MAX_SOURCE_NUMBER = 1_000_000_000
MAX_READ_RANGES = 1000
NO_PROGRESS_LIMIT = 3
_RENDER_FLAG = '--teamhub-partner-render'
_IMAGE_SUFFIXES = IMAGE_SUFFIXES


@dataclass(frozen=True)
class VisualImage:
    data: bytes
    media_type: str
    source_id: str
    page: int


@dataclass(frozen=True)
class VisualResult:
    metadata: dict
    images: list[VisualImage]


class PartnerNoProgress(ValueError):
    """连续读取没有新增证据，交由任务层保留进度并暂停。"""


class PartnerEvidenceLimit(ValueError):
    """完整证据达到持久化硬界，不能静默截断后继续宣称已读。"""


class PartnerTools:
    """每轮授权独立实例；只使用短数据库会话，解析和渲染时不占会话。"""

    def __init__(self, *, engine, root: str, scope: dict, guard: Callable[[], None],
                 emit: Callable[[str, str], None] | None = None):
        from backend.app.config import settings
        self.engine = engine
        self.root = str(Path(root).resolve())
        self.configured_root = str(Path(settings.library_path).absolute())
        self.scope = deepcopy(scope)
        self.guard = guard
        self.emit = emit or (lambda kind, message: None)
        self.sources: list[dict] = []
        # 实际暴露给模型的资料依赖与可引用原文来源分开，搜索元数据不能伪装成原文。
        self.exposed_document_ids: set[str] = set()
        self._source_keys: dict[tuple, str] = {}
        self._parsed: OrderedDict[str, dict] = OrderedDict()
        self._evidence: OrderedDict[str, dict] = OrderedDict()
        self._seen_evidence: set[str] = set()
        self._read_ranges: list[dict] = []
        self._no_progress_count = 0
        self._image_usage = {'count': 0, 'bytes': 0}
        self._next_source_number = 1
        self.rebase_notes: list[str] = []

    @property
    def progress(self):
        return {'read_units': sum(':text:' in key for key in self._seen_evidence),
                'visual_pages': sum(':visual:' in key for key in self._seen_evidence),
                'embedded_images': sum(':embedded:' in key for key in self._seen_evidence),
                'image_usage': dict(self._image_usage),
                'text_characters': sum(len(item['text']) for item in self._evidence.values()),
                'evidence_count': len(self._evidence), 'no_progress_count': self._no_progress_count,
                'read_ranges': deepcopy(self._read_ranges[-30:]), 'range_count': len(self._read_ranges)}

    def _scope_fingerprint(self, scope=None):
        from backend.app.services.index_content import PARSER_VERSION
        return hashlib.sha256(json.dumps({'scope': self.scope if scope is None else scope, 'parser': PARSER_VERSION},
            sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()

    def export_checkpoint(self):
        """只保存实际读过的完整原文和游标，图片字节永不落入检查点。"""
        value = {'schema_version': 3, 'scope_fingerprint': self._scope_fingerprint(),
                 'next_source_number': self._next_source_number,
                 'sources': deepcopy(self.sources), 'evidence': deepcopy(list(self._evidence.values())),
                 'read_ranges': deepcopy(self._read_ranges),
                 'seen_evidence': sorted(self._seen_evidence),
                 'exposed_document_ids': sorted(self.exposed_document_ids),
                 'no_progress_count': self._no_progress_count, 'image_usage': dict(self._image_usage)}
        if len(json.dumps(value, ensure_ascii=False).encode()) > MAX_CHECKPOINT_BYTES:
            raise PartnerEvidenceLimit('已读证据进度超过保存上限，请保留成果后缩小资料范围。')
        return value

    def _validate_checkpoint(self, value, scope):
        """先核对原冻结范围及完整结构；过滤不能替无效旧检查点洗白。"""
        fields = {'schema_version', 'scope_fingerprint', 'sources', 'evidence', 'read_ranges',
                  'seen_evidence', 'exposed_document_ids', 'no_progress_count'}
        error = '任务进度检查点无效，请重新准备。'
        try:
            schema_version = value.get('schema_version') if isinstance(value, dict) else None
            if schema_version in {2, 3}:
                fields.add('image_usage')
            if schema_version == 3:
                fields.add('next_source_number')
            if (not isinstance(value, dict) or set(value) != fields
                    or not isinstance(scope, dict)
                    or type(schema_version) is not int or schema_version not in {1, 2, 3}
                    or len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()) > MAX_CHECKPOINT_BYTES):
                raise ValueError(error)
            image_usage = value.get('image_usage', {'count': 0, 'bytes': 0})
            if (not isinstance(image_usage, dict) or set(image_usage) != {'count', 'bytes'}
                    or type(image_usage['count']) is not int or not 0 <= image_usage['count'] <= MAX_TASK_IMAGES
                    or type(image_usage['bytes']) is not int or not 0 <= image_usage['bytes'] <= MAX_TASK_IMAGE_BYTES):
                raise ValueError(error)
            if value['scope_fingerprint'] != self._scope_fingerprint(scope):
                raise ValueError('任务进度的授权范围或解析规则已变化，请重新准备。')
            if (not isinstance(value['sources'], list) or len(value['sources']) > MAX_CHECKPOINT_SOURCES
                    or not isinstance(value['evidence'], list)
                    or not isinstance(value['read_ranges'], list) or len(value['read_ranges']) > MAX_READ_RANGES
                    or not isinstance(value['seen_evidence'], list)
                    or not isinstance(value['exposed_document_ids'], list)
                    or type(value['no_progress_count']) is not int
                    or not 0 <= value['no_progress_count'] <= NO_PROGRESS_LIMIT):
                raise ValueError(error)
            if (any(not isinstance(key, str) or not 1 <= len(key) <= 200
                    for key in value['exposed_document_ids'])
                    or len(set(value['exposed_document_ids'])) != len(value['exposed_document_ids'])):
                raise ValueError(error)
            exposed = set(value['exposed_document_ids'])
            if not exposed.issubset(scope):
                raise ValueError('任务进度包含未授权资料。')
            sources, source_keys = {}, {}
            source_fields = {'id', 'source_id', 'document_id', 'name', 'version_id',
                             'version_number', 'content_hash', 'location', 'label', 'excerpt'}
            for number, source in enumerate(value['sources'], 1):
                if not isinstance(source, dict) or set(source) != source_fields:
                    raise ValueError(error)
                source_id = source['id']
                if (not isinstance(source_id, str) or re.fullmatch(r'S[1-9]\d{0,8}', source_id) is None
                        or source_id in sources
                        or (schema_version < 3 and source_id != f'S{number}')
                        or not isinstance(source['document_id'], str)):
                    raise ValueError(error)
                frozen = scope.get(source['document_id'])
                if (not isinstance(frozen, dict) or not frozen or source['document_id'] not in exposed
                        or source['source_id'] != source_id
                        or any(type(source[key]) is not type(frozen.get(key))
                               or source[key] != frozen.get(key) for key in
                               ('name', 'version_id', 'version_number', 'content_hash'))
                        or not _valid_evidence_location(source['location'])
                        or ('member_path' in source['location'] and not _member_source_scope(frozen))
                        or not isinstance(source['label'], str) or len(source['label']) > 500
                        or not isinstance(source['excerpt'], str) or len(source['excerpt']) > 300):
                    raise ValueError(error)
                key = (source['document_id'], source['version_id'], json.dumps(source['location'], sort_keys=True))
                if key in source_keys:
                    raise ValueError(error)
                sources[source_id], source_keys[key] = source, source_id
            next_source_number = value.get('next_source_number', len(sources) + 1)
            if (type(next_source_number) is not int
                    or not max((int(key[1:]) for key in sources), default=0) < next_source_number <= MAX_SOURCE_NUMBER):
                raise ValueError(error)
            evidence = OrderedDict()
            for item in value['evidence']:
                if (not isinstance(item, dict) or set(item) != {'source_id', 'document_id', 'text', 'kind', 'location'}
                        or item['source_id'] not in sources or item['source_id'] in evidence
                        or item['document_id'] != sources[item['source_id']]['document_id']
                        or item['location'] != sources[item['source_id']]['location']
                        or item['kind'] not in {'text', 'table', 'visual'}
                        or not isinstance(item['text'], str) or len(item['text']) > MAX_TEXT_RETURN
                        or (item['kind'] == 'visual' and item['text'])):
                    raise ValueError(error)
                evidence[item['source_id']] = deepcopy(item)
            if (set(evidence) != set(sources)
                    or sum(len(item['text']) for item in evidence.values()) > MAX_CHECKPOINT_TEXT
                    or any(not isinstance(key, str) or len(key) > 200
                           or key.split(':', 1)[0] not in exposed
                           or re.fullmatch(r'[^:]+:(?:member:[a-f0-9]{64}:)?(?:text:u\d{6}|visual:[1-9]\d*|embedded:image-[1-9]\d*|(?:search|card):[a-f0-9]{64}|overview)', key) is None
                           for key in value['seen_evidence'])
                    or len(set(value['seen_evidence'])) != len(value['seen_evidence'])
                    or any(not _valid_read_range(item, exposed) for item in value['read_ranges'])):
                raise ValueError(error)
        except (TypeError, KeyError, OverflowError):
            raise ValueError(error) from None
        return {'sources': deepcopy(value['sources']), 'source_keys': source_keys,
                'evidence': evidence, 'exposed': exposed, 'seen': set(value['seen_evidence']),
                'read_ranges': deepcopy(value['read_ranges']), 'image_usage': dict(image_usage),
                'next_source_number': next_source_number}

    def restore_checkpoint(self, value):
        """恢复本轮冻结版本的证据；从不从检查点接受路径或新的资料权限。"""
        restored = self._validate_checkpoint(value, self.scope)
        exposed = restored['exposed']
        for document_id in exposed:
            self._assert_current(document_id)
        self.sources = restored['sources']
        self._source_keys, self._evidence = restored['source_keys'], restored['evidence']
        self.exposed_document_ids, self._seen_evidence = exposed, restored['seen']
        self._read_ranges = restored['read_ranges']
        self._image_usage = restored['image_usage']
        self._next_source_number = restored['next_source_number']
        # 继续由用户明确授权，给模型重新选择策略或重读未持久化图片的机会。
        self._no_progress_count = 0

    def rebase_checkpoint(self, value, old_scope):
        """普通新轮只继承仍授权同一版本的文字；暂停续跑直接用 restore。"""
        self.rebase_notes = []
        restored = self._validate_checkpoint(value, old_scope)
        matching = {key for key in old_scope if key in self.scope
                    and json.dumps(old_scope[key], sort_keys=True, allow_nan=False)
                    == json.dumps(self.scope[key], sort_keys=True, allow_nan=False)}
        candidates = [item for item in restored['evidence'].values()
                      if item['document_id'] in matching and item['kind'] in {'text', 'table'}
                      and item['text'].strip()]
        selected, characters = [], 0
        for item in reversed(candidates):
            if len(selected) >= MAX_CONTEXT_SOURCES or characters + len(item['text']) > MAX_CONTEXT_TEXT:
                break
            selected.append(item)
            characters += len(item['text'])
        selected.reverse()
        selected_ids = {item['source_id'] for item in selected}
        exposed = {item['document_id'] for item in selected}
        # 旧格式没有来源到原文单元的逐项映射。某文档被部分省略时保留原文，
        # 但清掉该文档整体游标和已读声明，避免把省略部分算成已带入新轮。
        omitted_docs = {item['document_id'] for item in restored['evidence'].values()
                        if item['kind'] in {'text', 'table'} and item['source_id'] not in selected_ids}
        complete_docs = exposed - omitted_docs
        ranges = [item for item in restored['read_ranges']
                  if item['kind'] in {'text', 'table'} and item['document_id'] in complete_docs]
        seen = [key for key in restored['seen']
                if ':text:' in key and key.split(':', 1)[0] in complete_docs]
        if len(candidates) > len(selected):
            self.rebase_notes.append('新轮已读证据容量为最近 100 个来源、60000 字；较早的完整原文单元已省略，相关资料的整体游标已清除，请按保留出处重新定位。')
        if any(item['kind'] == 'visual' for item in restored['evidence'].values()):
            self.rebase_notes.append('前轮图片和原页字节未带入本轮，视觉内容需重新读取，不能据前轮已读记录确认细节。')
        if set(old_scope) - matching:
            self.rebase_notes.append('授权范围或冻结资料信息已变化，对应旧资料证据已省略。')
        result = {'schema_version': 3, 'scope_fingerprint': self._scope_fingerprint(),
                  'next_source_number': restored['next_source_number'],
                  'sources': [source for source in restored['sources'] if source['id'] in selected_ids],
                  'evidence': deepcopy(selected), 'read_ranges': ranges, 'seen_evidence': sorted(seen),
                  'exposed_document_ids': sorted(exposed), 'no_progress_count': 0,
                  'image_usage': {'count': 0, 'bytes': 0}}
        self._validate_checkpoint(result, self.scope)
        return result

    def _record(self, document_id, records, unit_keys, read_range):
        """在返回模型前原子记录完整证据，容量不足不留下未送达的来源。"""
        before = (deepcopy(self.sources), self._source_keys.copy(), deepcopy(self._evidence),
                  self._seen_evidence.copy(), deepcopy(self._read_ranges), self._no_progress_count,
                  self._next_source_number)
        try:
            result = []
            for record in records:
                source_id = self._source(document_id, record['location'], record['label'], record['text'])
                if source_id not in self._evidence:
                    self._evidence[source_id] = {'source_id': source_id, 'document_id': document_id,
                        'text': record['text'], 'kind': record['kind'], 'location': deepcopy(record['location'])}
                elif record['text'] and not self._evidence[source_id]['text']:
                    # 原页与同页文字共用位置来源；后读文字必须保留全文，不能被空的图像证据吞掉。
                    self._evidence[source_id].update(text=record['text'], kind=record['kind'])
                    next(source for source in self.sources if source['id'] == source_id)['excerpt'] = record['text'][:300]
                result.append(source_id)
            if (len(self.sources) > MAX_CHECKPOINT_SOURCES
                    or sum(len(item['text']) for item in self._evidence.values()) > MAX_CHECKPOINT_TEXT):
                raise PartnerEvidenceLimit('已读证据达到保存上限，本次内容未继续读取；请保留成果后缩小范围。')
            new_keys = set(unit_keys) - self._seen_evidence
            self._seen_evidence.update(unit_keys)
            self._no_progress_count = 0 if new_keys else min(NO_PROGRESS_LIMIT, self._no_progress_count + 1)
            if read_range not in self._read_ranges:
                if len(self._read_ranges) >= MAX_READ_RANGES:
                    raise PartnerEvidenceLimit('阅读范围进度达到保存上限，请保留成果后开始新任务。')
                self._read_ranges.append(deepcopy(read_range))
            self.export_checkpoint()  # 包括 UTF-8 字节量、范围和来源的总容量检查。
        except PartnerEvidenceLimit:
            (self.sources, self._source_keys, self._evidence, self._seen_evidence,
             self._read_ranges, self._no_progress_count, self._next_source_number) = before
            raise
        if self._no_progress_count >= NO_PROGRESS_LIMIT:
            raise PartnerNoProgress('连续 3 次读取或检索没有新增证据，已保留进度；请调整范围或读取下一位置，避免重复消耗。')
        return result

    def _check(self):
        from backend.app import database
        from backend.app.config import settings
        self.guard()
        if (database.engine is not self.engine or not settings.library_path
                or str(Path(settings.library_path).resolve()) != self.root):
            raise ValueError('文件库已变化，请重新授权本轮任务。')

    def _session(self):
        from backend.app.database import TrackedSession
        return TrackedSession(bind=self.engine, autoflush=False, autocommit=False)

    def _document(self, db, document_id):
        from backend.app.models import Document, Version
        from backend.app.services.indexing import metadata_fingerprint
        frozen = self.scope.get(document_id)
        if frozen is None:
            raise ValueError('该资料不在本轮授权范围内。')
        doc = db.get(Document, document_id)
        version = db.get(Version, frozen['version_id']) if frozen.get('version_id') else None
        if (doc is None or doc.status not in ('pending', 'organized') or version is None
                or version.document_id != document_id or doc.current_version_id != version.id
                or version.sha256_hash != frozen.get('content_hash')
                or metadata_fingerprint(doc) != frozen.get('metadata_hash')):
            raise ValueError('资料版本、状态或整理信息已变化，本轮授权已失效，请重新准备。')
        return doc, version

    def _assert_current(self, document_id):
        self._check()
        with self._session() as db:
            self._document(db, document_id)
        self._check()

    def _source(self, document_id, location, label, excerpt=''):
        self.exposed_document_ids.add(document_id)
        frozen = self.scope[document_id]
        key = (document_id, frozen['version_id'], json.dumps(location, sort_keys=True))
        source_id = self._source_keys.get(key)
        if source_id is None:
            if self._next_source_number >= MAX_SOURCE_NUMBER:
                raise PartnerEvidenceLimit('来源编号达到保存上限，请保留成果后开始新任务。')
            source_id = f'S{self._next_source_number}'
            self._next_source_number += 1
            self._source_keys[key] = source_id
            self.sources.append({'id': source_id, 'source_id': source_id,
                'document_id': document_id, 'name': frozen['name'],
                'version_id': frozen['version_id'], 'version_number': frozen.get('version_number'),
                'content_hash': frozen['content_hash'], 'location': deepcopy(location),
                'label': label, 'excerpt': excerpt[:300]})
        return source_id

    def search_documents(self, query: str, limit: int = 8, offset: int = 0) -> dict:
        """在授权当前版本中按相关性找候选；命中位置不产生阅读证据。"""
        from backend.app.models import Document
        from backend.app.services import search_retrieval
        self._check()
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 120:
            raise ValueError('搜索关键词须为 1 至 120 个字符；可更换关键词继续查找。')
        if type(limit) is not int or not 1 <= limit <= 10:
            raise ValueError('每次搜索最多返回 10 份资料。')
        if type(offset) is not int or not 0 <= offset <= 10000:
            raise ValueError('搜索起始位置须为 0 至 10000 的整数。')
        query = query.strip()
        if not self.scope:
            return {'query': query, 'documents': [], 'has_more': False, 'scope_count': 0,
                    'offset': offset, 'next_offset': offset}
        results = []
        with self._session() as db:
            self._check()
            ranked = search_retrieval.search(db, query, self.scope, limit=limit, offset=offset)
            for candidate in ranked['items']:
                doc, version = self._document(db, candidate['document_id'])
                passages = []
                for value in candidate['passages']:
                    passage = deepcopy(value)
                    ordinal = passage.get('ordinal')
                    if passage.get('source_kind') == 'content' and type(ordinal) is int and ordinal >= 0:
                        passage['next_call'] = {'tool': 'read_document', 'arguments': {
                            'document_id': doc.id, 'offset': ordinal, 'limit': 3}}
                    passages.append(passage)
                snippet = next((part['excerpt'] for part in passages
                                if part.get('source_kind') == 'content'), '')
                from backend.app.services import document_cards
                card_status = document_cards.status(db, doc.id)
                card = card_status.get('card') if card_status['state'] in {'ready', 'partial'} else None
                content = card['content'] if card else {}
                card_text = '\n'.join([content.get('summary', ''), content.get('document_type', ''),
                    *content.get('topics', []), *content.get('search_terms', []), *content.get('uses', []),
                    *(entry['summary'] for entry in content.get('sections', [])),
                    *(entry['value'] for entry in content.get('facts', []))])
                results.append({'document_id': doc.id, 'name': doc.name,
                    'version_id': version.id, 'version_number': version.version_number,
                    'is_folder': doc.is_folder, 'status': doc.status,
                    'tags': [tag.name for tag in doc.tags[:12]],
                    'description': (doc.description or '')[:300], 'excerpt': snippet,
                    'card_summary': content.get('summary', '')[:500],
                    'card_state': card_status['state'],
                    'card_match': bool(card and query.casefold() in card_text.casefold()),
                    'index_state': candidate.get('coverage', {}).get('state', 'unavailable'),
                    'matched_passages': passages, 'match_channels': candidate['channels'],
                    'relevance': candidate['relevance'], 'coverage': candidate.get('coverage', {})})
        self._check()
        self.exposed_document_ids.update(item['document_id'] for item in results)
        self._record(None, [], [f'{item["document_id"]}:search:' + hashlib.sha256(
            json.dumps(item, sort_keys=True, ensure_ascii=False).encode()).hexdigest() for item in results],
                     {'document_id': None, 'kind': 'search', 'query': query,
                      'offset': offset, 'next_offset': offset + len(results),
                      'document_ids': [item['document_id'] for item in results]})
        self.emit('search', f'检索“{query}”，找到 {len(results)} 份候选资料')
        return {'query': query, 'documents': results, 'has_more': ranked['has_more'],
                'scope_count': len(self.scope), 'offset': offset, 'next_offset': ranked['next_offset'],
                'total': ranked['total'], 'retrieval_mode': ranked['mode'],
                'note': '按相关性返回授权当前版本的候选及命中位置；当前使用关键词与资料卡线索，'
                        '未启用向量语义检索。命中摘录不产生本轮原文来源编号；请按 next_call 阅读核对。'
                        '未命中不代表资料不存在，未解析内容可能遗漏。'}

    def read_document_card(self, document_id):
        """卡片只帮助定位；历史生成时的来源编号不能成为本轮原文证据。"""
        from backend.app.services import document_cards
        self._check()
        with self._session() as db:
            doc, version = self._document(db, document_id)
            status = document_cards.status(db, doc.id)
            card = status.get('card') if status['state'] in {'ready', 'partial'} else None
            result = {'document_id': doc.id, 'version_id': version.id,
                      'origin': 'ai_derived', 'state': status['state'], 'card': None,
                      'annotation': status.get('annotation'),
                      'note': '资料卡为 AI 解读与检索线索；人工补充单独标明。请读取原文核实具体结论并取得本轮引用。'}
            if card:
                content = deepcopy(card['content'])
                sources = {item.get('id', item.get('source_id')): item for item in card['sources']}
                for entry in [content, *content.get('sections', []), *content.get('facts', [])]:
                    identifiers = entry.pop('source_ids', [])
                    entry['evidence_locations'] = [{'label': sources[key].get('label', ''),
                                                   'location': sources[key].get('location', {})}
                                                  for key in identifiers if key in sources]
                result.update(card=content, coverage=card['coverage'], revision=card['revision'])
        self._assert_current(document_id)
        self.exposed_document_ids.add(document_id)
        digest = hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        self._record(document_id, [], [f'{document_id}:card:{digest}'],
                     {'document_id': document_id, 'kind': 'card', 'revision': result.get('revision')})
        self.emit('status', '已查看资料卡，正在定位相关原文。')
        return result

    def _snapshot(self, document_id):
        from backend.app.services.indexing import _snapshot_bytes
        self._check()
        with self._session() as db:
            self._check()
            doc, version = self._document(db, document_id)
            if doc.is_folder:
                raise ValueError('本轮暂不递归读取文件夹对象，请选择其中单独收录的文件。')
            job = {'binding': (str(self.engine.url), self.root, id(self.engine)),
                   'configured_root': self.configured_root, 'file_path': version.file_path,
                   'content_hash': version.sha256_hash}
            filename = version.original_filename
        try:
            data = _snapshot_bytes(job)
        except (OSError, ValueError) as exc:
            if str(exc) == 'input_limit':
                raise ValueError('资料超过 32 MiB 读取上限，本次未读取内容。') from None
            raise ValueError('已保存版本快照不可读取或校验失败，本次未读取内容。') from None
        self._assert_current(document_id)
        self.exposed_document_ids.add(document_id)
        return data, filename

    def _parsed_document(self, document_id):
        from backend.app.services import index_content
        data, filename = self._snapshot(document_id)
        result = self._parsed.get(document_id)
        if result is None:
            result = index_content.extract_index(data, filename)
            index_content.validate_index(result)
            self._parsed[document_id] = result
            if len(self._parsed) > 3:
                self._parsed.popitem(last=False)
        self._assert_current(document_id)
        return result, filename

    def document_overview(self, document_id: str) -> dict:
        """先查看结构、工作表/行列范围和已读游标；结构不等同已经读完整正文。"""
        result, filename = self._parsed_document(document_id)
        if Path(filename).suffix.lower() in IMAGE_SUFFIXES:
            data, filename = self._snapshot(document_id)
            metadata = _probe_image_isolated(data, Path(filename).suffix.lower(), guard=self._check)
            self._assert_current(document_id)
            self._record(document_id, [], [f'{document_id}:overview'],
                         {'document_id': document_id, 'kind': 'overview', 'unit_count': 0})
            frozen = self.scope[document_id]
            frames = metadata['frame_count']
            self.emit('overview', f'《{frozen["name"]}》图片：{metadata["width"]} × {metadata["height"]}'
                      + (f'，{frames} 帧' if frames is not None else '，首图可读取'))
            return {'document_id': document_id, 'name': frozen['name'],
                'version_id': frozen['version_id'], 'version_number': frozen.get('version_number'),
                'format': 'image', 'image': metadata, 'unit_count': 0, 'sheets': [],
                'visual_assets': [], 'visual_asset_count': 0, 'visual_state': 'ready',
                'visual_notes': metadata['notes'], 'coverage': result['coverage'],
                'state': 'ready', 'gaps': [], 'gap_count': 0, 'text_index_state': result['state'],
                'notes': metadata['notes'], 'progress': self.progress,
                'next_call': {'tool': 'read_visual', 'arguments': {'document_id': document_id, 'pages': [1]}},
                'note': '这里只确认图片格式、尺寸及可知帧数，尚未阅读图像内容。请调用 read_visual 获取真实像素；文字索引为空不代表图片没有内容。'}
        sheets = {}
        if result['coverage']['unit'] == 'cells':
            for unit in result['units']:
                location = unit['location']
                key = location['sheet_index']
                if key not in sheets:
                    sheets[key] = {'sheet': location['sheet'], 'sheet_index': key,
                        'row_start': location['row'], 'row_end': location['row'],
                        'column_start': location['column'], 'column_end': location['column'],
                        'indexed_cells': 0}
                sheet = sheets[key]
                sheet['row_start'] = min(sheet['row_start'], location['row'])
                sheet['row_end'] = max(sheet['row_end'], location['row'])
                sheet['column_start'] = min(sheet['column_start'], location['column'])
                sheet['column_end'] = max(sheet['column_end'], location['column'])
            for key, sheet in sheets.items():
                sheet['indexed_cells'] = len({unit['location']['cell'] for unit in result['units']
                                             if unit['location']['sheet_index'] == key})
        from backend.app.services.document_visuals import discover_visual_assets
        data, filename = self._snapshot(document_id)
        try:
            visual_assets, visual_notes = discover_visual_assets(data, filename)
            visual_state = 'partial' if any('上限' in note or '未读取' in note or '尚未' in note for note in visual_notes) else 'ready'
        except ValueError as exc:
            visual_assets, visual_notes, visual_state = [], [str(exc)], 'failed'
        self._assert_current(document_id)
        self._record(document_id, [], [f'{document_id}:overview'],
                     {'document_id': document_id, 'kind': 'overview', 'unit_count': len(result['units'])})
        frozen = self.scope[document_id]
        structure = (f'{len(sheets)} 个有文字工作表，' if sheets else '') + f'{len(result["units"])} 个原文单元'
        if visual_assets:
            structure += f'，{len(visual_assets)} 张可读取内嵌图片'
        self.emit('overview', f'《{frozen["name"]}》结构：{structure}')
        return {'document_id': document_id, 'name': frozen['name'],
            'version_id': frozen['version_id'], 'version_number': frozen.get('version_number'),
            'format': 'spreadsheet' if result['coverage']['unit'] == 'cells' else 'text',
            'sheets': list(sheets.values()), 'unit_count': len(result['units']),
            'visual_assets': [asset.public() for asset in visual_assets],
            'visual_asset_count': len(visual_assets), 'visual_state': visual_state,
            'visual_notes': visual_notes,
            'coverage': result['coverage'], 'state': result['state'], 'gaps': _public_gaps(result),
            'gap_count': len(result['gaps']), 'notes': result['notes'], 'progress': self.progress,
            'note': '这里只查看结构，正文尚需读取；工作表范围只包含解析出文字的部分，空白表或未解析内容可能未列出。表格使用read_table，文字使用read_document；文档嵌图用read_embedded_images及这里实际返回的image_id；PDF或独立图片使用read_visual。图像列表达到上限时余下内容尚未列出，不代表全文已读。'}

    def read_document(self, document_id: str, offset: int = 0, limit: int = 60) -> dict:
        """连续读取至多100个原文单元和16000字；表格优先调用read_table。"""
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('原文读取位置须非负，每次读取 1 至 100 个单元。')
        result, _ = self._parsed_document(document_id)
        units, characters = [], 0
        for original in result['units'][offset:offset + limit]:
            if characters + len(original['text']) > MAX_TEXT_RETURN:
                break
            characters += len(original['text'])
            unit = deepcopy(original)
            units.append(unit)
        source_ids = self._record(document_id,
            [{'kind': unit['kind'], 'text': unit['text'], 'location': unit['location'], 'label': unit['label']}
             for unit in units], [f'{document_id}:text:{unit["id"]}' for unit in units],
            {'document_id': document_id, 'kind': 'text', 'offset': offset,
             'next_offset': offset + len(units), 'unit_count': len(result['units'])})
        for unit, source_id in zip(units, source_ids):
            unit['source_id'] = source_id
        frozen = self.scope[document_id]
        self.emit('read', f'阅读《{frozen["name"]}》原文位置 {offset}–{offset + len(units)} / {len(result["units"])}，下次从 {offset + len(units)} 继续')
        return {'document_id': document_id, 'name': frozen['name'],
            'version_id': frozen['version_id'], 'version_number': frozen.get('version_number'),
            'content_hash': frozen['content_hash'], 'source': 'saved_version',
            'state': result['state'], 'coverage': result['coverage'],
            'gaps': _public_gaps(result), 'gap_count': len(result['gaps']), 'notes': result['notes'],
            'units': units, 'offset': offset, 'returned_units': len(units),
            'unit_count': len(result['units']), 'has_more': offset + len(units) < len(result['units']),
            'next_offset': offset + len(units),
            'progress': self.progress,
            'note': '覆盖统计表示本地文字解析情况；本次只阅读返回的原文单元。图纸、图片及视觉关系需另外调用 read_visual 读取原页。'}

    def read_table(self, document_id: str, sheet: str | None = None, start_row: int = 1,
                   row_count: int = 80, offset: int = 0) -> dict:
        """按工作表连续行块读取Excel；next_call处理超宽/超长行，不丢弃单元格。"""
        if (type(start_row) is not int or not 1 <= start_row <= 1_048_576
                or type(row_count) is not int or not 1 <= row_count <= 200
                or type(offset) is not int or not 0 <= offset <= 5000
                or (sheet is not None and (not isinstance(sheet, str) or not 1 <= len(sheet) <= 31))):
            raise ValueError('表格读取须指定有效工作表、起始行和 1 至 200 行，原文偏移须为非负整数。')
        result, _ = self._parsed_document(document_id)
        if result['coverage']['unit'] != 'cells':
            raise ValueError('本资料不提供单元格行块；请使用 read_document 或图像读取工具。')
        names = list(dict.fromkeys(unit['location']['sheet'] for unit in result['units']))
        if not names:
            raise ValueError('该工作簿暂无可读取文字，请查看结构与解析缺口，不能视为已完整阅读。')
        sheet = names[0] if sheet is None else sheet
        if sheet not in names:
            raise ValueError('未找到该工作表，请先查看 document_overview 返回的工作表名称。')
        sheet_units = [unit for unit in result['units'] if unit['location']['sheet'] == sheet]
        candidates = [unit for unit in sheet_units
                      if start_row <= unit['location']['row'] < start_row + row_count]
        if offset > len(candidates):
            raise ValueError('原文偏移超出本行块范围，请使用上次返回的 next_call。')

        # 开头三行作为上下文，名称明确为参考，不推断其必然是表头。
        first_row = min(unit['location']['row'] for unit in sheet_units)
        header_units, header_size = [], 0
        for unit in sheet_units:
            if unit['location']['row'] >= first_row + 3:
                break
            part = _cell_text(unit)
            if header_size + len(part) + 3 > 2000:
                break
            header_size += len(part) + 3
            header_units.append(unit)
        separate_header = bool(header_units and start_row > first_row + 2)
        budget = MAX_TEXT_RETURN - (header_size if separate_header else 0)
        selected, size = [], 0
        for unit in candidates[offset:]:
            part = _cell_text(unit)
            if size + len(part) + 3 > budget:
                break
            selected.append(unit)
            size += len(part) + 3

        records = []
        if selected:
            records.append(_table_record(selected, start_row=start_row, offset=offset))
        if separate_header:
            records.append(_table_record(header_units, start_row=first_row, offset=0,
                                         label_prefix='开头行参考（未推断表头）'))
        next_offset = offset + len(selected)
        if next_offset < len(candidates):
            next_call = {'document_id': document_id, 'sheet': sheet, 'start_row': start_row,
                         'row_count': row_count, 'offset': next_offset}
            next_row = candidates[next_offset]['location']['row']
        else:
            later = next((unit for unit in sheet_units
                          if unit['location']['row'] >= start_row + row_count), None)
            next_row = later['location']['row'] if later else None
            next_call = ({'document_id': document_id, 'sheet': sheet, 'start_row': next_row,
                          'row_count': row_count, 'offset': 0} if later else None)
        read_start = min((unit['location']['row'] for unit in selected), default=start_row)
        read_end = max((unit['location']['row'] for unit in selected), default=start_row - 1)
        source_ids = self._record(document_id, records,
            [f'{document_id}:text:{unit["id"]}' for unit in [*selected, *(header_units if separate_header else [])]],
            {'document_id': document_id, 'kind': 'table', 'sheet': sheet,
             'row_start': read_start, 'row_end': read_end, 'offset': offset,
             'next_offset': next_offset, 'next_call': deepcopy(next_call)})
        units = []
        if selected:
            units.append({**records[0], 'source_id': source_ids[0]})
        header_context = []
        if separate_header:
            header_context.append({**records[-1], 'source_id': source_ids[-1]})
        elif header_units and selected and header_units[0] in selected:
            # 只是同一已读块的行参考，完整证据只保存一次。
            included = [unit for unit in header_units if unit in selected]
            header_context.append({'text': '\n'.join(_cell_text(unit) for unit in included),
                                   'source_id': source_ids[0], 'label': '开头行参考（未推断表头）'})
        frozen = self.scope[document_id]
        self.emit('read', f'阅读《{frozen["name"]}》工作表“{sheet}”第 {read_start}–{read_end} 行，'
                  f'{len(selected)} 个原文单元；' + (f'下次从第 {next_row} 行继续' if next_call else '本表已解析文字已读至末尾'))
        return {'document_id': document_id, 'name': frozen['name'],
            'version_id': frozen['version_id'], 'version_number': frozen.get('version_number'),
            'content_hash': frozen['content_hash'], 'source': 'saved_version', 'sheet': sheet,
            'row_start': read_start, 'row_end': read_end, 'next_row': next_row,
            'offset': offset, 'next_offset': next_offset, 'next_call': next_call,
            'has_more': next_call is not None, 'returned_cells': len({unit['location']['cell'] for unit in selected}),
            'returned_units': len(selected), 'sheet_unit_count': len(sheet_units),
            'read_cells': list(dict.fromkeys(unit['location']['cell'] for unit in selected)),
            'units': units, 'header_context': header_context, 'progress': self.progress,
            'state': result['state'], 'coverage': result['coverage'], 'gaps': _public_gaps(result),
            'gap_count': len(result['gaps']), 'notes': [*result['notes'],
                '仅列出存有文字/缓存值的原始单元格，保留坐标；未还原合并单元格、样式及图表，空白不自动填充；未重新计算公式。'],
            'note': '继续阅读请原样使用 next_call；开头行只是上下文参考，不代表已确认表头。统计仅针对解析文字，缺口仍需核对原件。'}

    def read_visual(self, document_id: str, pages: list[int] | None = None) -> VisualResult:
        """读取 PDF 指定原页或图片，文字丰富的 PDF 页面同样渲染。"""
        pages = [1] if pages is None else pages
        _validate_pages(pages)
        data, filename = self._snapshot(document_id)
        suffix = Path(filename).suffix.lower()
        if suffix != '.pdf' and suffix not in _IMAGE_SUFFIXES:
            raise ValueError('当前视觉读取支持 PDF 及 PNG、JPEG、GIF、WebP、TIFF、AVIF、JPEG2000 等图片；HEIC/HEIF/SVG 需本机解码支持。')
        self._check()
        rendered = _render_isolated(data, suffix, pages, guard=self._check)
        self._assert_current(document_id)
        decoded = [base64.b64decode(item['data'], validate=True) for item in rendered['images']]
        self._check_image_budget(decoded)
        images, descriptions = [], []
        records = [{'kind': 'visual', 'location': {'page': item['page']}, 'text': '',
                    'label': f'第 {item["page"]} 页原图' if suffix == '.pdf' else f'图片第 {item["page"]} 帧'}
                   for item in rendered['images']]
        source_ids = self._record(document_id, records,
            [f'{document_id}:visual:{page}' for page in pages],
            {'document_id': document_id, 'kind': 'visual', 'pages': pages,
             'page_count': rendered['page_count'], 'images_need_reread_after_resume': True})
        self._consume_images(decoded)
        for item, raw, source_id in zip(rendered['images'], decoded, source_ids):
            page = item['page']
            label = f'第 {page} 页原图' if suffix == '.pdf' else f'图片第 {page} 帧'
            media_type = item.get('media_type', 'image/png')
            images.append(VisualImage(raw, media_type, source_id, page))
            descriptions.append({key: item[key] for key in ('page', 'width', 'height')}
                                | {'source_id': source_id, 'label': label, 'media_type': media_type})
        frozen = self.scope[document_id]
        self.emit('visual', f'查看《{frozen["name"]}》原图页：{", ".join(map(str, pages))}')
        return VisualResult(metadata={'document_id': document_id, 'name': frozen['name'],
            'version_id': frozen['version_id'], 'version_number': frozen.get('version_number'),
            'content_hash': frozen['content_hash'], 'source': 'saved_version',
            'page_count': rendered['page_count'], 'read_pages': pages,
            'images': descriptions, 'notes': rendered['notes'],
            'progress': self.progress,
            'note': '仅阅读所列页面；图片可能经过缩放，细小文字与线路需进一步核对原件。'}, images=images)

    def _check_image_budget(self, images):
        if (self._image_usage['count'] + len(images) > MAX_TASK_IMAGES
                or self._image_usage['bytes'] + sum(map(len, images)) > MAX_TASK_IMAGE_BYTES):
            raise PartnerEvidenceLimit('本任务图片读取已达到 24 张或 12 MiB 总量上限；已读证据保留，请缩小资料范围后开始新任务。')

    def _consume_images(self, images):
        self._image_usage['count'] += len(images)
        self._image_usage['bytes'] += sum(map(len, images))

    def read_embedded_images(self, document_id: str, image_ids: list[str]) -> VisualResult:
        """读取结构工具列出的内部嵌图；不接受路径，不将图片编号伪称页码。"""
        if (not isinstance(image_ids, list) or not 1 <= len(image_ids) <= MAX_VISUAL_PAGES
                or any(not isinstance(item, str) or re.fullmatch(r'image-[1-9]\d{0,5}', item) is None
                       for item in image_ids) or len(set(image_ids)) != len(image_ids)):
            raise ValueError('每次须选择 1 至 3 个不重复的嵌入图片 image_id，请先查看资料结构。')
        from backend.app.services.document_visuals import read_asset_bytes
        data, filename = self._snapshot(document_id)
        selected, notes, asset_count = read_asset_bytes(data, filename, image_ids)
        rendered, decoded = [], []
        for asset, raw, suffix in selected:
            self._check()
            result = _render_isolated(raw, suffix, [1], guard=self._check)
            image = result['images'][0]
            rendered.append((asset, image))
            decoded.append(base64.b64decode(image['data'], validate=True))
            notes.extend(note for note in result['notes'] if note not in notes)
        self._assert_current(document_id)
        self._check_image_budget(decoded)
        records = [{'kind': 'visual', 'location': asset.location, 'text': '', 'label': asset.label}
                   for asset, _ in rendered]
        source_ids = self._record(document_id, records,
            [f'{document_id}:embedded:{asset.image_id}' for asset, _ in rendered],
            {'document_id': document_id, 'kind': 'embedded', 'image_ids': list(image_ids),
             'asset_count': asset_count, 'images_need_reread_after_resume': True})
        self._consume_images(decoded)
        images, descriptions = [], []
        for (asset, item), raw, source_id in zip(rendered, decoded, source_ids):
            media_type = item.get('media_type', 'image/png')
            images.append(VisualImage(raw, media_type, source_id, asset.location['image_index']))
            descriptions.append({key: item[key] for key in ('width', 'height')} |
                {'image_id': asset.image_id, 'source_id': source_id, 'label': asset.label,
                 'location': dict(asset.location), 'media_type': media_type})
        frozen = self.scope[document_id]
        self.emit('visual', f'查看《{frozen["name"]}》嵌入图片：{", ".join(image_ids)}')
        return VisualResult(metadata={'document_id': document_id, 'name': frozen['name'],
            'version_id': frozen['version_id'], 'version_number': frozen.get('version_number'),
            'content_hash': frozen['content_hash'], 'source': 'saved_version',
            'read_image_ids': list(image_ids), 'asset_count': asset_count,
            'images': descriptions, 'notes': notes, 'progress': self.progress,
            'note': '仅查看所列内部嵌图与真实锚点；未还原整个页面、工作表或幻灯片布局，图片编号不是文档页码。'}, images=images)


def _public_gaps(result):
    """Package-internal paths are parser details, never model-visible asset IDs."""
    gaps = deepcopy(result['gaps'][:30])
    for gap in gaps:
        location = gap.get('location', {})
        location.pop('part', None)
        if not location:
            location['scope'] = 'file'
    return gaps


def _cell_text(unit):
    location = unit['location']
    suffix = (f'（字符{location["char_start"]}–{location["char_end"]}）'
              if 'char_start' in location else '')
    return f'{location["cell"]}{suffix}={unit["text"]}'


def _member_source_scope(frozen):
    from backend.app.services.object_skills import object_kind
    return (frozen.get('is_folder') is True or
            object_kind(frozen.get('source_filename', frozen.get('name', ''))) == 'archive')


def _valid_member_path(value):
    """快照成员使用相对名称，不接受文件系统路径、控制字符或歧义分隔符。"""
    return (isinstance(value, str) and 1 <= len(value) <= 4000
            and not any(char in value for char in '\\:')
            and not any(unicodedata.category(char).startswith('C') for char in value)
            and all(part not in {'', '.', '..'} for part in value.split('/')))


def _valid_evidence_location(location):
    if not isinstance(location, dict) or not location:
        return False
    if 'member_path' in location:
        if not _valid_member_path(location['member_path']):
            return False
        location = {key: value for key, value in location.items() if key != 'member_path'}
        if not location:
            return False  # 目录名不能作为正文证据。
    numeric = {'line', 'page', 'paragraph', 'table', 'row', 'column', 'sheet_index',
               'image_index',
               'char_start', 'char_end', 'row_start', 'row_end', 'column_start', 'column_end',
               'block_start_row', 'unit_start', 'unit_end'}
    if not set(location).issubset(numeric | {'sheet', 'cell'}):
        return False
    for key, value in location.items():
        if key in numeric and (type(value) is not int or not (0 if key == 'unit_start' else 1) <= value <= 10_000_000):
            return False
        if key == 'sheet' and (not isinstance(value, str) or not 1 <= len(value) <= 31):
            return False
        if key == 'cell' and (not isinstance(value, str) or re.fullmatch(r'[A-Z]{1,3}[1-9]\d{0,6}', value) is None):
            return False
    return True


def _valid_read_range(item, exposed):
    if not isinstance(item, dict):
        return False
    if 'member_path' in item:
        if (not _valid_member_path(item['member_path'])
                or item.get('kind') not in {'overview', 'text', 'table', 'visual', 'embedded'}):
            return False
        item = {key: value for key, value in item.items() if key != 'member_path'}
    kind = item.get('kind')
    fields = {'search': {'query', 'offset', 'next_offset', 'document_ids'},
              'card': {'revision'},
              'overview': {'unit_count'}, 'text': {'offset', 'next_offset', 'unit_count'},
              'table': {'sheet', 'row_start', 'row_end', 'offset', 'next_offset', 'next_call'},
              'visual': {'pages', 'page_count', 'images_need_reread_after_resume'},
              'embedded': {'image_ids', 'asset_count', 'images_need_reread_after_resume'}}
    if kind not in fields or set(item) != {'document_id', 'kind', *fields[kind]}:
        return False
    if kind == 'search':
        return (item['document_id'] is None and isinstance(item['query'], str)
                and 1 <= len(item['query']) <= 120 and isinstance(item['document_ids'], list)
                and len(item['document_ids']) <= 10
                and all(isinstance(key, str) and key in exposed for key in item['document_ids'])
                and all(type(item[key]) is int and 0 <= item[key] <= 10010 for key in ('offset', 'next_offset')))
    if item['document_id'] not in exposed:
        return False
    if kind == 'card':
        return item['revision'] is None or type(item['revision']) is int and item['revision'] >= 1
    for key in {'unit_count', 'offset', 'next_offset', 'row_start', 'row_end', 'page_count'} & set(item):
        if type(item[key]) is not int or not 0 <= item[key] <= 10_000_000:
            return False
    if kind == 'table':
        if not isinstance(item['sheet'], str) or not 1 <= len(item['sheet']) <= 31:
            return False
        call = item['next_call']
        if call is not None and (not isinstance(call, dict)
                or set(call) != {'document_id', 'sheet', 'start_row', 'row_count', 'offset'}
                or call['document_id'] != item['document_id'] or call['sheet'] != item['sheet']
                or any(type(call[key]) is not int for key in ('start_row', 'row_count', 'offset'))
                or not 1 <= call['start_row'] <= 1_048_576 or not 1 <= call['row_count'] <= 200
                or not 0 <= call['offset'] <= 5000):
            return False
    if kind == 'visual':
        return (item['images_need_reread_after_resume'] is True and isinstance(item['pages'], list)
                and 1 <= len(item['pages']) <= MAX_VISUAL_PAGES
                and all(type(page) is int and 1 <= page <= item['page_count'] for page in item['pages']))
    if kind == 'embedded':
        return (item['images_need_reread_after_resume'] is True and type(item['asset_count']) is int
                and 1 <= item['asset_count'] <= 300 and isinstance(item['image_ids'], list)
                and 1 <= len(item['image_ids']) <= MAX_VISUAL_PAGES
                and len(set(item['image_ids'])) == len(item['image_ids'])
                and all(isinstance(identifier, str) and re.fullmatch(r'image-[1-9]\d{0,5}', identifier)
                        and int(identifier[6:]) <= item['asset_count'] for identifier in item['image_ids']))
    return True


def _table_record(units, *, start_row, offset, label_prefix='工作表行块'):
    first = units[0]['location']
    row_start = min(unit['location']['row'] for unit in units)
    row_end = max(unit['location']['row'] for unit in units)
    location = {'sheet': first['sheet'], 'sheet_index': first['sheet_index'],
                'row_start': row_start, 'row_end': row_end,
                'column_start': min(unit['location']['column'] for unit in units),
                'column_end': max(unit['location']['column'] for unit in units),
                'block_start_row': start_row, 'unit_start': offset, 'unit_end': offset + len(units)}
    rows = OrderedDict()
    for unit in units:
        rows.setdefault(unit['location']['row'], []).append(_cell_text(unit))
    return {'kind': 'table', 'text': '\n'.join(' | '.join(cells) for cells in rows.values()),
            'location': location, 'label': f'{first["sheet"]} · {label_prefix}第 {row_start}–{row_end} 行'}


def _validate_pages(pages):
    if (not isinstance(pages, list) or not 1 <= len(pages) <= MAX_VISUAL_PAGES
            or any(type(page) is not int or page < 1 for page in pages)
            or len(set(pages)) != len(pages)):
        raise ValueError('每次须选择 1 至 3 个不重复的正整数页码。')


def _run_render_process(data: bytes, suffix: str, pages: list[int], guard) -> tuple[bytes, int]:
    """原生解析在可终止子进程中运行；永远不把文件路径交给渲染器。"""
    parameters = json.dumps({'suffix': suffix, 'pages': pages})
    command = ([sys.executable, _RENDER_FLAG, parameters] if getattr(sys, 'frozen', False)
               else [sys.executable, '-m', __name__, _RENDER_FLAG, parameters])
    guard()
    # A file-backed, unlinked spool avoids communicate() accumulating unbounded
    # child stdout in parent memory. Both the child and parent enforce its cap.
    with tempfile.TemporaryDirectory(prefix='teamhub-render-') as render_temp, tempfile.TemporaryFile() as output:
        try:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=output,
                                       stderr=subprocess.DEVNULL, start_new_session=True,
                                       env=dict(os.environ, TEAMHUB_RENDER_TEMP=render_temp))
        except OSError:
            raise ValueError('原页解析进程无法启动，请检查本机安装。') from None
        deadline = time.monotonic() + 15
        first = True
        try:
            while True:
                guard()
                if os.fstat(output.fileno()).st_size > MAX_RENDER_OUTPUT_BYTES:
                    raise ValueError('原页解析输出超过安全上限，已停止。')
                if time.monotonic() >= deadline:
                    raise ValueError('原页解析超过 15 秒上限，已停止；请减少页数或简化资料。')
                try:
                    process.communicate(input=data if first else None, timeout=0.2)
                    output.seek(0)
                    result = output.read(MAX_RENDER_OUTPUT_BYTES + 1)
                    if len(result) > MAX_RENDER_OUTPUT_BYTES:
                        raise ValueError('原页解析输出超过安全上限，已停止。')
                    return result, process.returncode
                except subprocess.TimeoutExpired:
                    first = False
        finally:
            if process.poll() is None:
                # Native image helpers inherit this dedicated process group.
                # Cancelling a read must also stop their temporary-file work.
                if hasattr(process, 'pid') and os.name == 'posix':
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    process.kill()
                process.communicate()


def _render_isolated(data: bytes, suffix: str, pages: list[int], *,
                     guard: Callable[[], None] = lambda: None) -> dict:
    output, exit_code = _run_render_process(data, suffix, pages, guard)
    if exit_code != 0 or len(output) > MAX_RENDER_OUTPUT_BYTES:
        raise ValueError('原页解析失败或超过资源上限，本次未返回图片。')
    try:
        result = json.loads(output)
        if not isinstance(result, dict):
            raise ValueError('原页输出无效。')
        if 'error' in result:
            raise ValueError(result['error'])
        if (type(result.get('page_count')) is not int or result['page_count'] < 1
                or not isinstance(result.get('images'), list) or len(result['images']) != len(pages)
                or any(not isinstance(image, dict) for image in result['images'])
                or [image.get('page') for image in result['images']] != pages
                or not isinstance(result.get('notes'), list)
                or any(not isinstance(note, str) for note in result['notes'])):
            raise ValueError('原页输出不完整。')
        pixels = 0
        for item in result['images']:
            if (type(item.get('width')) is not int or type(item.get('height')) is not int
                    or min(item['width'], item['height']) < 1
                    or item['width'] * item['height'] > MAX_PAGE_PIXELS):
                raise ValueError('原页尺寸无效。')
            pixels += item['width'] * item['height']
            decoded = base64.b64decode(item['data'], validate=True)
            if len(decoded) > MAX_IMAGE_BYTES:
                raise ValueError('原页输出超过图片上限。')
            _validate_rendered_image(decoded, item)
        if pixels > MAX_VISUAL_PIXELS:
            raise ValueError('原页输出超过总像素上限。')
        return result
    except (KeyError, TypeError, json.JSONDecodeError, UnicodeError, ValueError) as exc:
        raise ValueError('无法读取原页：' + str(exc)[:160]) from None


def _validate_rendered_image(data, item):
    """Treat renderer output as untrusted, including declared MIME and size."""
    from PIL import Image
    expected = {'image/png': ('PNG', b'\x89PNG\r\n\x1a\n'),
                'image/jpeg': ('JPEG', b'\xff\xd8\xff')}.get(item.get('media_type'))
    if expected is None or not data.startswith(expected[1]):
        raise ValueError('原页图片格式或 MIME 不匹配。')
    try:
        with Image.open(BytesIO(data)) as image:
            if image.format != expected[0] or image.size != (item['width'], item['height']):
                raise ValueError('原页图片实际尺寸或格式不匹配。')
            image.verify()
    except (OSError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValueError('原页图片编码无效。') from None


def _png_record(image, page, *, notes=None):
    """Prefer lossless output; bound photographic output without rejecting it.

    The historical name is retained for the shared PDF/image renderer. Every
    attempt has fixed quality and at most two extra resize steps. The isolated
    worker's CPU, wall time, output and task image limits still apply.
    """
    from PIL import Image
    output = BytesIO()
    image.save(output, format='PNG', optimize=False, exif=b'', icc_profile=None)
    data = output.getvalue()
    if len(data) <= MAX_IMAGE_BYTES:
        return {'page': page, 'width': image.width, 'height': image.height,
                'media_type': 'image/png', 'data': base64.b64encode(data).decode('ascii')}
    # Keep luminance and colour detail first; only use chroma subsampling if
    # those high-quality encodings still exceed the single-image transport cap.
    attempts = ((92, 0), (85, 0), (92, 2), (85, 2))
    for step in range(3):
        candidate = image if step == 0 else image.resize(
            (max(1, round(image.width * 0.8 ** step)), max(1, round(image.height * 0.8 ** step))),
            resample=Image.Resampling.LANCZOS)
        try:
            for quality, subsampling in attempts:
                output = BytesIO()
                candidate.save(output, format='JPEG', quality=quality, subsampling=subsampling,
                               optimize=False, progressive=False)
                data = output.getvalue()
                if len(data) > MAX_IMAGE_BYTES:
                    continue
                if notes is not None:
                    notes.append(f'第 {page} 页或帧为满足 4 MiB 传输上限，采用高质量 JPEG 有损压缩'
                                 f'（质量 {quality}' + ('，色度抽样' if subsampling else '') +
                                 '）；细小文字、彩色线条和纹理需核对原件。')
                    if step:
                        notes.append(f'第 {page} 页或帧为满足传输上限额外缩放至 '
                                     f'{candidate.width}×{candidate.height}；细节可能损失，需核对原件。')
                return {'page': page, 'width': candidate.width, 'height': candidate.height,
                        'media_type': 'image/jpeg', 'data': base64.b64encode(data).decode('ascii')}
        finally:
            if candidate is not image:
                candidate.close()
    raise ValueError('原页图片经有限编码与缩放后仍超过 4 MiB 输出上限，请裁剪所需区域后重试。')


def _probe_image_isolated(data: bytes, suffix: str, *, guard=lambda: None) -> dict:
    output, exit_code = _run_render_process(data, suffix, [], guard)
    try:
        if exit_code != 0 or len(output) > MAX_RENDER_OUTPUT_BYTES:
            raise ValueError('图片结构解析失败或超过资源上限。')
        result = json.loads(output)
        if 'error' in result:
            raise ValueError(result['error'])
        if (type(result.get('width')) is not int or type(result.get('height')) is not int
                or min(result['width'], result['height']) < 1
                or result['width'] * result['height'] > 50_000_000
                or (result.get('frame_count') is not None and
                    (type(result['frame_count']) is not int or not 1 <= result['frame_count'] <= 1000))
                or not isinstance(result.get('image_format'), str)
                or not isinstance(result.get('notes'), list)
                or any(not isinstance(note, str) for note in result['notes'])):
            raise ValueError('图片结构输出无效。')
        return result
    except (KeyError, TypeError, json.JSONDecodeError, UnicodeError, ValueError) as exc:
        raise ValueError('无法读取图片结构：' + str(exc)[:160]) from None


def _render_bytes(data: bytes, suffix: str, pages: list[int]) -> dict:
    """仅由隔离工作进程调用，不访问网络，不执行 PDF JavaScript 或文档宏。"""
    from PIL import Image, ImageOps
    from backend.app.services.image_formats import image_probe, normalized_image_bytes
    if pages == [] and suffix in IMAGE_SUFFIXES:
        return image_probe(data, suffix)
    _validate_pages(pages)
    notes, images = [], []
    if suffix == '.pdf':
        import pypdfium2 as pdfium
        document = pdfium.PdfDocument(data)
        try:
            total = len(document)
            if any(page > total for page in pages):
                raise ValueError(f'PDF 共 {total} 页，所选页码超出范围。')
            for number in pages:
                page = document[number - 1]
                bitmap = None
                try:
                    width, height = page.get_size()
                    if not all(math.isfinite(value) and 0 < value <= 100_000 for value in (width, height)):
                        raise ValueError('PDF 页面尺寸超出安全范围。')
                    scale = min(2.0, 2304 / max(width, height),
                                math.sqrt(MAX_PAGE_PIXELS / (width * height)))
                    bitmap = page.render(scale=scale)
                    image = bitmap.to_pil().convert('RGB')
                    # PDFium rounds rendered dimensions up. At the exact
                    # square-root limit this can add a row/column beyond the
                    # per-page cap; bound actual pixels before encoding.
                    rounded_over_cap = image.width * image.height > MAX_PAGE_PIXELS
                    if rounded_over_cap:
                        factor = math.sqrt(MAX_PAGE_PIXELS / (image.width * image.height))
                        image.thumbnail((max(1, math.floor(image.width * factor)),
                                         max(1, math.floor(image.height * factor))),
                                        resample=Image.Resampling.LANCZOS)
                    images.append(_png_record(image, number, notes=notes))
                    image.close()
                    if scale < 2 or rounded_over_cap:
                        notes.append(f'第 {number} 页为控制资源已缩放；细节可能需要核对原件。')
                finally:
                    if bitmap is not None:
                        bitmap.close()
                    page.close()
        finally:
            document.close()
    else:
        data, native_notes = normalized_image_bytes(data, suffix, pages)
        notes.extend(native_notes)
        Image.MAX_IMAGE_PIXELS = 50_000_000
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        try:
            original = Image.open(BytesIO(data))
        except (Image.UnidentifiedImageError, OSError, Image.DecompressionBombError,
                Image.DecompressionBombWarning):
            raise ValueError('图片内容无法解码，格式或编码可能不受支持，也可能资料已损坏。') from None
        with original:
            total = getattr(original, 'n_frames', 1)
            if total > 1000 or any(page > total for page in pages):
                raise ValueError(f'图片共有 {total} 帧，所选页码超出范围或帧数过多。')
            for number in pages:
                original.seek(number - 1)
                if original.width * original.height > 50_000_000:
                    raise ValueError('原始图片超过 5000 万像素安全上限。')
                image = ImageOps.exif_transpose(original)
                longest = max(1, min(2304, int(math.sqrt(MAX_PAGE_PIXELS * image.width / image.height))))
                image.thumbnail((longest, min(2304, MAX_PAGE_PIXELS // max(1, longest))))
                if 'A' in image.getbands() or 'transparency' in image.info:
                    # Office 常有透明底黑色图；直接丢弃 alpha 会把背景与线条都变黑。
                    rgba = image.convert('RGBA')
                    background = Image.new('RGBA', image.size, 'white')
                    background.alpha_composite(rgba)
                    converted = background.convert('RGB')
                    rgba.close()
                    background.close()
                else:
                    converted = image.convert('RGB')
                image.close()
                image = converted
                images.append(_png_record(image, number, notes=notes))
                if image.size != original.size:
                    notes.append(f'第 {number} 帧为控制资源已缩放。')
                image.close()
    if total > len(pages):
        notes.append(f'本次仅查看 {len(pages)} / {total} 页或帧，其余内容尚未阅读。')
    return {'page_count': total, 'images': images, 'notes': notes}


def render_worker_main() -> int:
    """冻结后端入口在加载 FastAPI/配置/数据库前将专用参数转交到这里。"""
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
        resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_RENDER_OUTPUT_BYTES, MAX_RENDER_OUTPUT_BYTES))
        # macOS 原生 PDFium/解释器需要虚拟地址空间，2 GiB 是任务进程的硬上限。
        try:
            resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
        except (ValueError, OSError):
            pass
    except ImportError:
        pass
    try:
        index = sys.argv.index(_RENDER_FLAG)
        parameters = json.loads(sys.argv[index + 1])
        if parameters['suffix'] not in {'.pdf', *_IMAGE_SUFFIXES}:
            raise ValueError('不支持的视觉资料格式。')
        data = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(data) > MAX_INPUT_BYTES:
            raise ValueError('资料超过 32 MiB 上限。')
        result = _render_bytes(data, parameters['suffix'], parameters['pages'])
    except ValueError as exc:
        result = {'error': str(exc)[:160]}
    except Exception:
        result = {'error': '原页读取失败，资料可能损坏、加密或超过资源限制。'}
    sys.stdout.write(json.dumps(result, ensure_ascii=False, separators=(',', ':')))
    return 0


if __name__ == '__main__':
    raise SystemExit(render_worker_main())
