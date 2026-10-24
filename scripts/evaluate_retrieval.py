#!/usr/bin/env python3
"""在自动清理的合成库中比较旧字面检索与新排序；不调用任何模型。"""
import argparse
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]


def metrics(cases, method):
    positives = [case for case in cases if case['relevant']]
    result = {}
    for count in (5, 10):
        recalls, precisions = [], []
        for case in positives:
            expected = set(case['relevant'])
            found = case[method]['documents'][:count]
            hits = len(expected.intersection(found))
            recalls.append(hits / len(expected))
            precisions.append(hits / len(found) if found else 0)
        result[f'recall_at_{count}'] = statistics.mean(recalls) if recalls else 0
        result[f'precision_at_{count}'] = statistics.mean(precisions) if precisions else 0
    result['negative_false_positives'] = sum(
        bool(case[method]['documents']) for case in cases if not case['relevant'])
    durations = sorted(case[method]['duration_ms'] for case in cases)
    result['mean_duration_ms'] = statistics.mean(durations) if durations else 0
    result['max_duration_ms'] = max(durations, default=0)
    return result


def evaluate():
    # 隔离必须早于导入 Settings/数据库，保证任意工作目录运行都不打开用户库。
    os.environ['TEAMHUB_SANDBOX'] = '1'
    sys.path.insert(0, str(ROOT))
    fixture = json.loads((ROOT / 'backend/tests/fixtures/retrieval_cases.json').read_text())
    with tempfile.TemporaryDirectory(prefix='teamhub-retrieval-eval-') as directory:
        os.environ['TEAMHUB_AI_CONFIG_DIR'] = str(Path(directory) / 'ai-profile')
        os.environ['USER_DATA_DIR'] = str(Path(directory) / 'profile')
        os.environ.pop('DEEPSEEK_API_KEY', None)
        from backend.app.config import settings
        settings.library_path = str(Path(directory) / 'library')
        settings.pending_path = ''
        from backend.app import database
        from backend.app.models import Document, Tag
        from backend.app.services import indexing, search_retrieval, versions
        from sqlalchemy import or_, select
        database.init_db()
        keys = {}
        try:
            for item in fixture['documents']:
                path = Path(settings.library_path) / item['name']
                path.write_text('填' * item.get('padding', 0) + item['content'], encoding='utf-8')
                with database.SessionLocal() as db:
                    doc = Document(name=item['name'], storage_path=item['name'], status='pending',
                                   storage_mode='copy', is_folder=False, doc_type='file')
                    db.add(doc)
                    db.flush()
                    versions.snapshot_working_copy(db, doc)
                    db.commit()
                    keys[doc.id] = item['key']
            while indexing.process_one():
                pass
            cases = []
            with database.SessionLocal() as db:
                for question in fixture['queries']:
                    query = question['query']
                    started = time.perf_counter()
                    old = db.scalars(select(Document.id).where(
                        Document.status.in_(('pending', 'organized')), or_(
                            Document.name.contains(query, autoescape=True),
                            Document.description.contains(query, autoescape=True),
                            Document.tags.any(Tag.name.contains(query, autoescape=True)),
                            Document.id.in_(indexing.content_document_ids(query))))
                        .order_by(Document.name, Document.id).limit(10)).all()
                    old_ms = (time.perf_counter() - started) * 1000
                    started = time.perf_counter()
                    ranked = search_retrieval.search(db, query, keys, limit=10)
                    new_ms = (time.perf_counter() - started) * 1000
                    cases.append({**question,
                        'legacy': {'documents': [keys[key] for key in old], 'duration_ms': old_ms},
                        'ranked': {'documents': [keys[item['document_id']] for item in ranked['items']],
                                   'duration_ms': new_ms}})
            return {'scope': 'synthetic_lexical_only', 'model_requests': 0,
                    'document_count': len(keys), 'query_count': len(cases),
                    'note': fixture['description'], 'legacy': metrics(cases, 'legacy'),
                    'ranked': metrics(cases, 'ranked'), 'cases': cases}
        finally:
            indexing.stop_worker()
            database.engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='保存 JSON 评测结果的位置')
    arguments = parser.parse_args()
    result = evaluate()
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: value for key, value in result.items() if key != 'cases'},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
