"""资料卡保存、版本校验和原有全文引擎的当前版本投影。"""
import json
import re
import unicodedata
from datetime import timezone

from sqlalchemy import and_, literal, select, text, update
from sqlalchemy.dialects.sqlite import insert

from backend.app.card_models import DocumentCard, DocumentCardAnnotation
from backend.app.models import Document, Version, utc_now
from backend.app.services.card_schema import CardContent, SCHEMA_VERSION, SKILL_VERSION
from backend.app.services.index_content import PARSER_VERSION
from backend.app.services.object_skills import object_kind

_ACTIVE = ('pending', 'organized')
_COUNT_FIELDS = (('read_units', 'total_units'), ('visual_pages', 'total_visual_pages'),
                 ('embedded_images', 'total_embedded_images'))


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _document(db, document_id):
    doc = db.get(Document, document_id, populate_existing=True)
    if doc is None or doc.status not in _ACTIVE:
        raise ValueError('资料不存在或已移入回收站')
    version = db.get(Version, doc.current_version_id) if doc.current_version_id else None
    if version is not None and version.document_id != doc.id:
        raise ValueError('资料版本归属无效')
    return doc, version


def _valid(card, version):
    return bool(card and version and card.version_id == version.id
                and card.content_hash == version.sha256_hash and card.parser_version == PARSER_VERSION
                and card.skill_version == SKILL_VERSION and card.schema_version == SCHEMA_VERSION)


def _coverage(value):
    pairs = (*_COUNT_FIELDS, ('read_members', 'total_members')) if (
        isinstance(value, dict) and {'read_members', 'total_members'} & value.keys()) else _COUNT_FIELDS
    allowed = {field for pair in pairs for field in pair} | {'gaps', 'complete'}
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError('资料阅读覆盖记录无效')
    result = {'gaps': list(value.get('gaps', [])), 'complete': value.get('complete') is True}
    if (not isinstance(value.get('gaps', []), list) or len(result['gaps']) > 100
            or any(not isinstance(gap, str) or not 1 <= len(gap) <= 1000 for gap in result['gaps'])
            or ('complete' in value and type(value['complete']) is not bool)):
        raise ValueError('资料阅读覆盖记录无效')
    for read, total in pairs:
        amount, maximum = value.get(read, 0), value.get(total)
        if (type(amount) is not int or not 0 <= amount <= 10_000_000
                or (maximum is not None and (type(maximum) is not int or not 0 <= maximum <= 10_000_000))
                or (maximum is not None and amount > maximum)):
            raise ValueError('资料阅读覆盖记录无效')
        result[read], result[total] = amount, maximum
        if maximum is None or amount < maximum:
            result['complete'] = False
    if result['gaps']:
        result['complete'] = False
    return result


def _state(card):
    coverage = json.loads(card.coverage_json)
    content = json.loads(card.content_json)
    return 'ready' if coverage.get('complete') is True and not coverage.get('gaps') and not content['gaps'] else 'partial'


def _payload(card, version_number):
    return {'document_id': card.document_id, 'version_id': card.version_id,
            'version_number': version_number,
            'content_hash': card.content_hash, 'parser_version': card.parser_version,
            'skill_version': card.skill_version, 'schema_version': card.schema_version,
            'revision': card.revision, 'content': json.loads(card.content_json),
            'sources': json.loads(card.sources_json), 'coverage': json.loads(card.coverage_json),
            'generated_at': card.updated_at.replace(tzinfo=timezone.utc).isoformat(),
            'model': card.model, 'state': _state(card)}


