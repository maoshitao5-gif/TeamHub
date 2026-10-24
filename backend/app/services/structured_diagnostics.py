"""有界、无正文的结构化任务诊断；事件和最终返回值均视为不可信输入。"""
import json

_TERMINAL = {'preorganize': 'propose_organization', 'card': 'submit_document_card'}
_COUNTERS = ('model_requests', 'tool_calls', 'read_sources', 'visual_pages',
             'submission_attempts', 'recovery_attempts', 'read_failures')
_READ_TOOLS = frozenset({'document_overview', 'read_document', 'read_table', 'read_visual',
    'read_embedded_images', 'list_folder_members', 'read_folder_member',
    'list_archive_members', 'read_archive_member'})
_READ_ERROR_CODES = frozenset({'READ_FAILED', 'IMAGE_OUTPUT_TOO_LARGE', 'IMAGE_RENDER_FAILED'})
_IMAGE_OUTPUT_ERRORS = frozenset({
    '原页图片超过 4 MiB 输出上限，请选择更小的资料。',
    '原页图片经有限编码与缩放后仍超过 4 MiB 输出上限，请裁剪所需区域后重试。',
    '原页输出超过图片上限。', '原页解析输出超过安全上限，已停止。',
})
_IMAGE_RENDER_ERRORS = frozenset({
    '图片内容无法解码，格式或编码可能不受支持，也可能资料已损坏。',
    '原页解析失败或超过资源上限，本次未返回图片。',
    '原页解析进程无法启动，请检查本机安装。',
    '原页解析超过 15 秒上限，已停止；请减少页数或简化资料。',
    '图片结构解析失败或超过资源上限。', '图片结构输出无效。',
    '原页输出不完整。', '原页尺寸无效。', '原页输出超过总像素上限。',
    '原始图片超过 5000 万像素安全上限。',
})
_ERROR_CODES = frozenset({'SUBMISSION_SCHEMA_INVALID', 'SUBMISSION_EVIDENCE_INVALID',
                          'SUBMISSION_RULE_INVALID', 'SUBMISSION_INVALID'})
_VALIDATION_CODES = frozenset({
    'required', 'invalid_type', 'too_short', 'too_long', 'invalid_format',
    'unexpected_field', 'invalid_value', 'extension_mismatch', 'directory_unavailable',
    'source_unavailable', 'missing', 'extra_forbidden', 'string_type', 'string_too_short',
    'string_too_long', 'list_type', 'dict_type', 'model_type',
    'value_error', 'string_pattern_mismatch', 'int_type', 'bool_type',
})
_LEAF = None
_SOURCES = [_LEAF]
_CARD_FIELDS = {'summary': _LEAF, 'document_type': _LEAF, 'topics': [_LEAF],
    'sections': [{'title': _LEAF, 'summary': _LEAF, 'source_ids': _SOURCES}],
    'facts': [{'label': _LEAF, 'value': _LEAF, 'source_ids': _SOURCES}],
    'search_terms': [_LEAF], 'uses': [_LEAF], 'gaps': [_LEAF], 'source_ids': _SOURCES}
_FIELDS = {**_CARD_FIELDS, 'name': _LEAF, 'tags': [_LEAF], 'target_dir': _LEAF,
           'related_clues': [_LEAF], 'card': _CARD_FIELDS}


def read_failure(tool, value):
    """只识别宿主读取结果的顶层失败；原错误、调用参数与成员正文不留存。"""
    if not isinstance(tool, str) or tool not in _READ_TOOLS or not isinstance(value, dict):
        return None
    result = value.get('result')
    if not isinstance(result, dict) or not ('error' in result or result.get('ok') is False):
        return None
    code = 'READ_FAILED'
    error = result.get('error')
    if isinstance(error, str) and len(error) <= 500:
        # 这些前缀仅来自隔离读取包装层。只匹配完整受控消息，绝不记录原字符串。
        for prefix in ('无法读取原页：', '无法读取图片结构：'):
            if error.startswith(prefix):
                error = error[len(prefix):]
                break
        if error in _IMAGE_OUTPUT_ERRORS:
            code = 'IMAGE_OUTPUT_TOO_LARGE'
        elif error in _IMAGE_RENDER_ERRORS:
            code = 'IMAGE_RENDER_FAILED'
    return {'tool': tool, 'code': code}


