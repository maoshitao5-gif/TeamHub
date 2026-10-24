"""旧 SDK 的回归对照适配器，仅测试使用，不随应用发布。

保留已有版本/授权/预算回归行为；生产唯一引擎为 Hermes。
"""
import asyncio
import hashlib
import json
import re
import time

import httpx2
from fastapi import HTTPException
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field
from pydantic_core import from_json
from pydantic_ai import Agent, BinaryContent, ToolReturn
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError, UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.messages import (ModelRequest, ModelResponse, PartDeltaEvent, PartEndEvent,
                                  PartStartEvent, TextPart, ToolCallPart, UserPromptPart)
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import RunUsage, UsageLimits

from backend.app.core.ai_client import AIError
from backend.app.core.partner_budget import FIELDS, PAUSE_MESSAGES, PartnerPaused, normalize_budget
from backend.app.models import generate_uuid
from backend.app.services.partner_tools import PartnerEvidenceLimit, PartnerNoProgress, PartnerTools

from backend.app.core.partner_protocol import (
    BoundedTransport, _instructions, _validate_draft, _draft_with_sources,
)


class PartnerAnswer(BaseModel):
    model_config = ConfigDict(extra='forbid')
    answer: str = Field(min_length=1, max_length=18000, description='面向用户的中文回答，事实引用使用 [S1] 等已实际读取的来源编号')
    source_ids: list[str] = Field(default_factory=list, max_length=50,
                                 description='回答实际依据的来源编号，仅允许读取工具返回的 source_id')


def _history_messages(history):
    result = []
    for message in history:
        if message['role'] == 'user':
            result.append(ModelRequest(parts=[UserPromptPart(message['content'])]))
        elif message['role'] == 'assistant':
            result.append(ModelResponse(parts=[TextPart(message['content'])]))
    return result