def status(db, document_id):
    """只读状态；旧版和规则过时的卡片绝不充作当前卡片。"""
    doc, version = _document(db, document_id)
    card = db.get(DocumentCard, (doc.id, version.id), populate_existing=True) if version else None
    current = _valid(card, version)
    previous = None
    if not current:
        previous = card or db.scalars(select(DocumentCard).where(DocumentCard.document_id == doc.id)
                                     .order_by(DocumentCard.updated_at.desc()).limit(1)).first()
    annotation = db.get(DocumentCardAnnotation, doc.id, populate_existing=True)
    result = {'document_id': doc.id, 'version_id': version.id if version else None,
              'version_number': version.version_number if version else None,
              'object_kind': object_kind(version.original_filename, is_folder=doc.is_folder) if version else 'unknown',
              'state': _state(card) if current else ('stale' if previous else 'missing'),
              'card': _payload(card, version.version_number) if current else None,
              'annotation': {'text': annotation.note if annotation else '',
                             'revision': annotation.revision if annotation else 0,
                             'source_version_id': annotation.source_version_id if annotation else None,
                             'needs_review': bool(annotation and annotation.note and
                                                  annotation.source_version_id != doc.current_version_id)}}
    if previous:
        previous_version = db.get(Version, previous.version_id)
        previous_number = (previous_version.version_number if previous_version
                           and previous_version.document_id == doc.id else None)
        result['previous_card'] = _payload(previous, previous_number)
    return result


def _sources(sources, document_id, version):
    if not isinstance(sources, list) or not 1 <= len(sources) <= 1000:
        raise ValueError('资料卡没有有效原文来源')
    # 位置约束复用原文工具的校验，不能把任意本机路径保存为可展示来源。
    from backend.app.services.partner_tools import _valid_evidence_location
    ids = set()
    fields = {'id', 'source_id', 'document_id', 'version_id', 'content_hash', 'version_number',
              'name', 'location', 'label', 'excerpt'}
    for source in sources:
        if not isinstance(source, dict) or set(source) != fields:
            raise ValueError('资料卡原文来源无效')
        source_id = source.get('id')
        if (not isinstance(source_id, str) or not re.fullmatch(r'S[1-9]\d{0,8}', source_id)
                or source_id in ids or source.get('source_id') != source_id
                or source.get('document_id') != document_id or source.get('version_id') != version.id
                or source.get('content_hash') != version.sha256_hash
                or type(source.get('version_number')) is not int
                or source.get('version_number') != version.version_number
                or not _valid_evidence_location(source.get('location'))
                or any(not isinstance(source.get(key), str) or len(source[key]) > maximum
                       for key, maximum in (('name', 255), ('label', 500), ('excerpt', 300)))):
            raise ValueError('资料卡原文来源与保存版本不一致')
        ids.add(source_id)
    return ids


def _search_text(content):
    # 推测用途可帮助召回，但原文事实与用途在结构化卡片中始终分别保存。
    pieces = [content['summary'], content['document_type'], *content['topics'],
              *content['search_terms'], *content['uses']]
    for section in content['sections']:
        pieces.extend((section['title'], section['summary']))
    for fact in content['facts']:
        pieces.extend((fact['label'], fact['value']))
    return '\n'.join(pieces)


def _current_condition(document_id, version_id, digest):
    return select(Document.id).join(Version, and_(Version.id == Document.current_version_id,
                                                Version.document_id == Document.id)).where(
        Document.id == document_id, Document.status.in_(_ACTIVE), Version.id == version_id,
        Version.sha256_hash == digest).exists()


