import pytest

from assessment_agent.pricing import Usage


def test_input_rate():
    u = Usage("claude-sonnet-4-6", input_tokens=1_000_000, output_tokens=0)
    assert u.cost_usd == pytest.approx(3.0)


def test_output_rate():
    u = Usage("claude-opus-4-8", input_tokens=0, output_tokens=1_000_000)
    assert u.cost_usd == pytest.approx(25.0)


def test_cache_read_discount():
    u = Usage("claude-sonnet-4-6", input_tokens=0, output_tokens=0,
              cache_read_input_tokens=1_000_000)
    assert u.cost_usd == pytest.approx(0.3)  # 3.0 * 0.1


def test_unknown_model_is_unpriced():
    u = Usage("made-up-model", input_tokens=100, output_tokens=100)
    assert u.priced is False
    assert u.cost_usd == 0.0
