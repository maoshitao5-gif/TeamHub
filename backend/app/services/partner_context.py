"""本机可审查的会话上下文；不在准备阶段调用模型，不改写原历史。"""
import hashlib
import json
import re

from backend.app.partner_models import PartnerMessage, PartnerTask
from backend.app.services.partner_tools import PartnerTools

RECENT_MESSAGES = 16
RECENT_CHARACTERS = 18000
NOTE_CHARACTERS = 12000
EXCERPT_CHARACTERS = 1000
_GOAL_WORDS = re.compile(r'目标|约束|预算|要求|必须|不得|禁止|不能|待办|下一步|尚未|未完成|希望|重点|计划')


def authorization_for_task(task):
    """外发权限按资料身份传递；版本变化只影响证据，不抹掉用户目标。"""
    if task is None:
        return set()
    original = json.loads(task.input_json or '{}')
    required = set(original.get('scope', {}))
    inherited = original.get('authorization_scope_ids', [])
    if not isinstance(inherited, list) or any(not isinstance(key, str) for key in inherited):
        return None
    # 旧任务没有单独的授权链字段，至少继承原轮范围及已持久化的历史证据身份。
    required.update(inherited)
    required.update(original.get('history_dependencies', {}))
    return required


def authorization_for(db, message):
    task = db.get(PartnerTask, message.task_id)
    required = authorization_for_task(task)
    if required is None:
        return None
    stored = json.loads(message.sources_json or '[]')
    inherited = stored.get('authorization_scope_ids', []) if isinstance(stored, dict) else []
    if not isinstance(inherited, list) or any(not isinstance(key, str) for key in inherited):
        return None
    required.update(inherited)
    return required


def dependencies_for(db, message):
    """旧回答未记录搜索依赖时，保守复核原任务的完整冻结范围。"""
    if message.role != 'assistant':
        # 用户目标也可能含原资料摘录；原任务绑定资料失效时不把它提升为通用目标。
        task = db.get(PartnerTask, message.task_id)
        return json.loads(task.input_json or '{}').get('scope', {}) if task else {}
    stored = json.loads(message.sources_json)
    if isinstance(stored, dict) and stored.get('schema_version') == 2:
        return stored.get('dependencies', {})
    task = db.get(PartnerTask, message.task_id)
    previous_scope = json.loads(task.input_json).get('scope') if task else None
    if isinstance(previous_scope, dict):
        return previous_scope
    return None if stored else {}


def _valid_dependencies(dependencies, scope):
    return isinstance(dependencies, dict) and all(scope.get(key) == record
                                                for key, record in dependencies.items())


def _excerpt(content):
    """仅摘选完整段/句，明确标记选段；不把前文截断冒充语义总结。"""
    if len(content) <= EXCERPT_CHARACTERS:
        return content
    pieces = [piece.strip() for piece in re.split(r'(?<=[。！？!?；;])|\n+', content) if piece.strip()]
    selected = []
    size = 0
    for index, piece in sorted(enumerate(pieces), key=lambda item: (not bool(_GOAL_WORDS.search(item[1])), item[0])):
        if len(piece) > EXCERPT_CHARACTERS or size + len(piece) > EXCERPT_CHARACTERS:
            continue
        selected.append((index, piece))
        size += len(piece)
    if not selected:
        return '（原消息较长且没有适合完整摘录的段句；请在本机按消息ID查看全文。）'
    return '【完整句段选摘，原消息有省略】' + '\n'.join(piece for _, piece in sorted(selected))


