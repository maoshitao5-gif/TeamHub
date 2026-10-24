"""独立 Hermes 引擎适配。双向管道传递模型请求与受控工具调用。

Hermes 不持有真实服务密钥、资料路径或数据库。模型请求由宿主逐次守卫、
计量并转发；取消即关闭连接并回收本轮子进程，不重放未知付费状态。
"""
import asyncio
import base64
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time

import httpx2

from backend.app.core.ai_client import AIError
from backend.app.core.partner_protocol import BoundedTransport, _instructions
from backend.app.core.partner_budget import FIELDS, PartnerPaused, normalize_budget
from backend.app.core.partner_history import MAX_ANSWER_CHARACTERS, validate_engine_history
from backend.app.services.hermes_tools import HermesTools
from backend.app.services.partner_tools import PartnerEvidenceLimit, PartnerNoProgress, PartnerTools
from backend.app.services.structured_diagnostics import read_failure

ENGINE = {'engine': 'hermes', 'engine_version': 'v0.22.2', 'engine_protocol': 1, 'engine_adapter_revision': 7}
MAX_FRAME = 24 * 1024 * 1024
MAX_MODEL_RESPONSE_BYTES = 8 * 1024 * 1024
_active = set()


def _command():
    executable = 'teamhub-hermes.exe' if sys.platform == 'win32' else 'teamhub-hermes'
    repo = Path(__file__).resolve().parents[3]
    if getattr(sys, 'frozen', False):
        packaged = Path(sys.executable).resolve().parent.parent / 'hermes' / executable
    else:
        packaged = repo / 'dist' / 'hermes' / executable
    if packaged.is_file():
        return [str(packaged)]
    if not getattr(sys, 'frozen', False):
        python = repo / '.mvp-sandbox' / 'hermes-venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        worker = repo / 'integrations' / 'hermes' / 'worker.py'
        source = repo / '.mvp-sandbox' / 'hermes-src'
        if python.is_file() and worker.is_file() and (source / 'run_agent.py').is_file():
            return [str(python), '-I', str(worker)]
    raise AIError(503, 'HERMES_NOT_INSTALLED', 'AI 引擎未完整安装，请重新构建或安装包含 Hermes 的 TeamHub。')


async def _stop(process):
    if process.returncode is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), 1.5)
        except TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()
    _active.discard(process)


async def shutdown():
    await asyncio.gather(*(_stop(process) for process in tuple(_active)), return_exceptions=True)


def _protocol_error():
    return AIError(502, 'HERMES_PROTOCOL_ERROR', '本机 AI 引擎通信异常，本轮已停止；不会自动重试。')


async def _read(process):
    try:
        line = await process.stdout.readline()
        if not line or len(line) > MAX_FRAME:
            raise ValueError
        value = json.loads(line)
        if (not isinstance(value, dict) or type(value.get('v')) is not int or value['v'] != 1
                or not isinstance(value.get('type'), str)):
            raise ValueError
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise _protocol_error() from None


def _usage_from_response(content):
    try:
        value = json.loads(content)
    except (ValueError, TypeError):
        value = {}
        for line in content.splitlines():
            if line.startswith(b'data:'):
                try:
                    frame = json.loads(line[5:])
                    if isinstance(frame, dict) and frame.get('usage'):
                        value = frame
                except (ValueError, TypeError):
                    continue
    value = value.get('usage') if isinstance(value, dict) else None
    if (isinstance(value, dict) and type(value.get('prompt_tokens')) is int
            and type(value.get('completion_tokens')) is int
            and min(value['prompt_tokens'], value['completion_tokens']) >= 0):
        return value['prompt_tokens'], value['completion_tokens']
    return None


def _http_error(status):
    if status in {401, 403}:
        return AIError(400, 'AI_AUTH_FAILED', 'AI 密钥无效或模型无访问权限，请检查设置。')
    if status == 402:
        return AIError(400, 'AI_INSUFFICIENT_BALANCE', 'AI 服务余额不足，请检查服务账号。')
    if status == 429:
        return AIError(429, 'AI_RATE_LIMITED', 'AI 服务暂时限流，请稍后重试；已经生成的内容仍会保留。')
    if status == 400:
        return AIError(422, 'AI_MODEL_CAPABILITY_REQUIRED', 'AI 服务未接受请求，请检查模型的工具、思考与图像支持。')
    return AIError(503, 'AI_UNAVAILABLE', 'AI 服务暂时不可用，本轮不会自动重试。')


