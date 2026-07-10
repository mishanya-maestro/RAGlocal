
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import config  # noqa: E402
import database  # noqa: E402
import fts_index  # noqa: E402
import retrieval  # noqa: E402


def _norm_key(code: str, number) -> tuple[str, str]:
    return (str(code).strip(), str(number).strip())


def load_golden(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("cases", [])


def _mrr(rank_list, gold_set):
    for i, key in enumerate(rank_list):
        if key in gold_set:
            return 1.0 / (i + 1)
    return 0.0


def _dcg(rank_list, gold_set) -> float:
    score = 0.0
    for i, key in enumerate(rank_list):
        if key in gold_set:
            score += 1.0 / math.log2(i + 2)
    return score


def compute_metrics(
    top_k: int | None = None,
    pool: int | None = None,
    golden: Path | None = None,
) -> dict:
    """Возвращает retrieval-метрики в JSON-формате для Flask API."""
    if top_k is None:
        top_k = config.RETRIEVER_TOP_K
    if pool is None:
        pool = config.RERANK_POOL_SIZE
    if golden is None:
        golden = config.PROJECT_ROOT / "data" / "golden_eval.json"

    cases = load_golden(golden)
    if not cases:
        return {
            "n": 0,
            "k": top_k,
            "pool": pool,
            "metrics": {},
            "config": {},
            "per_case": [],
            "error": "Нет кейсов в golden-файле.",
        }

    retrieval.ensure_fts_ready()
    per_case = []
    totals = {
        "recall_at_k": 0.0,
        "mrr_at_k": 0.0,
        "precision_at_k": 0.0,
        "ndcg_at_k": 0.0,
        "recall_at_pool": 0.0,
    }
    evaluated = 0

    for case in cases:
        query = case["query"]
        gold = {_norm_key(g["code"], g["number"]) for g in case.get("gold", [])}
        if not gold:
            continue

        ranked = retrieval.rrf_ranked_article_keys(query, pool=pool)
        top = ranked[:top_k]
        top_hits = set(top) & gold
        pool_hits = set(ranked) & gold
        ideal_hits = min(len(gold), top_k)
        ideal_dcg = _dcg(list(gold)[:ideal_hits], gold) if ideal_hits else 0.0
        ndcg = (_dcg(top, gold) / ideal_dcg) if ideal_dcg else 0.0

        totals["recall_at_k"] += 1.0 if top_hits else 0.0
        totals["mrr_at_k"] += _mrr(top, gold)
        totals["precision_at_k"] += len(top_hits) / top_k if top_k else 0.0
        totals["ndcg_at_k"] += ndcg
        totals["recall_at_pool"] += 1.0 if pool_hits else 0.0
        evaluated += 1

        per_case.append(
            {
                "query": query,
                "gold": sorted(gold),
                "top": top,
                "recall_at_k": bool(top_hits),
                "recall_at_pool": bool(pool_hits),
                "mrr_at_k": _mrr(top, gold),
                "precision_at_k": len(top_hits) / top_k if top_k else 0.0,
                "ndcg_at_k": ndcg,
            }
        )

    if not evaluated:
        return {
            "n": 0,
            "k": top_k,
            "pool": pool,
            "metrics": {},
            "config": {},
            "per_case": per_case,
            "error": "Нет кейсов с gold-разметкой.",
        }

    return {
        "n": evaluated,
        "k": top_k,
        "pool": pool,
        "metrics": {name: value / evaluated for name, value in totals.items()},
        "config": {
            "vector_top_k": config.VECTOR_TOP_K,
            "fts_top_k": config.FTS_TOP_K,
            "rrf_k": config.RRF_K,
            "vector_weight": config.VECTOR_WEIGHT,
            "fts_weight": config.FTS_WEIGHT,
        },
        "per_case": per_case,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--golden",
        type=Path,
        default=config.PROJECT_ROOT / "data" / "golden_eval.json",
    )
    ap.add_argument("--with-rerank", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    cases = load_golden(args.golden)
    if not cases:
        print("Нет кейсов в golden-файле.")
        return 1

    retrieval.ensure_fts_ready()
    pool = config.RERANK_POOL_SIZE
    n = len(cases)

    metrics = {
        "recall_vector": 0,
        "recall_fts": 0,
        "recall_pool": 0,
        "hit3_rrf": 0,
        "hit3_pipeline": 0,
        "mrr_rrf": 0.0,
        "mrr_pipeline": 0.0,
    }
    rerank_status_counts: dict[str, int] = {}
    per_query_rows = []

    for case in cases:
        query = case["query"]
        gold = {_norm_key(g["code"], g["number"]) for g in case.get("gold", [])}
        if not gold:
            continue

        results = database.search(query, config.VECTOR_TOP_K) or {}
        v_metas = (results.get("metadatas") or [[]])[0]
        v_keys = [(str(m["code"]), str(m["number"])) for m in v_metas]
        v_hit = bool(set(v_keys) & gold)
        if v_hit:
            metrics["recall_vector"] += 1

        fts_rows = fts_index.fts_search(query, config.FTS_TOP_K)
        f_keys = [(str(r["code"]), str(r["number"])) for r in fts_rows]
        f_hit = bool(set(f_keys) & gold)
        if f_hit:
            metrics["recall_fts"] += 1

        rrf_keys = retrieval.rrf_ranked_article_keys(query, pool=pool)
        p_hit = bool(set(rrf_keys) & gold)
        if p_hit:
            metrics["recall_pool"] += 1
        if set(rrf_keys[:3]) & gold:
            metrics["hit3_rrf"] += 1
        mrr_r = _mrr(rrf_keys, gold)
        metrics["mrr_rrf"] += mrr_r

        row = {
            "query": query,
            "gold": sorted(gold),
            "vector_hit": v_hit,
            "fts_hit": f_hit,
            "pool_hit": p_hit,
            "rrf_top5": rrf_keys[:5],
            "mrr_rrf": mrr_r,
        }

        if args.with_rerank:
            _, meta, dbg = retrieval.retrieve_context(query, return_debug=True)
            final_keys = [(m["code"], str(m["number"])) for m in meta]
            if set(final_keys) & gold:
                metrics["hit3_pipeline"] += 1
            metrics["mrr_pipeline"] += _mrr(final_keys, gold)
            rerank_status_counts[dbg.rerank_status] = (
                rerank_status_counts.get(dbg.rerank_status, 0) + 1
            )
            row["rerank_status"] = dbg.rerank_status
            row["final"] = final_keys

        per_query_rows.append(row)

    if args.verbose:
        print("\nПо запросам:")
        for r in per_query_rows:
            print(f"--- {r['query']!r}")
            print(f"  gold:     {r['gold']}")
            print(f"  vector:   hit={r['vector_hit']}, fts: hit={r['fts_hit']}")
            print(f"  rrf top5: {r['rrf_top5']}")
            print(f"  mrr_rrf:  {r['mrr_rrf']:.4f}")
            if "rerank_status" in r:
                print(f"  rerank:   {r['rerank_status']} → {r['final']}")

    print(f"\nКейсов: {n}")
    print(
        f"recall@vector (top-{config.VECTOR_TOP_K}): "
        f"{metrics['recall_vector']}/{n} = {metrics['recall_vector']/n:.2%}"
    )
    print(
        f"recall@fts    (top-{config.FTS_TOP_K}):  "
        f"{metrics['recall_fts']}/{n} = {metrics['recall_fts']/n:.2%}"
    )
    print(
        f"recall@pool   (top-{pool}):  "
        f"{metrics['recall_pool']}/{n} = {metrics['recall_pool']/n:.2%}"
    )
    print(f"hit@3_rrf:    {metrics['hit3_rrf']}/{n} = {metrics['hit3_rrf']/n:.2%}")
    print(f"MRR_rrf:      {metrics['mrr_rrf']/n:.4f}")
    if args.with_rerank:
        print(
            f"hit@3_pipeline: "
            f"{metrics['hit3_pipeline']}/{n} = {metrics['hit3_pipeline']/n:.2%}"
        )
        print(f"MRR_pipeline:   {metrics['mrr_pipeline']/n:.4f}")
        print(f"rerank статусы: {rerank_status_counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
