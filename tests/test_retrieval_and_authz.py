import time

import backend.retrieval.hybrid as hybrid
from backend.authz.policy import AuthorizationScope, AuthorizedDoc
from backend.retrieval.cache import TTLCache, cache_key, normalize_query
from backend.retrieval.hybrid import dense_filter, reciprocal_rank_fusion


def _scope(tenant_id=1, **versions):
    docs = {int(k[1:]): AuthorizedDoc(int(k[1:]), v, f"{k}.pdf", 1, None) for k, v in versions.items()}
    return AuthorizationScope(tenant_id=tenant_id, user_id=5, docs=docs)


def test_rrf_rewards_agreement_between_retrievers():
    fused = reciprocal_rank_fusion([["a", "b", "c"], ["c", "a", "d"]], k=60)
    ids = [cid for cid, _ in fused]
    assert ids[0] == "a"  # rank 1 + rank 2
    assert ids.index("c") < ids.index("b")  # c appears in both lists
    assert set(ids) == {"a", "b", "c", "d"}


def test_scope_allows_only_active_version_in_same_tenant():
    scope = _scope(d10=7, d11=3)
    assert scope.allows(1, 10, 7)
    assert not scope.allows(1, 10, 6)   # stale version
    assert not scope.allows(2, 10, 7)   # other tenant
    assert not scope.allows(1, 12, 1)   # not granted


def test_scope_fingerprint_changes_with_permissions_or_versions():
    base = _scope(d10=7, d11=3).fingerprint
    assert _scope(d10=7, d11=3).fingerprint == base
    assert _scope(d10=8, d11=3).fingerprint != base   # new active version
    assert _scope(d10=7).fingerprint != base          # access revoked
    assert _scope(tenant_id=2, d10=7, d11=3).fingerprint != base


def test_dense_filter_is_built_from_scope(monkeypatch):
    flt, by_keys = dense_filter(_scope(d10=7, d11=3))
    assert by_keys
    clauses = flt["$and"]
    assert {"tenant_id": {"$eq": 1}} in clauses
    assert {"doc_version_key": {"$in": ["10:7", "11:3"]}} in clauses

    monkeypatch.setattr(hybrid, "DENSE_FILTER_MAX_KEYS", 1)
    flt, by_keys = dense_filter(_scope(d10=7, d11=3))
    assert not by_keys and all("doc_version_key" not in c for c in flt["$and"])


def test_ttl_cache_expiry_and_tag_invalidation():
    cache = TTLCache(max_entries=2, ttl_seconds=1.0)
    cache.set("a", 1, tag=1)
    cache.set("b", 2, tag=2)
    assert cache.get("a") == 1
    assert cache.invalidate_tag(1) == 1 and cache.get("a") is None
    cache.set("c", 3)
    cache.set("d", 4)
    assert cache.get("b") is None  # evicted (LRU, max 2)
    time.sleep(1.1)
    assert cache.get("d") is None  # expired


def test_cache_key_depends_on_every_part():
    base = dict(tenant=1, scope="x", q=normalize_query("  How  many PTO days "))
    assert cache_key(**base) == cache_key(tenant=1, scope="x", q="how many pto days")
    assert cache_key(**base) != cache_key(**{**base, "scope": "y"})