def save_card(db, document_id, version_id, content_hash, card, sources, coverage, model,
              expected_revision=None, commit=False):
    """同事务保存；不修改原文件、人工元数据或版本，过期结果和陈旧修订被拒绝。"""
    doc, version = _document(db, document_id)
    if not version or version.id != version_id or version.sha256_hash != content_hash:
        raise ValueError('资料版本已变化，请重新生成资料卡')
    content = (card if isinstance(card, CardContent) else CardContent.model_validate(card)).model_dump()
    source_ids = _sources(sources, doc.id, version)
    container = doc.is_folder or object_kind(version.original_filename) == 'archive'
    if any(('member_path' in source['location']) != bool(container) for source in sources):
        raise ValueError('资料卡来源类型与资料不一致，成套资料来源须保留成员相对路径')
    referenced = set(content['source_ids'])
    for entry in content['sections'] + content['facts']:
        referenced.update(entry['source_ids'])
    if not referenced.issubset(source_ids):
        raise ValueError('资料卡包含没有原文依据的引用')
    coverage = _coverage(coverage)
    if container and (coverage.get('total_members') is None or
            coverage.get('read_members') != len({source['location']['member_path'] for source in sources})):
        raise ValueError('成套资料卡的成员覆盖记录与原文来源不一致')
    if not isinstance(model, str) or not 1 <= len(model) <= 200:
        raise ValueError('资料卡模型标识无效')
    current = db.get(DocumentCard, (doc.id, version.id), populate_existing=True)
    revision = current.revision if current else 0
    if expected_revision is not None and (type(expected_revision) is not int or expected_revision != revision):
        raise ValueError('资料卡已更新，请刷新后重试')
    if _valid(current, version):
        old_coverage = json.loads(current.coverage_json)
        if ((old_coverage.get('complete') and not coverage['complete']) or
                any(old_coverage.get(read, 0) > coverage.get(read, 0)
                    for read in ('read_units', 'visual_pages', 'embedded_images', 'read_members'))):
            return status(db, document_id)
    values = dict(content_hash=content_hash, parser_version=PARSER_VERSION, skill_version=SKILL_VERSION,
                  schema_version=SCHEMA_VERSION, revision=revision + 1, content_json=_json(content),
                  sources_json=_json(sources), coverage_json=_json(coverage), search_text=_search_text(content),
                  model=model, updated_at=utc_now())
    condition = _current_condition(doc.id, version.id, content_hash)
    if current:
        accepted = db.execute(update(DocumentCard).where(
            DocumentCard.document_id == doc.id, DocumentCard.version_id == version.id,
            DocumentCard.revision == revision, condition).values(**values)
            .execution_options(synchronize_session=False))
    else:
        values.update(document_id=doc.id, version_id=version.id)
        accepted = db.execute(insert(DocumentCard).from_select(
            list(values), select(*[literal(value) for value in values.values()]).where(condition))
            .on_conflict_do_nothing(index_elements=['document_id', 'version_id']))
    if accepted.rowcount != 1:
        raise ValueError('资料或卡片已变化，本次结果未保存')
    db.flush()
    if commit:
        db.commit()
    return status(db, doc.id)


def save_annotation(db, document_id, version_id, expected_revision, note, commit=False):
    """人工补充按修订比较交换，换版保留并提示核对，AI 保存永不触碰。"""
    doc, version = _document(db, document_id)
    if not version or version.id != version_id:
        raise ValueError('资料版本已变化，请刷新后核对补充内容')
    if (not isinstance(note, str) or len(note) > 4000
            or any(unicodedata.category(char).startswith('C') and char not in '\n\t' for char in note)):
        raise ValueError('人工补充不能超过 4000 字或包含不可见控制字符')
    record = db.get(DocumentCardAnnotation, doc.id, populate_existing=True)
    revision = record.revision if record else 0
    if type(expected_revision) is not int or expected_revision != revision:
        raise ValueError('人工补充已被修改，请刷新后重试')
    values = dict(note=note.strip(), source_version_id=version.id, revision=revision + 1, updated_at=utc_now())
    condition = _current_condition(doc.id, version.id, version.sha256_hash)
    if record:
        accepted = db.execute(update(DocumentCardAnnotation).where(
            DocumentCardAnnotation.document_id == doc.id, DocumentCardAnnotation.revision == revision,
            condition).values(**values).execution_options(synchronize_session=False))
    else:
        values['document_id'] = doc.id
        accepted = db.execute(insert(DocumentCardAnnotation).from_select(
            list(values), select(*[literal(value) for value in values.values()]).where(condition))
            .on_conflict_do_nothing(index_elements=['document_id']))
    if accepted.rowcount != 1:
        raise ValueError('资料或人工补充已变化，请刷新后重试')
    if commit:
        db.commit()
    return status(db, doc.id)


