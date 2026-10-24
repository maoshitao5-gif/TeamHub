"""任务总预算；自然聊天的内部执行段共享总额，旧接口保留单段契约。"""
from fastapi import HTTPException

FIELDS = ('model_requests', 'tool_calls', 'output_tokens', 'time_seconds')
PRESETS = {
    'quick': dict(zip(FIELDS, (12, 24, 16000, 180))),
    'standard': dict(zip(FIELDS, (32, 80, 48000, 600))),
    'deep': dict(zip(FIELDS, (64, 160, 96000, 1800))),
}
BOUNDS = dict(zip(FIELDS, ({'min': 4, 'max': 128}, {'min': 8, 'max': 256},
                         {'min': 4000, 'max': 128000}, {'min': 60, 'max': 3600})))
LABELS = {'quick': '快速', 'standard': '标准', 'deep': '深入'}
CHAT_BUDGET = {'mode': 'custom', **dict(zip(FIELDS, (64, 160, 128000, 1800)))}
CHAT_SEGMENT_LIMITS = {'model_requests': 24, 'tool_calls': 64}
PAUSE_MESSAGES = {
    'model_requests': '已达到本段模型请求预算，阅读进度和已有成果已保留；确认预算后可继续。',
    'tool_calls': '已达到本段工具调用预算，阅读进度和已有成果已保留；确认后可继续。',
    'output_tokens': '已达到本段模型输出预算，已有证据已保留；确认后可继续。',
    'time_seconds': '已达到本段执行时间，已有进度已保留；确认后可继续。',
    'no_progress': '连续操作未获得新内容，已暂停避免重复计费；请查看阶段成果并决定是否继续。',
    'evidence_limit': '本任务已达到可安全保留的证据容量；请保存阶段成果，缩小范围后新建问题。',
}


def budget_policy():
    return {'default_mode': 'standard', 'presets': {
        key: {**value, 'label': LABELS[key]} for key, value in PRESETS.items()},
        'bounds': {key: dict(value) for key, value in BOUNDS.items()}}


def normalize_budget(value=None):
    value = value if value is not None else {'mode': 'standard'}
    valid = isinstance(value, dict) and set(value) <= {'mode', *FIELDS}
    mode = value.get('mode', 'standard') if isinstance(value, dict) else None
    if valid and mode in PRESETS and not (set(value) - {'mode'}):
        return {'mode': mode, **PRESETS[mode]}
    if valid and mode == 'custom' and set(value) == {'mode', *FIELDS}:
        if all(type(value[key]) is int and BOUNDS[key]['min'] <= value[key] <= BOUNDS[key]['max']
               for key in FIELDS):
            return dict(value)
    raise HTTPException(422, detail={'error': {'code': 'PARTNER_INVALID_BUDGET',
        'message': '请选择任务模式；自定义预算须填写界限内的四个正整数。'}})


class PartnerPaused(Exception):
    def __init__(self, reason, output):
        self.reason, self.output = reason, output
        super().__init__(PAUSE_MESSAGES[reason])
