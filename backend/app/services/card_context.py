"""冻结既有理解作为阅读线索；绝不继承上一任务的原文证据编号。"""
import json

from backend.app.services import document_cards


def understanding_context(db, document_id):
    state = document_cards.status(db, document_id)
    previous = state['card'] or state.get('previous_card')
    result = {'annotation': {**state['annotation'], 'origin': 'user_annotation'}}
    if previous is None:
        return result
    content = json.loads(json.dumps(previous['content']))
    content.pop('source_ids', None)
    by_id = {source['id']: source for source in previous['sources']}
    for item in [*content['sections'], *content['facts']]:
        references = item.pop('source_ids', [])
        item['evidence_locations'] = [
            {'label': by_id[ref]['label'], 'location': by_id[ref]['location']}
            for ref in references if ref in by_id][:5]
    card = {'origin': 'ai_derived', 'state': state['state'],
            'version_id': previous['version_id'], 'version_number': previous['version_number'],
            'same_version': previous['version_id'] == state['version_id'],
            'revision': previous['revision'], 'content': content, 'coverage': previous['coverage']}
    # Keep the main summary and known gaps, but never crowd out the actual file
    # with a large inherited catalogue. Truncation is explicit to the reader.
    arrays = ('facts', 'sections', 'search_terms', 'topics', 'uses')
    while len(json.dumps(card, ensure_ascii=False)) > 16000:
        candidates = [key for key in arrays if content[key]]
        if not candidates:
            break
        key = max(candidates, key=lambda key: len(json.dumps(content[key], ensure_ascii=False)))
        content[key].pop()
        card['context_truncated'] = True
    result['existing_card'] = card
    return result