def install_projection_triggers(conn):
    """当前版本切换同步 FTS 投影，恢复旧卡不必等待原文重新解析。"""
    def projection(document_id):
        return f"""
        INSERT OR IGNORE INTO document_indexes(document_id,version_id,content_hash,parser_version,generation,state)
        SELECT d.id,v.id,v.sha256_hash,'{PARSER_VERSION}',1,'queued'
        FROM documents d JOIN versions v ON v.id=d.current_version_id AND v.document_id=d.id
        WHERE d.id={document_id} AND EXISTS (
            SELECT 1 FROM document_cards c WHERE c.document_id=d.id AND c.version_id=v.id
            UNION ALL SELECT 1 FROM document_card_annotations a
            WHERE a.document_id=d.id AND a.source_version_id=v.id);
        DELETE FROM index_texts WHERE document_id={document_id} AND kind IN ('card','note');
        INSERT INTO index_texts(document_id,kind,body)
        SELECT c.document_id,'card',c.search_text FROM document_cards c JOIN documents d ON d.id=c.document_id
        WHERE c.document_id={document_id} AND c.version_id=d.current_version_id;
        INSERT INTO index_texts(document_id,kind,body)
        SELECT a.document_id,'note',a.note FROM document_card_annotations a JOIN documents d ON d.id=a.document_id
        WHERE a.document_id={document_id} AND a.source_version_id=d.current_version_id AND a.note<>'';
        """
    for table, prefix in (('document_cards', 'card_projection'),
                          ('document_card_annotations', 'card_note_projection')):
        for action, row in (('INSERT', 'new'), ('UPDATE', 'new'), ('DELETE', 'old')):
            # DROP/CREATE occurs only at initialization inside its schema transaction.
            name = f'{prefix}_{action.lower()}'
            conn.execute(text(f'DROP TRIGGER IF EXISTS {name}'))
            conn.execute(text(f'CREATE TRIGGER {name} AFTER {action} ON {table} BEGIN '
                              + projection(f'{row}.document_id') + ' END'))
    conn.execute(text('DROP TRIGGER IF EXISTS card_projection_version'))
    conn.execute(text('CREATE TRIGGER card_projection_version AFTER UPDATE OF current_version_id ON documents '
                      'WHEN old.current_version_id IS NOT new.current_version_id '
                      'BEGIN ' + projection('new.id') + ' END'))
    # 原文索引可能仍指向刚删除的历史版本；其外键级联会连带删除当前卡片/注释的
    # FTS 行。必须在版本删除及其级联完成后恢复当前投影，不能只依赖卡片表的触发器。
    conn.execute(text('DROP TRIGGER IF EXISTS card_projection_version_delete'))
    conn.execute(text('CREATE TRIGGER card_projection_version_delete AFTER DELETE ON versions '
                      'BEGIN ' + projection('old.document_id') + ' END'))
    # 修复缺失/陈旧投影；正常重启不重写全库 FTS 文本。
    conn.execute(text(f"""INSERT OR IGNORE INTO document_indexes
        (document_id,version_id,content_hash,parser_version,generation,state)
        SELECT d.id,v.id,v.sha256_hash,'{PARSER_VERSION}',1,'queued'
        FROM documents d JOIN versions v ON v.id=d.current_version_id AND v.document_id=d.id
        WHERE EXISTS(SELECT 1 FROM document_cards c WHERE c.document_id=d.id)
           OR EXISTS(SELECT 1 FROM document_card_annotations a WHERE a.document_id=d.id)"""))
    for kind, table, version_column, body_column in (
        ('card', 'document_cards', 'version_id', 'search_text'),
        ('note', 'document_card_annotations', 'source_version_id', 'note'),
    ):
        current = (f'SELECT c.document_id,c.{body_column} AS body FROM {table} c '
                   'JOIN documents d ON d.id=c.document_id '
                   f"WHERE c.{version_column}=d.current_version_id AND c.{body_column}<>''")
        conn.execute(text(f"DELETE FROM index_texts WHERE kind='{kind}' AND NOT EXISTS "
                          f'(SELECT 1 FROM ({current}) valid WHERE valid.document_id=index_texts.document_id)'))
        conn.execute(text(f"UPDATE index_texts SET body=(SELECT valid.body FROM ({current}) valid "
                          'WHERE valid.document_id=index_texts.document_id) '
                          f"WHERE kind='{kind}' AND EXISTS (SELECT 1 FROM ({current}) valid "
                          'WHERE valid.document_id=index_texts.document_id AND valid.body<>index_texts.body)'))
        conn.execute(text(f"INSERT INTO index_texts(document_id,kind,body) SELECT valid.document_id,'{kind}',valid.body "
                          f'FROM ({current}) valid WHERE NOT EXISTS (SELECT 1 FROM index_texts existing '
                          f"WHERE existing.document_id=valid.document_id AND existing.kind='{kind}')"))