async def run_agent(*, message, history, scope, config, key, engine, root, guard, emit, limits,
                    history_dependencies=None, model_override=None, transport_override=None,
                    checkpoint=None, previous_artifacts=None, previous_answer=None, context=None):
    """一次受限任务可包含多次模型与工具调用；不持久化 SDK 原始消息。"""
    guard()
    tools = PartnerTools(engine=engine, root=root, scope=scope, guard=guard, emit=emit)
    unreadable_documents = set()
    if checkpoint:
        tools.restore_checkpoint(checkpoint)
    limits = {**{key: normalize_budget()[key] for key in FIELDS}, **limits}
    started = time.monotonic()
    usage = RunUsage()
    request_attempts, metered_responses = 0, 0
    metered_input_tokens, metered_output_tokens = 0, 0
    attempt_limit = limits['model_requests'] - 1
    def before_send():
        nonlocal request_attempts
        if request_attempts >= attempt_limit:
            raise UsageLimitExceeded('model request attempt budget exhausted')
        request_attempts += 1
    def after_response(content):
        nonlocal metered_responses, metered_input_tokens, metered_output_tokens
        # 流式供应商可在多帧发送累计用量；一份响应只采用最后一份完整快照。
        try:
            payload = json.loads(content)
        except (ValueError, TypeError):
            payload = {}
            for line in content.splitlines():
                if line.startswith(b'data:'):
                    try:
                        frame = json.loads(line[5:].strip())
                        if isinstance(frame, dict) and frame.get('usage'):
                            payload = frame
                    except (ValueError, TypeError):
                        continue
        try:
            tokens = payload.get('usage') or {}
            input_count, output_count = tokens.get('prompt_tokens'), tokens.get('completion_tokens')
            if type(input_count) is int and type(output_count) is int and min(input_count, output_count) >= 0:
                metered_responses += 1
                metered_input_tokens += input_count
                metered_output_tokens += output_count
        except (ValueError, TypeError, AttributeError):
            pass  # 缺少供应商用量时明确标记未知，不能推算为零费用。
    def attempted_requests():
        return usage.requests if model_override is not None else request_attempts
    history_dependencies = history_dependencies or {}
    if any(document_id not in scope or scope[document_id] != record
           for document_id, record in history_dependencies.items()):
        raise AIError(409, 'PARTNER_HISTORY_CHANGED', '历史回答依赖的资料不在本轮有效范围内，请重新准备')
    preview_ids = list(scope)[:30]
    tools.exposed_document_ids.update([*history_dependencies, *preview_ids])
    last_checkpoint = tools.export_checkpoint()
    artifacts = list(previous_artifacts or [])
    def verify_exposed_sources():
        guard()
        for document_id in tools.exposed_document_ids.copy():
            tools._assert_current(document_id)
    def _output(answer):
        summary = answer[:1800]
        return {'answer': answer, 'sources': list(tools.sources), 'artifacts': artifacts,
                'read_document_ids': list({source['document_id'] for source in tools.sources}),
                'dependency_records': {item: scope[item] for item in tools.exposed_document_ids},
                'checkpoint': tools.export_checkpoint(), 'context_summary': summary, 'progress': tools.progress,
                'usage': {'model_requests': attempted_requests(), 'tool_calls': usage.tool_calls,
                          'input_tokens': usage.input_tokens if model_override is not None else metered_input_tokens,
                          'output_tokens': usage.output_tokens if model_override is not None else metered_output_tokens,
                          'token_usage_complete': model_override is not None or metered_responses == request_attempts,
                          'unmetered_requests': 0 if model_override is not None else request_attempts - metered_responses,
                          'time_seconds': round(time.monotonic() - started, 3)}}
    http = None
    client = None
    is_deepseek = config['model'].lower().startswith('deepseek')
    thinking_enabled = is_deepseek and config.get('thinking_mode', 'enabled') != 'disabled'
    if model_override is None:
        http = httpx2.AsyncClient(transport=BoundedTransport(verify_exposed_sources, transport_override,
            before_send=before_send, after_response=after_response),
            timeout=httpx2.Timeout(60, connect=10), trust_env=False, follow_redirects=False)
        client = AsyncOpenAI(base_url=config['base_url'].rstrip('/') + '/', api_key=key,
                             max_retries=0, timeout=60, http_client=http)
        profile = OpenAIModelProfile(supports_tools=True, openai_supports_strict_tool_definition=False,
                                     openai_chat_supports_max_completion_tokens=False)
        if is_deepseek:
            profile.update(openai_chat_thinking_field='reasoning_content',
                           openai_chat_send_back_thinking_parts='field',
                           supports_forced_tool_choice=False)
        model = OpenAIChatModel(config['model'], provider=OpenAIProvider(openai_client=client), profile=profile)
    else:
        model = model_override
    reserve_tokens = min(2000, limits['output_tokens'] // 4)
    # DeepSeek 的 max_tokens 同时包含推理和回答，思考模式需留足实际输出空间。
    request_token_cap = 32000 if thinking_enabled else 6000
    model_settings = {'temperature': .2, 'max_tokens': min(request_token_cap, limits['output_tokens'] - reserve_tokens),
                      'parallel_tool_calls': False}
    if is_deepseek:
        model_settings['extra_body'] = {'thinking': {'type': 'enabled' if thinking_enabled else 'disabled'}}
    def step_settings(context):
        if attempted_requests() >= attempt_limit:
            raise UsageLimitExceeded('model request attempt budget exhausted')
        remaining = limits['output_tokens'] - reserve_tokens - context.usage.output_tokens
        if remaining <= 0:
            raise UsageLimitExceeded('output token budget exhausted')
        return {**model_settings, 'max_tokens': min(request_token_cap, remaining)}
    try:
        agent = Agent(model, output_type=PartnerAnswer, instructions=_instructions(),
                      retries=0, model_settings=step_settings, name='TeamHub智能伙伴')

        async def preview_stream(context, events):
            # SDK 同时传递模型与工具事件。只从结构化最终结果中取 answer，
            # ThinkingPart 和普通工具参数绝不可作为可展示/持久化的思考内容。
            parts, latest, shown, last_emit = {}, '', '', 0.0
            async for event in events:
                if isinstance(event, PartStartEvent):
                    parts[event.index] = event.part
                elif isinstance(event, PartDeltaEvent) and event.index in parts:
                    parts[event.index] = event.delta.apply(parts[event.index])
                elif isinstance(event, PartEndEvent):
                    parts[event.index] = event.part
                else:
                    continue
                part = parts.get(event.index)
                if not isinstance(part, ToolCallPart) or part.tool_name != 'final_result':
                    continue
                try:
                    data = part.args if isinstance(part.args, dict) else from_json(
                        part.args or '', allow_partial='trailing-strings')
                    answer = data.get('answer') if isinstance(data, dict) else None
                except (ValueError, TypeError):
                    continue
                if not isinstance(answer, str) or not answer or len(answer) > 18000:
                    continue
                latest = answer
                now = time.monotonic()
                if latest != shown and (not shown or now - last_emit >= .08 or len(latest) - len(shown) >= 240):
                    guard()
                    emit('answer_preview', latest)
                    shown, last_emit = latest, now
            if latest and latest != shown:
                guard()
                emit('answer_preview', latest)

        # 显式注入的模型可只实现非流式 request（如 FunctionModel）；不能为兼容再次外发。
        stream_settings = {'event_stream_handler': preview_stream} if model_override is None else {}

        async def observe_tool(function, *arguments):
            nonlocal last_checkpoint
            try:
                result = await asyncio.to_thread(function, *arguments)
                last_checkpoint = tools.export_checkpoint()
                if isinstance(result, dict) and (usage.requests >= limits['model_requests'] - 2
                        or usage.tool_calls >= limits['tool_calls'] - 2):
                    result = {**result, 'budget_notice': '本段接近预算，请整理已读内容与未完成部分；需要更多阅读可暂停后继续。'}
                return result
            except ValueError as exc:
                # 这些边界必须保留证据并暂停，不能作为普通工具失败继续耗费请求。
                if isinstance(exc, (PartnerNoProgress, PartnerEvidenceLimit)):
                    raise
                # 能力/格式限制作为观察返回，允许模型在已授权预算内改变策略。
                # 资料/库/取消失效仍由守卫终止，不能把失败当作继续外发旧资料的许可。
                verify_exposed_sources()
                emit('warning', '本次资料读取未完成，正在选择其他可用方法')
                return {'ok': False, 'error': {'code': 'PARTNER_TOOL_UNAVAILABLE', 'message': str(exc)[:250]},
                        'next_steps': '可改用文字读取、查看PDF或图片原页、调整关键词/页码；无法读取时向用户说明缺口。'}

        @agent.tool_plain(sequential=True, retries=0)
        async def search_documents(query: str, limit: int = 8, offset: int = 0) -> dict:
            """在本轮授权资料中按名称、标签和已建立正文索引找候选，可自行换关键词补查。"""
            return await observe_tool(tools.search_documents, query, limit, offset)

        @agent.tool_plain(sequential=True, retries=0)
        async def read_document(document_id: str, offset: int = 0, limit: int = 60) -> dict:
            """读取候选资料已保存版本的定位原文，返回来源编号、覆盖范围和缺口，可分页补读。"""
            result = await observe_tool(tools.read_document, document_id, offset, limit)
            if isinstance(result, dict) and result.get('units') == [] and result.get('unit_count') == 0:
                unreadable_documents.add(document_id)
                result = {**result, 'reading_status': 'no_extractable_text',
                    'next_steps': '没有取得可引用原文。先用document_overview查看格式缺口及内嵌图片，'
                    '用read_embedded_images读取可用图片；PDF或独立图片用read_visual。'
                    '仍无法读取时，明确说明未读到正文，建议真实另存为PDF或受支持格式，不可自造来源。'}
                emit('warning', '本次未读到可引用文字，可继续检查内嵌图片或原页；这不代表资料没有内容')
            return result

        @agent.tool_plain(sequential=True, retries=0)
        async def document_overview(document_id: str) -> dict:
            """先查看资料结构、工作表行列和可用阅读方式，选择合适工具而不是逐格盲读。"""
            return await observe_tool(tools.document_overview, document_id)

        @agent.tool_plain(sequential=True, retries=0)
        async def read_table(document_id: str, sheet: str | None = None, start_row: int = 1,
                             row_count: int = 80, offset: int = 0) -> dict:
            """按工作表行块读取表格并保留坐标；遵循返回next_call可无遗漏续读长表格。"""
            return await observe_tool(tools.read_table, document_id, sheet, start_row, row_count, offset)

        @agent.tool_plain(sequential=True, retries=0)
        async def read_visual(document_id: str, pages: list[int] | None = None) -> ToolReturn:
            """查看PDF原始页面或独立图片，适合图纸、扫描页、图表及版面；每次最多3页，页码从1开始。"""
            result = await observe_tool(tools.read_visual, document_id, pages)
            if isinstance(result, dict):
                return ToolReturn(return_value=result)
            return ToolReturn(return_value=result.metadata, content=[
                BinaryContent(data=image.data, media_type=image.media_type) for image in result.images])

        @agent.tool_plain(sequential=True, retries=0)
        async def read_embedded_images(document_id: str, image_ids: list[str]) -> ToolReturn:
            """读取Office文档真实引用的内嵌图片；先查看document_overview的visual_assets，每次最多3张。图片不代表完整页面布局。"""
            result = await observe_tool(tools.read_embedded_images, document_id, image_ids)
            if isinstance(result, dict):
                return ToolReturn(return_value=result)
            return ToolReturn(return_value=result.metadata, content=[
                BinaryContent(data=image.data, media_type=image.media_type) for image in result.images])

        @agent.tool_plain(sequential=True, retries=0)
        async def create_draft(name: str, content: str, source_ids: list[str] | None = None) -> dict:
            """创建可供用户确认保存的Markdown或文本成果草稿，不修改原资料；最多3份，每份30000字。"""
            guard()
            if len(artifacts) >= 3:
                return {'error': '本轮最多生成3份草稿，请在回答中说明未生成的部分'}
            source_ids = list(dict.fromkeys([*(source_ids or []), *re.findall(r'\[(S\d+)\]', content)]))
            try:
                _validate_draft(name, content, tools.sources, source_ids)
                content = _draft_with_sources(content, source_ids, tools.sources)
            except ValueError as exc:
                return {'error': str(exc)}
            artifact = {'id': generate_uuid(), 'name': name.strip(), 'content': content,
                        'content_hash': hashlib.sha256(content.encode('utf-8')).hexdigest(),
                        'media_type': 'text/markdown' if name.lower().endswith('.md') else 'text/plain',
                        'source_ids': source_ids, 'saved_document_id': None}
            artifacts.append(artifact)
            emit('draft', f'已生成草稿：{artifact["name"]}')
            return {key: artifact[key] for key in ('id', 'name', 'content_hash', 'source_ids')}

        scope_summary = [{'id': key, 'name': scope[key]['name'], 'version_number': scope[key].get('version_number')}
                         for key in preview_ids]
        prompt = json.dumps({'question': message, 'authorized_document_count': len(scope),
            'authorized_documents_preview': scope_summary,
            'scope_notice': '仅可使用本轮工具搜索冻结范围；列表只是前30份预览，存在更多资料时请自主搜索。',
            'context': context,
            'continuation_context': {'evidence': checkpoint.get('evidence', []),
                'summary': previous_answer or '',
                'summary_notice': '这是此前的阶段推断与待办，不是原始资料；需以已读证据核对并继续完成原问题。',
                'read_ranges': checkpoint.get('read_ranges', []), 'progress': tools.progress,
                'previous_artifacts': artifacts,
                'notice': '本段继续原任务；这些是此前真实读取并保留的原文，保持source编号；只代表已读范围，并非资料全文。不要重复读取已保留的文字，按next_call或游标补读；视觉图像bytes未保留，可按需重新查看。'} if checkpoint and context is None else None}, ensure_ascii=False)
        try:
            # 为阶段整理预留一次模型请求、部分输出和时间；绝不在授权预算外另行调用。
            async with asyncio.timeout(max(1, limits['time_seconds'] - 5)):
                result = await agent.run(prompt, message_history=_history_messages(history), usage=usage,
                    usage_limits=UsageLimits(request_limit=limits['model_requests'] - 1,
                        tool_calls_limit=limits['tool_calls'],
                        output_tokens_limit=limits['output_tokens'] - reserve_tokens), **stream_settings)
        except (UsageLimitExceeded, TimeoutError, PartnerNoProgress, PartnerEvidenceLimit) as exc:
            if isinstance(exc, UsageLimitExceeded):
                detail = str(exc).lower()
                reason = 'output_tokens' if 'token' in detail else 'tool_calls' if 'tool' in detail else 'model_requests'
            elif isinstance(exc, TimeoutError):
                reason = 'time_seconds'
                # 被取消的解析线程可能稍后返回；使用上个完整工具边界的独立副本，
                # 不让它的迟到读取写进阶段证据或下一段任务。
                tools = PartnerTools(engine=engine, root=root, scope=scope, guard=guard, emit=emit)
                tools.restore_checkpoint(last_checkpoint)
            elif isinstance(exc, (PartnerNoProgress, PartnerEvidenceLimit)):
                reason = 'no_progress' if isinstance(exc, PartnerNoProgress) else 'evidence_limit'
            else:
                raise
            guard()
            verify_exposed_sources()
            emit('pause', PAUSE_MESSAGES[reason])
            paused_answer = (f'已保留 {len(tools.sources)} 条实际阅读来源、{len(artifacts)} 份草稿。'
                             '任务尚未完成，可查看下方阅读范围及阶段成果。')
            remaining_time = limits['time_seconds'] - (time.monotonic() - started)
            remaining_tokens = limits['output_tokens'] - usage.output_tokens
            if attempted_requests() < limits['model_requests'] and remaining_tokens >= 500 and remaining_time > 1:
                attempt_limit = limits['model_requests']
                summary_settings = {**model_settings, 'max_tokens': min(reserve_tokens, remaining_tokens)}
                if is_deepseek:
                    summary_settings['extra_body'] = {'thinking': {'type': 'disabled'}}
                summarizer = Agent(model, output_type=PartnerAnswer, instructions=_instructions() +
                    '\n仅根据提供的真实已读证据整理阶段成果和未完成事项；这是暂停阶段，明确尚未完成，不臆测未读资料。',
                    retries=0, model_settings=summary_settings)
                try:
                    async with asyncio.timeout(max(.1, remaining_time)):
                        summary = await summarizer.run(json.dumps({'purpose': 'progress_summary', 'question': message,
                            'pause_reason': reason, 'continuation_context': tools.export_checkpoint(),
                            'artifacts': artifacts}, ensure_ascii=False), usage=usage,
                            usage_limits=UsageLimits(request_limit=limits['model_requests'],
                                tool_calls_limit=limits['tool_calls'], output_tokens_limit=limits['output_tokens']),
                            **stream_settings)
                    known = {item['source_id'] for item in tools.sources}
                    if all(item in known for item in [*summary.output.source_ids,
                            *re.findall(r'\[(S\d+)\]', summary.output.answer)]):
                        paused_answer = summary.output.answer
                except (UsageLimitExceeded, TimeoutError, AIError, ModelAPIError, APIStatusError,
                        APIConnectionError, UnexpectedModelBehavior):
                    emit('warning', '阶段整理未生成有效分析；当前展示已保存的阅读进度，原文和草稿仍保留。')
            guard()
            verify_exposed_sources()
            raise PartnerPaused(reason, _output(paused_answer)) from None
        guard()
        output = result.output
        known = {source['source_id']: source for source in tools.sources}
        used = list(dict.fromkeys(output.source_ids))
        cited = re.findall(r'\[(S\d+)\]', output.answer)
        if any(source_id not in known for source_id in [*used, *cited]):
            message = ('本轮未取得可引用的原文或图像，AI 仍生成了来源编号，结果未采用；'
                       '请查看资料结构与内嵌图片，或真实另存为 PDF 后重试'
                       if unreadable_documents and not known else
                       'AI 回答包含无法对应原资料的引用，本轮结果未采用')
            raise AIError(502, 'PARTNER_INVALID_SOURCES', message)
        # 保留全部真实读取来源，模型不能通过省略引用绕过最终版本校验。
        verify_exposed_sources()
        return _output(output.answer)
    except (TimeoutError, APITimeoutError):
        raise AIError(504, 'AI_TIMEOUT', 'AI 处理超时，本轮已停止；可能已产生费用，请重新确认后重试') from None
    except (APIStatusError, ModelHTTPError) as exc:
        status = exc.status_code
        if status in {401, 403}:
            raise AIError(400, 'AI_AUTH_FAILED', 'AI 密钥无效或模型无访问权限，请检查设置') from None
        if status == 402:
            raise AIError(400, 'AI_INSUFFICIENT_BALANCE', 'AI 服务余额不足，请检查服务账号') from None
        if status == 429:
            raise AIError(429, 'AI_RATE_LIMITED', 'AI 服务请求较多，请稍后重新确认再试') from None
        if status == 400:
            raise AIError(422, 'AI_MODEL_CAPABILITY_REQUIRED', 'AI 服务未接受本轮请求，请确认所选模型支持工具调用；阅读原页还需要图像能力') from None
        raise AIError(503, 'AI_UNAVAILABLE', 'AI 服务暂时不可用，本轮不会自动重试') from None
    except (APIConnectionError, ModelAPIError) as exc:
        # SDK 会包装传输层异常；只恢复本机已定义的安全错误，不透出上游 body。
        cause = exc
        for _ in range(8):
            if isinstance(cause, (AIError, HTTPException)):
                raise cause from None
            if isinstance(cause, (APITimeoutError, httpx2.TimeoutException)):
                raise AIError(504, 'AI_TIMEOUT', 'AI 响应超时；本次可能已产生费用，请重新确认后重试') from None
            cause = cause.__cause__
            if cause is None:
                break
        raise AIError(503, 'AI_NETWORK_ERROR', '无法连接 AI 服务，请检查设置后重新确认；不会自动重试') from None
    except UnexpectedModelBehavior:
        raise AIError(502, 'AI_INVALID_RESULT', 'AI 返回未能通过结构或工具校验，本轮不会自动重试') from None
    finally:
        if client is not None:
            await client.close()
        elif http is not None:
            await http.aclose()