def build_context(db, session_id, scope, *, engine, root):
    """返回近期原文、可核对旧消息摘录及仍获授权的已读原文。"""
    messages = db.query(PartnerMessage).filter_by(session_id=session_id).order_by(
        PartnerMessage.created_at.asc(), PartnerMessage.id.asc()).all()
    valid = []
    discarded = 0
    fingerprint = []
    authorization_by_message = {}
    for message in messages:
        records = dependencies_for(db, message)
        authorization = authorization_for(db, message)
        # 完整历史指纹让未进入有界预览的原消息变化也必须重新确认。
        fingerprint.append([message.id, message.role, message.content, records,
                            sorted(authorization) if authorization is not None else None])
        # 助手无工具复述也继承用户输入的授权来源；不能以空版本依赖冒充通用知识。
        if authorization is None or not authorization.issubset(scope):
            discarded += 1
            continue
        # 新聊天的用户原话用于保留目标与修正；同一资料的新版本不等于撤销阅读权限。
        # 仍要求原范围内所有资料可访问，避免收窄后重新外发用户粘贴的旧资料文字。
        # 助手事实回答/工具证据继续逐版本核验，不能以用户目标的规则放行。
        if message.role == 'user' and isinstance(records, dict):
            task = db.get(PartnerTask, message.task_id)
            direct = task is not None and json.loads(task.input_json or '{}').get('direct_chat')
            if direct and set(records).issubset(scope):
                # 用户原话不是当前版本证据，不把尚未读取的整库文件变成模型结果依赖。
                records = {}
        if not _valid_dependencies(records, scope):
            discarded += 1
            continue
        valid.append((message, records))
        authorization_by_message[message.id] = authorization
    recent, count = [], 0
    for item in reversed(valid):
        size = len(item[0].content)
        if len(recent) >= RECENT_MESSAGES or count + size > RECENT_CHARACTERS:
            break
        recent.append(item)
        count += size
    recent.reverse()
    recent_ids = {message.id for message, _ in recent}
    older = [(message, records) for message, records in valid if message.id not in recent_ids]
    # 优先保留最初目标、用户约束，再选已完成回答；内容均为可追溯原文摘录。
    priority = sorted(enumerate(older), key=lambda item: (
        0 if item[0] == 0 and item[1][0].role == 'user' else
        1 if item[1][0].role == 'user' and _GOAL_WORDS.search(item[1][0].content) else
        2 if item[1][0].role == 'assistant' else 3, -item[0]))
    excerpts, used, note_size = [], [], 0
    for index, (message, records) in priority:
        snippet = f'消息ID：{message.id}；角色：{message.role}\n{_excerpt(message.content)}'
        if note_size + len(snippet) > NOTE_CHARACTERS - 400:
            continue
        excerpts.append((index, snippet))
        used.append((message, records))
        note_size += len(snippet) + 2
    note = ''
    if older:
        note = ('历史摘录（本机按原文挑选，未调用模型总结；摘录仅用于理解对话，资料事实仍须核对有效证据）。\n'
                + '\n\n'.join(snippet for _, snippet in sorted(excerpts)))
        omitted = len(older) - len(excerpts)
        if omitted:
            note += f'\n另有 {omitted} 条较早消息未进入本次摘录；完整历史保留在本机对话记录。'
    history = [{'role': message.role, 'content': message.content} for message, _ in recent]
    dependencies = {key: value for _, records in [*recent, *used] for key, value in records.items()}
    authorization_scope_ids = {key for message, _ in [*recent, *used]
                               for key in authorization_by_message[message.id]}
    checkpoint = None
    checkpoint_identity = None
    recovery_notes = []
    # 新版完成任务会保存真实文字检查点；首版没有检查点时不从300字来源摘录伪造。
    previous_tasks = db.query(PartnerTask).filter_by(session_id=session_id, state='completed').order_by(
        PartnerTask.created_at.desc(), PartnerTask.id.desc()).all()
    for task in previous_tasks:
        result = json.loads(task.result_json or '{}')
        stored = result.get('checkpoint')
        if not isinstance(stored, dict) or 'schema_version' not in stored:
            continue
        original = json.loads(task.input_json or '{}')
        old_scope = original.get('scope', {})
        tools = PartnerTools(engine=engine, root=root, scope=scope, guard=lambda: None)
        checkpoint_identity = [task.id, stored]
        try:
            checkpoint = tools.rebase_checkpoint(stored, old_scope)
        except ValueError:
            # 损坏或旧解析规则的本机记录不外发；用户仍可在新轮重新实际读取。
            recovery_notes.append('此前阅读进度未通过本机核验，未恢复证据；需要时将重新读取本轮授权资料。')
            break
        recovery_notes.extend(tools.rebase_notes)
        if not checkpoint['evidence']:
            checkpoint = None
        break
    if recovery_notes:
        note = '\n'.join(filter(None, [note, *recovery_notes]))
    context = {'summary': note,
               'evidence': checkpoint['evidence'] if checkpoint else [],
               'sources': checkpoint['sources'] if checkpoint else []}
    if checkpoint:
        dependencies.update({key: scope[key] for key in checkpoint['exposed_document_ids']})
        authorization_scope_ids.update(checkpoint['exposed_document_ids'])
    fingerprint.append(checkpoint_identity)
    revision = hashlib.sha256(json.dumps(fingerprint, sort_keys=True, ensure_ascii=False,
                                        separators=(',', ':')).encode()).hexdigest()
    return {'history': history, 'context': context, 'checkpoint': checkpoint,
            'history_dependencies': dependencies, 'context_revision': revision,
            'authorization_scope_ids': sorted(authorization_scope_ids),
            'omitted': discarded}
