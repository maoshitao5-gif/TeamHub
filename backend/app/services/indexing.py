"""已保存版本的本机索引：短会话认领，离线解析，重新核验后提交。"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import threading
import time
import uuid

from sqlalchemy import and_, delete, literal_column, or_, select, text, update
from sqlalchemy.dialects.sqlite import insert

from backend.app import database
from backend.app.config import settings
from backend.app.core.logger import get_logger
from backend.app.index_models import DocumentIndex, IndexChunk, IndexText, IndexWorkItem
from backend.app.models import Document, Version
from .index_content import PARSER_VERSION, SCHEMA_VERSION, SKILL_VERSION

_ACTIVE = ('pending', 'organized')
CHUNK_BATCH_SIZE = 128
WORK_LEASE_SECONDS = 300
_process_lock = threading.Lock()
_worker_lock = threading.Lock()
_worker = None
_worker_stop = threading.Event()
_owned_leases = set()
logger = get_logger('services.indexing')


def install_schema(engine):
    """为已有数据库幂等增加派生全文表；只在初次创建时补全已有文本。"""
    from .index_chunks import install_schema as install_chunk_schema
    install_chunk_schema(engine)
    with engine.begin() as conn:
        existed = conn.execute(text(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='index_text_fts'"
        )).scalar()
        conn.execute(text("""CREATE VIRTUAL TABLE IF NOT EXISTS index_text_fts USING fts5(
            body, content='index_texts', content_rowid='id', tokenize='trigram'
        )"""))
        conn.execute(text("""CREATE TRIGGER IF NOT EXISTS index_texts_ai AFTER INSERT ON index_texts BEGIN
            INSERT INTO index_text_fts(rowid, body) VALUES (new.id, new.body);
        END"""))
        conn.execute(text("""CREATE TRIGGER IF NOT EXISTS index_texts_ad AFTER DELETE ON index_texts BEGIN
            INSERT INTO index_text_fts(index_text_fts, rowid, body) VALUES ('delete', old.id, old.body);
        END"""))
        conn.execute(text("""CREATE TRIGGER IF NOT EXISTS index_texts_au AFTER UPDATE ON index_texts BEGIN
            INSERT INTO index_text_fts(index_text_fts, rowid, body) VALUES ('delete', old.id, old.body);
            INSERT INTO index_text_fts(rowid, body) VALUES (new.id, new.body);
        END"""))
        if not existed:
            conn.execute(text("INSERT INTO index_text_fts(index_text_fts) VALUES ('rebuild')"))
        from backend.app.services.document_cards import install_projection_triggers
        install_projection_triggers(conn)


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def _metadata_json(doc):
    # 顺序与下方 SQL 一致；标签是可变检索语境，不写入正文事实。
    return _json([doc.name, doc.description, doc.storage_path, doc.storage_area,
                  doc.original_path, doc.storage_mode,
                  [[tag.id, tag.name] for tag in sorted(doc.tags, key=lambda tag: tag.id)]])


def metadata_fingerprint(doc):
    return hashlib.sha256(_metadata_json(doc).encode('utf-8')).hexdigest()


_CURRENT_METADATA_SQL = """json_array(documents.name, documents.description,
    documents.storage_path, documents.storage_area, documents.original_path, documents.storage_mode,
    json((SELECT json_group_array(json(tag_pair)) FROM (
        SELECT json_array(tags.id, tags.name) AS tag_pair
        FROM tags JOIN document_tag ON document_tag.tag_id = tags.id
        WHERE document_tag.document_id = documents.id ORDER BY tags.id
    ))))"""


def _document(db, document_id):
    doc = db.get(Document, document_id)
    if doc is None or doc.status not in _ACTIVE:
        raise ValueError('资料不存在或已移入回收站')
    return doc


def _version(db, doc):
    version = db.get(Version, doc.current_version_id) if doc.current_version_id else None
    return version if version is not None and version.document_id == doc.id else None


def _current(record, doc, version):
    return (record is not None and record.version_id == doc.current_version_id
            and record.content_hash == (version.sha256_hash if version else None)
            and record.parser_version == PARSER_VERSION)


def _queue(db, doc, version, record=None):
    values = {'version_id': version.id if version else None,
              'content_hash': version.sha256_hash if version else None,
              'parser_version': PARSER_VERSION, 'state': 'queued', 'result_json': None,
              'ai_json': None, 'ai_metadata_hash': None, 'ai_metadata_json': None}
    # 原子递增，避免两个重建请求拿到相同 generation 后接纳旧解析结果。
    statement = insert(DocumentIndex).values(document_id=doc.id, generation=1, **values)
    db.execute(statement.on_conflict_do_update(index_elements=['document_id'],
               set_={**values, 'generation': DocumentIndex.generation + 1}))
    db.execute(delete(IndexText).where(IndexText.document_id == doc.id,
                                      IndexText.kind.in_(('content', 'ai'))))
    db.execute(delete(IndexChunk).where(IndexChunk.document_id == doc.id))
    db.execute(delete(IndexWorkItem).where(IndexWorkItem.document_id == doc.id))
    queued = db.get(DocumentIndex, doc.id, populate_existing=True)
    db.add(IndexWorkItem(document_id=doc.id, version_id=queued.version_id,
                         generation=queued.generation, source_hash=queued.content_hash,
                         parser_version=PARSER_VERSION, stage='parse', state='queued'))
    db.flush()
    return queued


def _empty(state, reason=None, note=None):
    return {'schema_version': SCHEMA_VERSION, 'skill_version': SKILL_VERSION, 'parser_version': PARSER_VERSION,
            'state': state, 'overview': '原文摘录：暂无可提取文字。', 'units': [],
            'coverage': {'total': 1 if reason else 0, 'processed': 0,
                         'failed': 1 if reason else 0, 'unit': 'file'},
            'gaps': [{'location': {'scope': 'file'}, 'reason': reason}] if reason else [],
            'notes': [note] if note else []}


def _payload(record, doc, version, db=None):
    result = json.loads(record.result_json) if record.result_json else _empty(record.state)
    work = (db.scalars(select(IndexWorkItem).where(
        IndexWorkItem.document_id == doc.id, IndexWorkItem.generation == record.generation)).first()
        if db is not None else None)
    if work is not None and work.stage != 'done':
        if not record.result_json and work.parsed_result_json:
            result = json.loads(work.parsed_result_json)
            result['units'] = result['units'][:work.cursor]
        result['processing_progress'] = {'stage': work.stage, 'cursor': work.cursor,
                                         'total_units': work.total_units}
    result.update(document_id=doc.id, version_id=record.version_id,
                  version_number=version.version_number if version else None,
                  content_hash=record.content_hash, generation=record.generation,
                  source='saved_version', state=record.state,
                  metadata_hash=metadata_fingerprint(doc), ai=None)
    if record.ai_json and record.ai_metadata_hash == result['metadata_hash']:
        result['ai'] = json.loads(record.ai_json)
    result['unit_count'] = len(result['units'])
    return result


def status(db, document_id, offset=0, limit=20):
    doc = _document(db, document_id)
    version = _version(db, doc)
    record = db.get(DocumentIndex, doc.id)
    if not _current(record, doc, version):
        record = _queue(db, doc, version, record)
        db.commit()
    result = _payload(record, doc, version, db)
    offset, limit = max(0, offset), max(1, min(limit, 100))
    result['units'] = result['units'][offset:offset + limit]
    result.update(offset=offset, limit=limit)
    return result


def rebuild(db, document_id):
    doc = _document(db, document_id)
    record = _queue(db, doc, _version(db, doc), db.get(DocumentIndex, doc.id))
    db.commit()
    return _payload(record, doc, _version(db, doc), db)


def load_record(db, document_id):
    doc = _document(db, document_id)
    version = _version(db, doc)
    record = db.get(DocumentIndex, doc.id)
    if not _current(record, doc, version) or record.state not in ('ready', 'partial'):
        return None
    return _payload(record, doc, version, db)


def save_ai(db, document_id, version_id, content_hash, metadata_hash, result, *, expected_generation=None):
    db.expire_all()
    doc = _document(db, document_id)
    version = _version(db, doc)
    record = db.get(DocumentIndex, doc.id)
    if (not _current(record, doc, version) or record.state not in ('ready', 'partial')
            or version_id != record.version_id or content_hash != record.content_hash
            or metadata_hash != metadata_fingerprint(doc)
            or (expected_generation is not None and expected_generation != record.generation)):
        raise ValueError('资料版本或整理语境已变化，请重新准备 AI 增强')
    unit_ids = {unit['id'] for unit in json.loads(record.result_json)['units']}
    if any(clue['unit_id'] not in unit_ids for clue in result.get('clues', [])):
        raise ValueError('AI 线索没有对应的正文单位')
    metadata_json = _metadata_json(doc)
    current_document = (select(Document.id).join(Version, and_(
        Version.id == Document.current_version_id, Version.document_id == Document.id))
        .where(Document.id == document_id, Document.status.in_(_ACTIVE),
               Version.id == version_id, Version.sha256_hash == content_hash,
               literal_column(_CURRENT_METADATA_SQL) == metadata_json).exists())
    accepted = db.execute(update(DocumentIndex).where(
        DocumentIndex.document_id == document_id, DocumentIndex.generation == record.generation,
        DocumentIndex.version_id == version_id, DocumentIndex.content_hash == content_hash,
        DocumentIndex.parser_version == PARSER_VERSION, DocumentIndex.state.in_(('ready', 'partial')),
        current_document).values(ai_json=_json(result), ai_metadata_hash=metadata_hash,
                                 ai_metadata_json=metadata_json).execution_options(synchronize_session=False))
    if accepted.rowcount != 1:
        db.rollback()
        raise ValueError('资料版本或整理语境已变化，请重新准备 AI 增强')
    db.execute(delete(IndexText).where(IndexText.document_id == document_id, IndexText.kind == 'ai'))
    body = '\n'.join([result.get('overview', ''),
                      *(term for clue in result.get('clues', []) for term in clue.get('terms', []))])
    if body.strip():
        db.add(IndexText(document_id=document_id, kind='ai', body=body))
    db.commit()


def content_document_ids(keyword):
    """参数化的字面子串检索；当前版本、资料状态、AI 元数据均在查询时核验。"""
    keyword = str(keyword or '').strip()
    from backend.app.card_models import DocumentCard, DocumentCardAnnotation
    from backend.app.services.card_schema import SCHEMA_VERSION as CARD_SCHEMA, SKILL_VERSION as CARD_SKILL
    current_index = and_(DocumentIndex.version_id == Version.id,
                         DocumentIndex.content_hash == Version.sha256_hash,
                         DocumentIndex.parser_version == PARSER_VERSION,
                         DocumentIndex.state.in_(('ready', 'partial')))
    current_card = select(DocumentCard.document_id).where(
        DocumentCard.document_id == Document.id, DocumentCard.version_id == Version.id,
        DocumentCard.content_hash == Version.sha256_hash, DocumentCard.parser_version == PARSER_VERSION,
        DocumentCard.skill_version == CARD_SKILL, DocumentCard.schema_version == CARD_SCHEMA).exists()
    current_note = select(DocumentCardAnnotation.document_id).where(
        DocumentCardAnnotation.document_id == Document.id,
        DocumentCardAnnotation.source_version_id == Version.id).exists()
    query = (select(IndexText.document_id).join(DocumentIndex,
             DocumentIndex.document_id == IndexText.document_id)
             .join(Document, Document.id == IndexText.document_id)
             .join(Version, and_(Version.id == Document.current_version_id,
                                Version.document_id == Document.id))
             .where(Document.status.in_(_ACTIVE), or_(
                 and_(current_index, or_(IndexText.kind == 'content',
                     and_(IndexText.kind == 'ai', DocumentIndex.ai_json.isnot(None),
                          DocumentIndex.ai_metadata_json == literal_column(_CURRENT_METADATA_SQL)))),
                 and_(IndexText.kind == 'card', current_card),
                 and_(IndexText.kind == 'note', current_note))))
    if not keyword:
        return query.where(False)
    if len(keyword) >= 3:
        # 双引号转义为 FTS 字面短语，操作符、百分号等均不作为查询语法。
        phrase = '"' + keyword.replace('"', '""') + '"'
        matches = select(literal_column('rowid')).select_from(text('index_text_fts')).where(
            literal_column('index_text_fts').op('MATCH')(phrase))
        query = query.where(IndexText.id.in_(matches))
    else:
        escaped = keyword.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        query = query.where(IndexText.body.like('%' + escaped + '%', escape='\\'))
    return query.distinct()


def _scope():
    return str(Path(settings.library_path).resolve()) if settings.library_path else ''


def _binding(db):
    # 只保存值，不缓存引擎或跨线程传递 ORM 对象。
    return str(db.get_bind().url), _scope(), id(db.get_bind())


def _discover(db):
    # 租约只恢复未完成工作；已持久化的解析产物不会重新进入解析阶段。
    # process_one 的锁保证本进程旧 token 已无执行者，切库后可立即回收。
    abandoned = set(db.scalars(select(IndexWorkItem.lease_token).where(
        IndexWorkItem.lease_token.in_(tuple(_owned_leases))))) if _owned_leases else set()
    db.execute(update(IndexWorkItem).where(
        IndexWorkItem.state == 'processing', or_(IndexWorkItem.lease_until.is_(None),
                                                 IndexWorkItem.lease_until <= time.time(),
                                                 IndexWorkItem.lease_token.in_(tuple(abandoned))))
        .values(state='queued', lease_token=None, lease_until=None))
    _owned_leases.difference_update(abandoned)
    candidates = (db.query(Document, Version, DocumentIndex)
                  .outerjoin(Version, and_(Version.id == Document.current_version_id,
                                           Version.document_id == Document.id))
                  .outerjoin(DocumentIndex, DocumentIndex.document_id == Document.id)
                  .filter(Document.status.in_(_ACTIVE), or_(
                      DocumentIndex.document_id.is_(None),
                      DocumentIndex.version_id.is_distinct_from(Document.current_version_id),
                      DocumentIndex.content_hash.is_distinct_from(Version.sha256_hash),
                      DocumentIndex.parser_version != PARSER_VERSION)).limit(100).all())
    for doc, version, record in candidates:
        _queue(db, doc, version, record)
    # 升级前的 ready/partial 结果可直接回填，processing 无产物才需要重解析。
    missing = (db.query(DocumentIndex).join(Document, Document.id == DocumentIndex.document_id)
               .outerjoin(IndexWorkItem, and_(IndexWorkItem.document_id == DocumentIndex.document_id,
                                             IndexWorkItem.generation == DocumentIndex.generation))
               .filter(Document.status.in_(_ACTIVE), IndexWorkItem.id.is_(None),
                       DocumentIndex.state.in_(('queued', 'processing', 'ready', 'partial')))
               .limit(100).all())
    for record in missing:
        parsed = None
        if record.state in ('ready', 'partial') and record.result_json:
            try:
                candidate = json.loads(record.result_json)
                if isinstance(candidate.get('units'), list):
                    parsed = candidate
            except (ValueError, TypeError, AttributeError):
                pass
        if parsed is None:
            record.state = 'queued'
        db.add(IndexWorkItem(document_id=record.document_id, version_id=record.version_id,
                             generation=record.generation, source_hash=record.content_hash,
                             parser_version=record.parser_version,
                             stage='publish' if parsed is not None else 'parse', state='queued',
                             total_units=len(parsed['units']) if parsed is not None else 0,
                             parsed_result_json=_json(parsed) if parsed is not None else None))
    db.commit()


def _snapshot_bytes(job):
    from backend.app.services.index_content import MAX_INPUT_BYTES
    digest = job['content_hash']
    if not digest or not re.fullmatch(r'[a-f0-9]{64}', digest):
        raise ValueError('snapshot_hash_missing')
    root = Path(job['binding'][1])
    relative = Path('.teamhub') / 'objects' / digest[:2] / digest
    # macOS /var 与 /private/var 等库根别名允许共存。只接受配置根或其规范根
    # 下的固定对象路径；仍从规范根逐级 no-follow 打开，绝不解析内部链接。
    expected = {root / relative, Path(job['configured_root']) / relative}
    if Path(job['file_path']) not in expected:
        raise ValueError('snapshot_path_invalid')
    # 用目录描述符逐级锚定，拒绝管理目录或最终文件被符号链接替换。
    descriptors = []
    try:
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, 'O_NOFOLLOW', 0)
        descriptors.append(os.open(root, directory_flags))
        for part in ('.teamhub', 'objects', digest[:2]):
            descriptors.append(os.open(part, directory_flags, dir_fd=descriptors[-1]))
        fd = os.open(digest, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0), dir_fd=descriptors[-1])
        with os.fdopen(fd, 'rb') as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ValueError('snapshot_path_invalid')
            if info.st_size > MAX_INPUT_BYTES:
                raise ValueError('input_limit')
            data = source.read(MAX_INPUT_BYTES + 1)
            if len(data) > MAX_INPUT_BYTES:
                raise ValueError('input_limit')
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError('snapshot_hash_mismatch')
        return data
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _claim():
    with database.SessionLocal() as db:
        _discover(db)
        pending = (db.query(DocumentIndex, IndexWorkItem)
                   .join(Document, Document.id == DocumentIndex.document_id)
                   .join(IndexWorkItem, and_(IndexWorkItem.document_id == DocumentIndex.document_id,
                                             IndexWorkItem.generation == DocumentIndex.generation))
                   .filter(IndexWorkItem.state == 'queued', Document.status.in_(_ACTIVE))
                   .order_by(IndexWorkItem.id).first())
        if pending is None:
            return None, None, None
        record, work = pending
        doc = _document(db, record.document_id)
        version = _version(db, doc)
        if not _current(record, doc, version):
            return None, None, None
        token = str(uuid.uuid4())
        job = {'document_id': doc.id, 'version_id': record.version_id,
               'content_hash': record.content_hash, 'generation': record.generation,
               'work_id': work.id, 'lease_token': token, 'stage': work.stage,
               'binding': _binding(db), 'is_folder': doc.is_folder,
               'configured_root': str(Path(settings.library_path).absolute()),
               'filename': version.original_filename if version else '',
               'file_path': version.file_path if version else ''}
        claimed = db.execute(update(IndexWorkItem).where(
            IndexWorkItem.id == work.id, IndexWorkItem.state == 'queued')
            .values(state='processing', lease_token=token, lease_until=time.time() + WORK_LEASE_SECONDS,
                    attempt=IndexWorkItem.attempt + 1))
        if claimed.rowcount != 1:
            db.rollback()
            return None, None, None
        _owned_leases.add(token)
        db.execute(update(DocumentIndex).where(
            DocumentIndex.document_id == doc.id, DocumentIndex.generation == record.generation,
            DocumentIndex.state == 'queued').values(state='processing'))
        db.commit()
        failure, data = None, None
        if work.stage == 'publish':
            return job, None, None
        if version is None:
            failure = _empty('failed', 'snapshot_missing', '当前资料没有可读取的已保存版本。')
        elif not doc.is_folder:
            try:
                data = _snapshot_bytes(job)
            except (OSError, ValueError) as exc:
                reason = str(exc) if isinstance(exc, ValueError) else 'snapshot_unavailable'
                note = ('文件超过 32 MiB，本次未读取内容。' if reason == 'input_limit'
                        else '已保存版本快照不可读取或校验失败，请检查后重建索引。')
                failure = _empty('failed', reason, note)
        return job, data, failure


def _guard_work(db, job):
    """写入前原子核验来源和租约，并取得本事务的 SQLite 写锁。"""
    if _binding(db) != job['binding']:
        return None, None
    record = db.get(DocumentIndex, job['document_id'])
    work = db.get(IndexWorkItem, job['work_id'])
    if record is None or work is None:
        return None, None
    current_document = select(Document.id).where(
        Document.id == job['document_id'], Document.status.in_(_ACTIVE),
        Document.current_version_id.is_not_distinct_from(job['version_id'])).exists()
    current_version = (select(Version.id).where(Version.id == job['version_id'],
                       Version.document_id == job['document_id'],
                       Version.sha256_hash.is_not_distinct_from(job['content_hash'])).exists()
                       if job['version_id'] else True)
    lease = select(IndexWorkItem.id).where(
        IndexWorkItem.id == job['work_id'], IndexWorkItem.document_id == job['document_id'],
        IndexWorkItem.generation == job['generation'], IndexWorkItem.state == 'processing',
        IndexWorkItem.version_id.is_not_distinct_from(job['version_id']),
        IndexWorkItem.source_hash.is_not_distinct_from(job['content_hash']),
        IndexWorkItem.parser_version == PARSER_VERSION,
        IndexWorkItem.lease_token == job['lease_token'], IndexWorkItem.lease_until > time.time()).exists()
    accepted = db.execute(update(DocumentIndex).where(
        DocumentIndex.document_id == job['document_id'], DocumentIndex.generation == job['generation'],
        DocumentIndex.state.in_(('processing', 'ready', 'partial')),
        DocumentIndex.version_id.is_not_distinct_from(job['version_id']),
        DocumentIndex.content_hash.is_not_distinct_from(job['content_hash']),
        DocumentIndex.parser_version == PARSER_VERSION, current_document, current_version, lease)
        .values(state=DocumentIndex.state).execution_options(synchronize_session=False))
    if accepted.rowcount != 1:
        db.rollback()
        return None, None
    return record, work


def _stage_result(job, result):
    with database.SessionLocal() as db:
        record, work = _guard_work(db, job)
        if record is None or work.stage != 'parse':
            return False
        work.parsed_result_json = _json(result)
        work.total_units = len(result['units'])
        work.stage = 'publish'
        work.cursor = 0
        db.commit()
        return True


def _publish_result(job):
    from .index_chunks import write_batch
    with database.SessionLocal() as db:
        record, work = _guard_work(db, job)
        if record is None or work.stage != 'publish':
            return
        result = json.loads(work.parsed_result_json)
        start = work.cursor
        end = min(start + max(1, CHUNK_BATCH_SIZE), len(result['units']))
        write_batch(db, job, result['units'][start:end], start)
        work.cursor = end
        work.lease_token = None
        work.lease_until = None
        work.state = 'queued'
        if end == len(result['units']):
            record.state = result['state']
            record.result_json = work.parsed_result_json
            work.stage = 'done'
            work.state = 'done'
            work.parsed_result_json = None
            # 旧索引消费者仍使用整文 IndexText；回填时保留已有 AI 线索。
            db.execute(delete(IndexText).where(IndexText.document_id == job['document_id'],
                                              IndexText.kind == 'content'))
            body = _search_body(result['units'])
            if body.strip() and result['state'] in ('ready', 'partial'):
                db.add(IndexText(document_id=job['document_id'], kind='content', body=body))
        db.commit()
        _owned_leases.discard(job['lease_token'])


def _commit_result(job, result):
    if _stage_result(job, result):
        _publish_result(job)


def _search_body(units):
    """还原同一原文位置的分块，保证跨 4000 字分块的编号仍能命中。"""
    pieces, previous_location, previous_end = [], None, None
    for unit in units:
        location = {key: value for key, value in unit['location'].items()
                    if key not in ('char_start', 'char_end')}
        start = unit['location'].get('char_start')
        continued = (previous_end is not None and start == previous_end + 1
                     and location == previous_location)
        if pieces and not continued:
            pieces.append('\n')
        pieces.append(unit['text'])
        previous_location = location
        previous_end = unit['location'].get('char_end')
    return ''.join(pieces)


def process_one():
    if not _process_lock.acquire(blocking=False):
        return False
    try:
        job, data, result = _claim()
        if job is None:
            return False
        if job['stage'] == 'publish':
            _publish_result(job)
            return True
        if job['is_folder']:
            result = _empty('partial', 'folder_not_parsed', '文件夹内容尚未解析；索引未递归读取文件夹内部资料。')
        elif result is None:
            from backend.app.services import index_content
            try:
                result = index_content.extract_index(data, job['filename'])
                index_content.validate_index(result)
            except Exception:
                # 失败持久化并等待人工重建，禁止每轮重复解析损坏文件。
                logger.exception('已保存版本索引解析失败，document_id=%s', job['document_id'])
                result = _empty('failed', 'parser_failed', '正文解析失败，可修复资料后重建索引。')
        _commit_result(job, result)
        return True
    finally:
        _process_lock.release()


def _worker_loop():
    while not _worker_stop.is_set():
        try:
            worked = process_one() if settings.library_path else False
        except Exception:
            logger.exception('本机索引调度失败，下轮重新检查当前文件库')
            worked = False
        _worker_stop.wait(0.05 if worked else 2.0)


def start_worker():
    global _worker
    with _worker_lock:
        if _worker is not None and _worker.is_alive():
            return
        _worker_stop.clear()
        _worker = threading.Thread(target=_worker_loop, name='teamhub-index', daemon=True)
        _worker.start()


def stop_worker():
    global _worker
    with _worker_lock:
        _worker_stop.set()
        if _worker is not None:
            _worker.join(timeout=5)
            if not _worker.is_alive():
                _worker = None
