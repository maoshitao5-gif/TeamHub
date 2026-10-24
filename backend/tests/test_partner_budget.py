"""AI 伙伴预算是明确授权的本段预算，验证不能使用布尔值/隐式放大。"""
import pytest
from fastapi import HTTPException


def test_default_budget_is_usable_for_longer_real_reading():
    from backend.app.core.partner_budget import normalize_budget, budget_policy
    value = normalize_budget(None)
    assert value['mode'] == 'standard'
    assert value['model_requests'] == 32
    assert value['tool_calls'] == 80
    assert set(budget_policy()['presets']) == {'quick', 'standard', 'deep'}


@pytest.mark.parametrize('value', [
    {'mode': 'custom', 'model_requests': True, 'tool_calls': 8, 'output_tokens': 4000, 'time_seconds': 60},
    {'mode': 'custom', 'model_requests': 3, 'tool_calls': 8, 'output_tokens': 4000, 'time_seconds': 60},
    {'mode': 'custom', 'model_requests': 4, 'tool_calls': 8, 'output_tokens': 4000},
    {'mode': 'quick', 'model_requests': 128}, {'mode': 'unlimited'},
])
def test_budget_rejects_invalid_or_ambiguous_values(value):
    from backend.app.core.partner_budget import normalize_budget
    with pytest.raises(HTTPException):
        normalize_budget(value)


def test_custom_budget_round_trips_exactly():
    from backend.app.core.partner_budget import normalize_budget
    value = {'mode': 'custom', 'model_requests': 4, 'tool_calls': 8,
             'output_tokens': 4000, 'time_seconds': 60}
    assert normalize_budget(value) == value
