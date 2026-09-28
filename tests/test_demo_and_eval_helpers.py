import pytest
from fastapi import HTTPException

from backend.common import rate_limit
from backend.evaluation.eval_runner import answer_correct, contains_evidence, percentile, relevance


def test_answer_correct_requires_every_group():
    required = [["5", "five"], ["March 31"]]
    assert answer_correct("Up to **5** days carry over and expire on March 31 [S1].", required)
    assert answer_correct("Five days, expiring March  31.", required)
    assert not answer_correct("Up to 5 days carry over.", required)


def test_evidence_matching_ignores_case_and_whitespace():
    assert contains_evidence("| Silver PPO |  $135 | $1,400 |", "| silver ppo | $135 | $1,400 |")
    assert relevance([{"text": "a"}, {"text": "gold passage here"}], "Gold passage") == [0, 1]
    assert not contains_evidence("anything", "")


def test_percentile():
    assert percentile([10, 20, 30, 40, 50], 50) == 30
    assert percentile([10, 20, 30, 40, 50], 95) == 50
    assert percentile([], 50) is None


def test_rate_limit_window(monkeypatch):
    rate_limit.reset()
    for _ in range(3):
        rate_limit.check("t", "k", limit=3, window_seconds=60)
    with pytest.raises(HTTPException) as exc:
        rate_limit.check("t", "k", limit=3, window_seconds=60)
    assert exc.value.status_code == 429 and "Retry-After" in exc.value.headers
    rate_limit.check("t", "other-key", limit=3, window_seconds=60)  # separate keys are independent
    rate_limit.reset()


def test_daily_budget():
    rate_limit.reset()
    rate_limit.consume_daily_budget("b", budget=2)
    rate_limit.consume_daily_budget("b", budget=2)
    with pytest.raises(HTTPException):
        rate_limit.consume_daily_budget("b", budget=2)
    rate_limit.reset()
