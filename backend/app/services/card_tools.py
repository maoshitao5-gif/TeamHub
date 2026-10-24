"""Single-document understanding shares the preorganization reading boundary."""
import asyncio
from pathlib import Path

from pydantic import Field, ValidationError

from backend.app.core.ai_client import AIError
from backend.app.services.card_schema import CardContent
from backend.app.services.hermes_tools import Arguments
from backend.app.services.organization_tools import OrganizationTools, READ_TOOLS


class CardContext(Arguments):
    document_id: str = Field(min_length=1, max_length=100)
    existing_card: dict | None = None
    annotation: dict | None = None


CARD_TOOLS = {**READ_TOOLS, 'submit_document_card': (
    CardContent, '提交当前资料的结构化理解并结束任务；内容概览、导航、事实与检索线索分开，'
    '关键事实和导航填写本任务真实来源编号，保留未读和不确定内容；不修改或整理原文件。')}


def card_instructions(is_folder=False, kind=None):
    skill = Path(__file__).resolve().parents[1] / 'skills' / 'document-understanding' / 'SKILL.md'
    try:
        content = skill.read_text(encoding='utf-8')
        from backend.app.services.object_skills import object_instructions
        if kind is not None or is_folder:
            content += '\n\n' + object_instructions(kind or 'folder')
        return content
    except (OSError, UnicodeError, ValueError):
        raise AIError(503, 'CARD_SKILL_MISSING', '资料理解工作规则未完整安装，请重新构建应用。') from None


class CardTools(OrganizationTools):
    def __init__(self, *, purpose_context, **kwargs):
        context = CardContext.model_validate(purpose_context)
        super().__init__(purpose_context={**context.model_dump(exclude_none=True),
                                          'tags': [], 'folders': []}, **kwargs)
        self.context = {**context.model_dump(exclude_none=True), 'object_kind': self.object_kind}

    def definitions(self):
        return [{'type': 'function', 'function': {'name': name, 'description': description,
                'parameters': model.model_json_schema()}}
                for name, (model, description) in self._tool_map().items()]

    def _tool_map(self):
        return {**self.read_tools, 'submit_document_card': CARD_TOOLS['submit_document_card']}

    async def call(self, name, arguments):
        if not isinstance(name, str) or name not in self._tool_map():
            await asyncio.to_thread(self.verify)
            self.calls += 1
            return {'result': {'ok': False, 'error': '此工具不在资料理解任务的可用范围内。'}}
        if name == 'submit_document_card':
            await asyncio.to_thread(self.verify)
            self.calls += 1
            self.submission_attempts += 1
            try:
                card = self.validate_card(arguments, field_prefix='')
            except (ValueError, ValidationError) as exc:
                return {'result': self.submission_failure(exc, card_only=True)}
            self.card = card
            self.last_submission_error = None
            self.emit('status', '资料卡已生成，正在保存理解成果。')
            return {'result': {'ok': True, 'status': 'card_ready'}}
        # Reuse the exact same reading/coverage path; this class only changes the
        # permitted terminal action, never document access or source accounting.
        return await super().call(name, arguments)