def _field(value):
    if value == '$':
        return value
    if not isinstance(value, str) or len(value) > 128:
        return None
    node = _FIELDS
    parts = value.split('.')
    if len(parts) > 7:
        return None
    for part in parts:
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isascii() and part.isdecimal() and len(part) <= 3 and int(part) < 200:
            node = node[0]
        else:
            return None
    return value


def sanitize_diagnostics(value, purpose):
    """只接受固定枚举、计数、静态 schema 路径；未知字符串绝不照抄。"""
    if (purpose not in _TERMINAL or not isinstance(value, dict)
            or value.get('purpose') != purpose or value.get('terminal_tool') != _TERMINAL[purpose]
            or value.get('outcome') not in ('recovery', 'incomplete', 'submitted')):
        return {}
    result = {'purpose': purpose, 'terminal_tool': _TERMINAL[purpose], 'outcome': value['outcome']}
    for key in _COUNTERS:
        count = value.get(key)
        result[key] = min(1000000, max(0, count)) if type(count) is int else 0
    read_error = value.get('last_read_error')
    if isinstance(read_error, dict):
        tool, code = read_error.get('tool'), read_error.get('code')
        if (isinstance(tool, str) and len(tool) <= 40 and tool in _READ_TOOLS
                and isinstance(code, str) and len(code) <= 40 and code in _READ_ERROR_CODES):
            result['last_read_error'] = {'tool': tool, 'code': code}
    error = value.get('last_error')
    if isinstance(error, dict):
        code = error.get('code')
        # 成员值也可能是不可信容器；不用集合查询，避免未哈希类型引发异常。
        code = code if isinstance(code, str) and len(code) <= 80 and code in _ERROR_CODES else 'SUBMISSION_INVALID'
        errors = []
        candidates = error.get('validation_errors')
        for item in candidates[:32] if isinstance(candidates, list) else ():
            if not isinstance(item, dict):
                continue
            field, detail_code = _field(item.get('field')), item.get('code')
            if field and isinstance(detail_code, str) and len(detail_code) <= 40 and detail_code in _VALIDATION_CODES:
                clean = {'field': field, 'code': detail_code}
                if clean not in errors:
                    errors.append(clean)
                if len(errors) == 8:
                    break
        result['last_error'] = {'code': code, 'validation_errors': errors}
    return result


class StructuredDiagnostics:
    """回调只接收专用诊断事件，普通正文、状态及工具输出全部忽略。"""
    def __init__(self, purpose):
        self.purpose = purpose
        self.value = {}

    def __call__(self, kind, value):
        if kind == 'structured_diagnostic':
            clean = sanitize_diagnostics(value, self.purpose)
            if clean:
                self.value = clean

    def accept_output(self, output):
        if isinstance(output, dict):
            self('structured_diagnostic', output.get('diagnostics'))


def log_diagnostics(logger, purpose, state, value):
    """不含任务名称、路径、模型消息或异常文本；落库成功后记录真实任务状态。"""
    clean = sanitize_diagnostics(value, purpose)
    if not clean:
        clean = sanitize_diagnostics({'purpose': purpose, 'terminal_tool': _TERMINAL[purpose],
                                      'outcome': 'incomplete'}, purpose)
    clean['task_state'] = 'ready' if state == 'ready' else 'failed'
    read_code = (clean.get('last_read_error', {}).get('code')
                 if state != 'ready' and not clean['read_sources'] else None)
    clean['diagnostic_code'] = read_code or clean.get('last_error', {}).get('code') or (
        'STRUCTURED_TASK_COMPLETED' if state == 'ready' else 'STRUCTURED_TASK_FAILED')
    (logger.info if state == 'ready' else logger.warning)(
        'structured_task %s', json.dumps(clean, ensure_ascii=True, separators=(',', ':')))
