"""
Retrieval parameter sweep: candidate_k (hybrid recall depth) x final top_k x
reranking on/off, ranked by a composite of Recall@10, nDCG@5 and MRR.

Usage:
    python -m backend.evaluation.tuning_runner
    python -m backend.evaluation.tuning_runner --quick --save tuning_results.json
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from backend.auth.database import init_db  # noqa: E402
from backend.auth.principal import load_principal_by_username  # noqa: E402
from backend.authz.policy import resolve_scope  # noqa: E402
from backend.db.connection import close_pools  # noqa: E402
from backend.evaluation.eval_runner import EVAL_USER, evaluate_retrieval, load_cases, mean  # noqa: E402

PARAM_GRID_FULL = [
    {"candidate_k": ck, "top_k": tk, "rerank": rr}
    for ck in (20, 50, 100) for tk in (3, 5, 8) for rr in (True, False)
]
PARAM_GRID_QUICK = [
    {"candidate_k": 20, "top_k": 5, "rerank": True},
    {"candidate_k": 50, "top_k": 5, "rerank": True},
    {"candidate_k": 50, "top_k": 5, "rerank": False},
    {"candidate_k": 100, "top_k": 5, "rerank": True},
]


async def run_config(cases, scope, params: Dict[str, Any], delay: float) -> Dict[str, Any]:
    results = []
    for case in cases:
        results.append(await evaluate_retrieval(
            case, scope, params["candidate_k"], params["top_k"], use_rerank=params["rerank"]
        ))
        await asyncio.sleep(delay)
    summary = {
        "recall_at_10": mean([r.get("recall_at_10") for r in results]),
        "ndcg_at_5": mean([r.get("ndcg_at_5") for r in results]),
        "mrr": mean([r.get("mrr") for r in results]),
        "avg_latency_s": mean([r.get("latency") for r in results]),
    }
    summary["composite"] = round(
        0.4 * (summary["recall_at_10"] or 0) + 0.4 * (summary["ndcg_at_5"] or 0) + 0.2 * (summary["mrr"] or 0), 4
    )
    return summary


async def main_async(args: argparse.Namespace) -> None:
    await init_db()
    principal = await load_principal_by_username(args.username)
    if principal is None:
        sys.exit(f"Unknown user '{args.username}'")
    scope = await resolve_scope(principal)
    cases = [c for c in load_cases() if c.get("evidence")]
    grid = PARAM_GRID_QUICK if args.quick else PARAM_GRID_FULL

    results: List[Dict[str, Any]] = []
    for i, params in enumerate(grid, start=1):
        print(f"[{i}/{len(grid)}] {params}")
        metrics = await run_config(cases, scope, params, args.delay)
        results.append({"params": params, "metrics": metrics})
        print(f"   -> {metrics}")

    results.sort(key=lambda r: r["metrics"]["composite"], reverse=True)
    print("\nTop configurations:")
    for r in results[:5]:
        print(f"  {r['params']}  composite={r['metrics']['composite']}")
    Path(args.save).write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nSaved to {args.save}")
    await close_pools()


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG retrieval parameter tuning")
    parser.add_argument("--save", default="tuning_results.json")
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--username", default=EVAL_USER)
    parser.add_argument("--delay", type=float, default=1.5)
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
