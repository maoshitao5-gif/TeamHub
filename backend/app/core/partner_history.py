"""私有模型历史的持久化边界；不接受系统规则、像素或不完整工具序列。"""
from copy import deepcopy
import json

MAX_HISTORY_BYTES = 4 * 1024 * 1024
MAX_HISTORY_MESSAGES = 512
MAX_ANSWER_CHARACTERS = 64000
ALLOWED_TOOLS = frozenset({'search_documents', 'document_overview', 'read_document',
    'read_table', 'read_visual', 'read_embedded_images', 'create_draft'})


def validate_engine_history(history):
    """返回独立的协议消息副本，失败供调用方回退到重新授权的文字上下文。

    保留 DeepSeek 等服务要求的 reasoning_content。它不是公共答复，不能通过
    task DTO、日志或来源预览暴露。历史含图像时整段回退，避免持久化像素或假装
    删掉像素之后仍具有原图的上下文。所有工具调用必须已获得一一对应的结果。
    """
    if not isinstance(history, list) or len(history) > MAX_HISTORY_MESSAGES:
        raise ValueError('Invalid native history')
    try:
        encoded = json.dumps(history, ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, RecursionError, UnicodeError):
        raise ValueError('Invalid native history') from None
    if len(encoded) > MAX_HISTORY_BYTES:
        raise ValueError('Native history exceeds storage limit')
    pending = set()
    for item in history:
        if (not isinstance(item, dict) or not isinstance(item.get('role'), str)
                or item['role'] not in {'user', 'assistant', 'tool'}):
            raise ValueError('Invalid native history role')
        role = item['role']
        allowed = {'role', 'content'}
        if role == 'assistant':
            allowed.update({'reasoning_content', 'tool_calls'})
        elif role == 'tool':
            allowed.update({'tool_call_id', 'name'})
        if set(item) - allowed:
            raise ValueError('Invalid native history fields')
        content = item.get('content')
        if not isinstance(content, str) and not (role == 'assistant' and content is None):
            raise ValueError('Native history must contain text only')
        if role != 'tool' and pending:
            raise ValueError('Native history has unfinished tool calls')
        if role == 'tool':
            identifier = item.get('tool_call_id')
            if not isinstance(identifier, str) or identifier not in pending:
                raise ValueError('Native history tool result is not paired')
            if 'name' in item and (not isinstance(item['name'], str) or item['name'] not in ALLOWED_TOOLS):
                raise ValueError('Native history tool is not allowed')
            pending.remove(identifier)
        if role == 'assistant':
            if 'reasoning_content' in item and not isinstance(item['reasoning_content'], str):
                raise ValueError('Invalid reasoning protocol field')
            calls = item.get('tool_calls', [])
            if not isinstance(calls, list) or len(calls) > 64:
                raise ValueError('Invalid native tool calls')
            for call in calls:
                if (not isinstance(call, dict) or set(call) != {'id', 'type', 'function'}
                        or call.get('type') != 'function' or not isinstance(call.get('id'), str)
                        or not 1 <= len(call['id']) <= 200 or call['id'] in pending):
                    raise ValueError('Invalid native tool call')
                function = call['function']
                if (not isinstance(function, dict) or set(function) != {'name', 'arguments'}
                        or not isinstance(function.get('name'), str) or function['name'] not in ALLOWED_TOOLS
                        or not isinstance(function.get('arguments'), str)):
                    raise ValueError('Native history tool is not allowed')
                try:
                    arguments = json.loads(function['arguments'])
                except (ValueError, TypeError, RecursionError):
                    raise ValueError('Invalid native tool arguments') from None
                if not isinstance(arguments, dict):
                    raise ValueError('Invalid native tool arguments')
                pending.add(call['id'])
    if pending:
        raise ValueError('Native history has unfinished tool calls')
    return deepcopy(history)
