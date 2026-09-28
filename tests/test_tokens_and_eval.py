import pytest

import backend.auth.tokens as tokens
from backend.evaluation.eval_runner import mrr, ndcg_at_k, recall_at_k, regression_gate


def test_token_round_trip(monkeypatch):
    monkeypatch.setattr(tokens, "JWT_SECRET", "test-secret-0123456789abcdef-0123456789")
    token = tokens.issue_token(user_id=3, tenant_id=8, expires_minutes=5)
    assert tokens.decode_token(token) == (3, 8)


def test_tampered_or_expired_tokens_are_rejected(monkeypatch):
    monkeypatch.setattr(tokens, "JWT_SECRET", "test-secret-0123456789abcdef-0123456789")
    token = tokens.issue_token(3, 8)
    with pytest.raises(tokens.TokenError):
        tokens.decode_token(token[:-2] + "xx")
    expired = tokens.issue_token(3, 8, expires_minutes=-1)
    with pytest.raises(tokens.TokenError):
        tokens.decode_token(expired)
    monkeypatch.setattr(tokens, "JWT_SECRET", "other-secret-0123456789abcdef-0123456789")
    with pytest.raises(tokens.TokenError):
        tokens.decode_token(token)


def test_retrieval_metrics():
    rels = [0, 1, 0, 1]
    assert recall_at_k(rels, 1, None) == 0.0
    assert recall_at_k(rels, 2, None) == 1.0          # document-level hit
    assert recall_at_k(rels, 4, n_gold=4) == 0.5      # chunk-level gold
    assert mrr(rels) == 0.5
    assert 0 < ndcg_at_k(rels, 5) < 1
    assert ndcg_at_k([1, 1, 0], 5) == 1.0


def test_regression_gate(tmp_path):
    baseline = tmp_path / "baseline.json"
    baseline.write_text('{"summary": {"recall_at_10": 0.90}}')
    assert regression_gate({"recall_at_10": 0.89, "faithfulness": 0.9}, str(baseline)) == []
    failures = regression_gate({"recall_at_10": 0.85, "faithfulness": 0.80}, str(baseline))
    assert len(failures) == 2