class _Pause(Exception):
    def __init__(self, reason):
        self.reason = reason


async def run_agent(*, message, history, scope, config, key, engine, root, guard, emit, limits,
                    history_dependencies=None, checkpoint=None, previous_artifacts=None,
                    previous_answer=None, context=None, transport_override=None,
                    engine_history=None, scope_mode='library', purpose='chat', purpose_context=None):
    await asyncio.to_thread(guard)
    if purpose not in {'chat', 'preorganize', 'card'}:
        raise AIError(422, 'AI_TASK_PURPOSE', 'AI 任务类型无效。')
    structured_task = purpose in {'preorganize', 'card'}
    if structured_task and any((history, history_dependencies, checkpoint,
                                        previous_artifacts, previous_answer, context, engine_history)):
        raise AIError(422, 'AI_CARD_SCOPE' if purpose == 'card' else 'AI_ORGANIZATION_SCOPE',
                      '资料理解与预整理须使用当前单份资料的新任务，不能复用历史依据。')
    history_dependencies = history_dependencies or {}
    if any(scope.get(doc_id) != record for doc_id, record in history_dependencies.items()):
        raise AIError(409, 'PARTNER_HISTORY_CHANGED', '历史依据已变化，请重新准备。')
    limits = {**{field: normalize_budget()[field] for field in FIELDS}, **limits}
    if structured_task:
        from backend.app.services.organization_tools import OrganizationTools, organization_instructions
        from backend.app.services.card_tools import CardTools, card_instructions
        try:
            bridge = await asyncio.to_thread(CardTools if purpose == 'card' else OrganizationTools,
                engine=engine, root=root, scope=scope,
                guard=guard, emit=emit, purpose_context=purpose_context)
        except ValueError:
            raise AIError(422, 'AI_CARD_SCOPE' if purpose == 'card' else 'AI_ORGANIZATION_SCOPE',
                          '资料理解的单份资料或分类语境无效，请重新发起。') from None
        await asyncio.to_thread(bridge.preflight)
        instructions = (card_instructions(kind=bridge.object_kind) if purpose == 'card'
                        else organization_instructions(kind=bridge.object_kind))
        scope_mode = 'selected'
    else:
        bridge = await asyncio.to_thread(HermesTools, engine=engine, root=root, scope=scope,
            guard=guard, emit=emit, checkpoint=checkpoint, artifacts=previous_artifacts)
        instructions = _instructions(native=True)
    definitions = bridge.definitions()
    allowed_tools = {tool['function']['name'] for tool in definitions}
    preview_ids = list(scope) if scope_mode == 'selected' else []
    bridge.tools.exposed_document_ids.update([*history_dependencies, *preview_ids])
    bridge.last_checkpoint = bridge.tools.export_checkpoint()
    last_coverage = bridge.coverage() if structured_task else None
    emit('dependency_records', {doc_id: scope[doc_id] for doc_id in bridge.tools.exposed_document_ids})
    started = time.monotonic()
    counters = dict(model_requests=0, input_tokens=0, output_tokens=0, metered=0)
    process = reader = model_task = directory = None
    used_ids = set()
    native_messages = []
    last_preview = ''
    citation_warning = None
    recovery_attempts = 0
    read_failures = 0
    last_read_error = None
    if engine_history:
        try:
            native_messages = validate_engine_history(engine_history)
        except ValueError:
            emit('status', '历史工具上下文已更新，正使用可用的对话背景继续。')
    write_lock = asyncio.Lock()

    def remember_history(messages):
        nonlocal native_messages
        if structured_task:
            return
        try:
            native_messages = validate_engine_history(messages)
        except ValueError:
            # A partial tool exchange or retained pixels cannot become reusable state.
            native_messages = []

    def output(answer, coverage=None):
        result = {'answer': answer, 'engine_history': native_messages,
            'engine_history_reusable': bool(native_messages),
            'warnings': [citation_warning] if citation_warning else [], 'sources': list(bridge.tools.sources), 'artifacts': bridge.artifacts,
            'read_document_ids': list({source['document_id'] for source in bridge.tools.sources}),
            'dependency_records': {doc_id: scope[doc_id] for doc_id in bridge.tools.exposed_document_ids},
            'checkpoint': bridge.tools.export_checkpoint(), 'context_summary': answer[:1800],
            'progress': bridge.tools.progress, 'engine': dict(ENGINE),
            'usage': {'model_requests': counters['model_requests'], 'tool_calls': bridge.calls,
                'input_tokens': counters['input_tokens'], 'output_tokens': counters['output_tokens'],
                'token_usage_complete': counters['metered'] == counters['model_requests'],
                'unmetered_requests': counters['model_requests'] - counters['metered'],
                'time_seconds': round(time.monotonic() - started, 3)}}
        if purpose == 'preorganize':
            result['proposal'] = bridge.proposal
        if structured_task:
            result['card'] = bridge.card
            result['coverage'] = bridge.coverage() if coverage is None else coverage
            result['diagnostics'] = structured_report('submitted' if bridge.card is not None else 'incomplete')
        return result

    def structured_report(outcome):
        report = {'purpose': purpose,
            'terminal_tool': 'propose_organization' if purpose == 'preorganize' else 'submit_document_card',
            'outcome': outcome, 'model_requests': counters['model_requests'], 'tool_calls': bridge.calls,
            'read_sources': len(bridge.tools.sources),
            'visual_pages': bridge.tools.progress.get('visual_pages', 0),
            'submission_attempts': getattr(bridge, 'submission_attempts', 0),
            'recovery_attempts': recovery_attempts,
            'read_failures': read_failures, 'last_read_error': last_read_error,
            'last_error': getattr(bridge, 'last_submission_error', None)}
        emit('structured_diagnostic', report)
        return report

    def incomplete_message():
        label = '整理方案' if purpose == 'preorganize' else '资料卡'
        if not bridge.tools.sources and last_read_error:
            reason = {'IMAGE_OUTPUT_TOO_LARGE': '图片转换后的数据超出读取上限',
                'IMAGE_RENDER_FAILED': '图片解码或原页转换未完成',
                'READ_FAILED': '未能读取资料内容'}[last_read_error['code']]
            return f'{reason}，未取得可用于生成{label}的内容依据；资料保持不变，可重试或手工处理。'
        last_error = getattr(bridge, 'last_submission_error', None)
        if last_error:
            code = last_error.get('code')
            reason = ('缺少本次实际读取的原文或图像依据' if code == 'SUBMISSION_EVIDENCE_INVALID'
                      else '提交内容未通过字段或资料规则校验')
            return f'AI {label}{reason}，补交后仍未完成；资料保持不变，可重试或手工处理。'
        return f'AI 未提交{label}，已尝试补交但仍未完成；资料保持不变，可重试或手工处理。'

    async def check():
        # 配置锁可能等待系统钥匙串；不能阻塞 ASGI 的健康检查与普通请求。
        await asyncio.to_thread(guard)

    async def before_send():
        # 完整版本校验在外发边界执行，流式字节不反复扫描整批资料。
        await asyncio.to_thread(bridge.verify)
        if counters['model_requests'] >= limits['model_requests']:
            raise _Pause('model_requests')
        if counters['output_tokens'] >= limits['output_tokens']:
            raise _Pause('output_tokens')
        counters['model_requests'] += 1

    def after_response(content):
        value = _usage_from_response(content)
        if value is not None:
            counters['metered'] += 1
            counters['input_tokens'] += value[0]
            counters['output_tokens'] += value[1]

    async def send(value):
        data = (json.dumps({'v': 1, **value}, ensure_ascii=False, allow_nan=False) + '\n').encode()
        if len(data) > MAX_FRAME:
            raise _protocol_error()
        async with write_lock:
            process.stdin.write(data)
            await process.stdin.drain()

    def identify(value):
        identifier = value.get('id')
        if (not isinstance(identifier, str) or not 1 <= len(identifier) <= 100
                or identifier in used_ids):
            raise _protocol_error()
        used_ids.add(identifier)
        return identifier

    async def forward(value, http):
        identifier = identify(value)
        body = value.get('body')
        if (not isinstance(body, dict) or body.get('model') != config['model']
                or not isinstance(body.get('messages'), list)):
            raise _protocol_error()
        if any(not isinstance(tool, dict) or not isinstance(tool.get('function'), dict)
               or tool['function'].get('name') not in allowed_tools for tool in body.get('tools', [])):
            raise _protocol_error()
        # 服务目标、凭据、思考方式与硬预算只取宿主冻结值，不接受引擎改写。
        body = dict(body)
        # The worker may stop at a host budget boundary before returning its final result.
        # Preserve only complete protocol history already present at this request boundary.
        remember_history([item for item in body['messages']
                          if isinstance(item, dict) and item.get('role') != 'system'])
        body['parallel_tool_calls'] = False
        body['max_tokens'] = min(32000 if config.get('thinking_mode') != 'disabled' else 6000,
                                 max(1, limits['output_tokens'] - counters['output_tokens']))
        body.pop('max_completion_tokens', None)
        if config['model'].lower().startswith('deepseek'):
            body['thinking'] = {'type': config.get('thinking_mode', 'enabled')}
            body['reasoning_effort'] = 'high'
        async with http.stream('POST', config['base_url'].rstrip('/') + '/chat/completions',
                headers={'Authorization': f'Bearer {key}'}, json=body) as response:
            if response.status_code != 200:
                if response.status_code == 400 and any(
                        isinstance(item, dict) and item.get('type') == 'image_url'
                        for message in body['messages'] if isinstance(message, dict)
                        for item in (message.get('content') if isinstance(message.get('content'), list) else [])):
                    raise AIError(422, 'AI_IMAGE_REQUEST_REJECTED',
                        '当前 AI 服务未接受图像请求。请在设置中测试图像理解，核对模型及接口的图像支持后重试。')
                raise _http_error(response.status_code)
            await send({'type': 'model_start', 'id': identifier, 'status': response.status_code,
                'headers': {'content-type': response.headers.get('content-type', 'application/json')}})
            async for chunk in response.aiter_bytes():
                # 有界分块避免供应商一次返回的大 chunk 占据整个管道。
                for offset in range(0, len(chunk), 32768):
                    await send({'type': 'model_chunk', 'id': identifier,
                        'data': base64.b64encode(chunk[offset:offset + 32768]).decode('ascii')})
            await send({'type': 'model_end', 'id': identifier})

    try:
        # 子进程先退出，再删除它仍可能写入的工作目录。
        directory = tempfile.TemporaryDirectory(prefix='teamhub-hermes-task-')
        work = directory.name
        if work:
            command = _command()
            env = {name: os.environ[name] for name in ('SYSTEMROOT', 'WINDIR', 'TMPDIR', 'TEMP', 'TMP') if name in os.environ}
            env.update(PATH=os.defpath, PYTHONNOUSERSITE='1', PYTHONIOENCODING='utf-8',
                       HERMES_HOME=str(Path(work) / 'profile'))
            spawning = asyncio.create_task(asyncio.create_subprocess_exec(*command,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, cwd=work, env=env, limit=MAX_FRAME + 1))
            spawn_cancelled = False
            while True:
                try:
                    process = await asyncio.shield(spawning)
                    break
                except asyncio.CancelledError:
                    # Even cancellation during OS spawn must first obtain the child handle;
                    # otherwise asyncio can wait forever for a child we cannot terminate.
                    spawn_cancelled = True
            _active.add(process)
            if spawn_cancelled:
                raise asyncio.CancelledError

            transport = BoundedTransport(check, transport_override,
                                         before_send=before_send, after_response=after_response,
                                         response_limit=MAX_MODEL_RESPONSE_BYTES)
            async with httpx2.AsyncClient(transport=transport, trust_env=False, follow_redirects=False,
                    timeout=httpx2.Timeout(60, connect=10)) as http:
                async with asyncio.timeout(limits['time_seconds']):
                    ready = await asyncio.wait_for(_read(process), 45)
                    if (ready.get('type') != 'ready' or type(ready.get('protocol')) is not int
                            or ready['protocol'] != 1 or ready.get('version') != ENGINE['engine_version']):
                        raise _protocol_error()
                    if ready.get('adapter_revision') != ENGINE['engine_adapter_revision']:
                        raise AIError(503, 'HERMES_ADAPTER_OUTDATED',
                            '本机 AI 引擎需要更新，请重新构建 Hermes 或安装最新 TeamHub。')
                    prompt_context = {'authorized_document_count': len(scope),
                        'authorized_documents_preview': [{'id': doc_id, 'name': scope[doc_id]['name'],
                            'version_number': scope[doc_id].get('version_number')} for doc_id in preview_ids],
                        'scope_notice': ('当前为资料库范围；请按问题需要检索，未读取的资料不构成内容依据。'
                                         if scope_mode == 'library' else '当前为用户选择的资料，可按需要阅读。'),
                        'context': context,
                        'continuation_context': {'evidence': bridge.last_checkpoint.get('evidence', []),
                            'sources': list(bridge.tools.sources),
                            'read_ranges': bridge.last_checkpoint.get('read_ranges', []), 'summary': previous_answer,
                            'previous_artifacts': bridge.artifacts,
                            'notice': '这是最新已验证进度，优先于 context 中的旧摘要与阅读范围。按当前用户问题推进，保持已读来源编号；'
                                      '阶段推断不是原文；此处未保留图像像素，分析视觉关系时须重新读取图像。'}
                            if checkpoint else None}
                    if purpose == 'preorganize':
                        prompt_context['organization'] = bridge.context
                    elif purpose == 'card':
                        prompt_context['document_understanding'] = bridge.context
                    await send({'type': 'start', 'instructions': instructions,
                        'question': message, 'history': history, 'engine_history': native_messages,
                        'context': prompt_context,
                        'tools': definitions, 'model': config['model'],
                        'max_iterations': limits['model_requests'],
                        'max_tokens': min(32000, limits['output_tokens']),
                        'thinking_mode': config.get('thinking_mode', 'enabled')})
                    emit('status', 'Hermes 已就绪，正在结合本轮资料处理任务。')
                    while True:
                        if reader is None:
                            reader = asyncio.create_task(_read(process))
                        waiting = [reader, *([model_task] if model_task else [])]
                        done, _ = await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
                        if model_task in done:
                            await model_task
                            model_task = None
                        if reader not in done:
                            continue
                        value = await reader
                        reader = None
                        await check()
                        kind = value['type']
                        if kind == 'model_request':
                            if model_task is not None:
                                raise _protocol_error()
                            model_task = asyncio.create_task(forward(value, http))
                        elif kind == 'tool_call':
                            if model_task is not None:
                                await model_task
                                model_task = None
                            identifier = identify(value)
                            if bridge.calls >= limits['tool_calls']:
                                raise _Pause('tool_calls')
                            result = await bridge.call(value.get('name'), value.get('arguments'))
                            if structured_task:
                                failure = read_failure(value.get('name'), result)
                                if failure:
                                    read_failures += 1
                                    last_read_error = failure
                                last_coverage = bridge.coverage()
                            emit('dependency_records', {doc_id: scope[doc_id]
                                 for doc_id in bridge.tools.exposed_document_ids})
                            if purpose == 'preorganize' and bridge.proposal is not None:
                                # 有效提交就是结果边界；不让引擎发起无价值的总结请求。
                                await asyncio.to_thread(bridge.verify)
                                return output(bridge.proposal['summary'])
                            if purpose == 'card' and bridge.card is not None:
                                await asyncio.to_thread(bridge.verify)
                                return output(bridge.card['summary'])
                            await send({'type': 'tool_result', 'id': identifier, **result})
                        elif kind == 'answer_preview':
                            text = value.get('text')
                            if not isinstance(text, str) or len(text) > MAX_ANSWER_CHARACTERS:
                                raise _protocol_error()
                            if text:
                                last_preview = text
                                emit('answer_preview', text)
                        elif kind == 'structured_recovery':
                            expected = ('propose_organization' if purpose == 'preorganize' else 'submit_document_card')
                            if not structured_task or recovery_attempts or value.get('terminal_tool') != expected:
                                raise _protocol_error()
                            recovery_attempts = 1
                            structured_report('recovery')
                            if last_read_error and not bridge.tools.sources:
                                emit('status', '资料读取尚未完成，正在尝试调整读取方式。')
                            else:
                                emit('status', '已保留阅读结果，正在补齐整理方案。' if purpose == 'preorganize'
                                     else '已保留阅读结果，正在补齐资料卡。')
                        elif kind == 'completed':
                            if model_task is not None:
                                await model_task
                                model_task = None
                            if purpose == 'preorganize':
                                structured_report('incomplete')
                                raise AIError(502, 'AI_ORGANIZATION_INCOMPLETE',
                                    incomplete_message())
                            if purpose == 'card':
                                structured_report('incomplete')
                                raise AIError(502, 'AI_CARD_INCOMPLETE',
                                    incomplete_message())
                            answer = value.get('answer')
                            if isinstance(answer, str) and len(answer) > MAX_ANSWER_CHARACTERS:
                                raise AIError(422, 'PARTNER_ANSWER_TOO_LONG', '本次回答超过单条消息容量，已保留生成中的内容；可让 AI 分段继续。')
                            if not isinstance(answer, str) or not answer.strip():
                                raise AIError(502, 'AI_INVALID_RESULT', 'AI 未能完成回答，已生成的内容仍会保留，可以继续提问。')
                            known = {source['source_id'] for source in bridge.tools.sources}
                            remember_history(value.get('engine_history', []))
                            if any(cited not in known for cited in re.findall(r'\[(S\d+)\]', answer)):
                                answer = re.sub(r'\[(S\d+)\]', lambda match:
                                    match.group(0) if match.group(1) in known else '（引用未核实）', answer)
                                citation_warning = '部分引用未能核实，相关标记已移除；正文分析请结合实际资料判断。'
                                emit('warning', citation_warning)
                                if native_messages and native_messages[-1]['role'] == 'assistant':
                                    native_messages[-1]['content'] = answer

                            await asyncio.to_thread(bridge.verify)
                            return output(answer)
                        elif kind == 'error' and value.get('code') == 'HERMES_ANSWER_TOO_LONG':
                            raise AIError(422, 'PARTNER_ANSWER_TOO_LONG', '本次回答超过单条消息容量，已保留生成中的内容；可让 AI 分段继续。')
                        elif kind == 'error' and isinstance(value.get('code'), str) and value['code'] in {
                                'HERMES_REPETITION_STOPPED', 'HERMES_CONTEXT_LIMIT',
                                'HERMES_INTERRUPTED', 'HERMES_INCOMPLETE', 'HERMES_EXECUTION_FAILED'}:
                            code, description = {
                                'HERMES_REPETITION_STOPPED': ('AI_REPETITION_STOPPED',
                                    '模型开始连续重复输出，本次生成已停止；已显示的内容仍保留，可以补充问题继续。'),
                                'HERMES_CONTEXT_LIMIT': ('AI_CONTEXT_LIMIT',
                                    '当前对话内容超过模型可处理范围，已生成内容仍保留；可从具体问题继续。'),
                                'HERMES_INTERRUPTED': ('AI_RESPONSE_INTERRUPTED',
                                    '本次模型生成被中断，已生成内容仍保留，可以继续提问。'),
                                'HERMES_INCOMPLETE': ('AI_RESPONSE_INCOMPLETE',
                                    '模型没有完成本次回答，已生成内容仍保留，可以继续提问。'),
                                'HERMES_EXECUTION_FAILED': ('AI_ENGINE_FAILED',
                                    '本机 AI 引擎未完成本次处理，已生成内容仍保留；请重试或检查引擎状态。'),
                            }[value['code']]
                            raise AIError(502, code, description)
                        elif kind == 'paused':
                            remember_history(value.get('engine_history', []))
                            raise _Pause('model_requests')
                        else:
                            raise _protocol_error()
    except (_Pause, TimeoutError, PartnerNoProgress, PartnerEvidenceLimit) as exc:
        reason = (exc.reason if isinstance(exc, _Pause) else 'time_seconds' if isinstance(exc, TimeoutError)
                  else 'no_progress' if isinstance(exc, PartnerNoProgress) else 'evidence_limit')
        # 解析线程取消后可能迟到；恢复最近完整工具边界，隔离其写入。
        restored = PartnerTools(engine=engine, root=root, scope=scope, guard=guard, emit=emit)
        await asyncio.to_thread(restored.restore_checkpoint, bridge.last_checkpoint)
        bridge.tools = restored
        await asyncio.to_thread(bridge.verify)
        raise PartnerPaused(reason, output(last_preview or f'已保留 {len(bridge.tools.sources)} 条阅读依据、'
            f'{len(bridge.artifacts)} 份草稿，正在衔接后续处理。', coverage=last_coverage)) from None
    except httpx2.TimeoutException:
        raise AIError(504, 'AI_TIMEOUT', 'AI 服务响应超时，已生成内容仍会保留。本次可能已产生费用，可以稍后继续提问。') from None
    except httpx2.HTTPError:
        raise AIError(503, 'AI_NETWORK_ERROR', '无法连接 AI 服务，请检查设置；本轮不会自动重试。') from None
    except (OSError, BrokenPipeError, ConnectionResetError):
        raise AIError(503, 'HERMES_UNAVAILABLE', '本机 AI 引擎未能正常运行，请检查安装；不会自动重试。') from None
    finally:
        async def cleanup():
            for pending in (reader, model_task):
                if pending is not None:
                    pending.cancel()
            await asyncio.gather(*(pending for pending in (reader, model_task) if pending is not None),
                                 return_exceptions=True)
            if process is not None:
                await _stop(process)
            if directory is not None:
                directory.cleanup()
        # 切库与退出可能先后取消同一任务；清理必须完成，随后再传播取消。
        cleanup_task = asyncio.create_task(cleanup())
        cancelled = False
        while True:
            try:
                await asyncio.shield(cleanup_task)
                break
            except asyncio.CancelledError:
                cancelled = True
        if cancelled:
            raise asyncio.CancelledError
