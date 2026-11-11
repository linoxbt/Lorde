#!/usr/bin/env python
"""Step 6: Lorde Bench judge evaluation on the held-out test set.

    python forge/06_judge_eval.py \
        --judge base=openai/student@http://localhost:8001/v1 \
        --judge lorde=openai/lorde@http://localhost:8002/v1 \
        --gpu-hourly base=0.50 --gpu-hourly lorde=0.50 --structured

    # optional closed-model reference for the ROI comparison (any OpenAI-compatible API)
    ... --judge closed=openai/<model>@https://api.openai.com/v1 --judge-key closed=OPENAI_API_KEY \
        --api-price closed=<USD per 1M input>,<USD per 1M output>

For each judge: gate-usable rate, decision agreement and Cohen's kappa vs the
teacher, Spearman and MAE of scores, grounding verdict accuracy, per-language
and per-variant breakdowns, agreement with human labels (the teacher's own
human agreement is the ceiling), agreement with Aya Expanse 32B on the
calibration slice, latency and throughput at fixed concurrency, cost per 1M
judgments vs the API teacher, and AuditKIT paired-bootstrap significance tests.
``--structured`` also runs every judge with vLLM structured outputs ("<name>+so").

Writes results/judge_eval.{json,md} and results/preds/<judge>.jsonl (+ <judge>.meta.json).

Prediction caches are keyed by model identity and test set: results/preds/<judge>.meta.json records the
fingerprint of the weights the endpoint serves (hash of the local directory vLLM reports as the model root,
the Hub sha of a model id, or ``--model-sha``), the structured flag, the template fingerprints and a hash of
the test prompts. A cache that does not match is moved aside (<judge>.jsonl.stale) and recomputed. Caches
from before fingerprints existed have no meta file; they are reused only with ``--trust-legacy-cache``:

    python forge/06_judge_eval.py --judge base=... --judge lorde=... --structured --report-only --trust-legacy-cache
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lorde_forge import audit, config, contract, fingerprint, human, jsonl, llm, metrics  # noqa: E402
from lorde_forge.judge import as_metric_row, cache_meta, error_counts, predict  # noqa: E402
from lorde_forge.report import fmt, table  # noqa: E402


def kv(pairs: list[str]) -> dict[str, float]:
    out = {}
    for p in pairs or []:
        k, _, v = p.partition("=")
        out[k] = float(v)
    return out


def perf(ep: llm.Endpoint, items: list[dict], structured: bool, n: int, concurrency: int) -> dict:
    """Timed run at fixed concurrency (not cached), for latency and throughput."""
    sample = items[:n]
    reqs = [llm.Request(uid=r["uid"], messages=[{"role": "user", "content": r["prompt"]}],
                        params=llm.judge_params(r["kind"], structured)) for r in sample]
    res, wall = llm.run(ep, reqs, concurrency=concurrency, desc=f"perf {ep.name}")
    ok = [r for r in res if r.error is None]
    s = metrics.latency_stats([r.latency_s for r in ok], wall, n_ok=len(ok),
                              completion_tokens=sum(r.completion_tokens for r in ok))
    s.update({"n": len(sample), "concurrency": concurrency, "errors": len(res) - len(ok),
              "mean_prompt_tokens": round(sum(r.prompt_tokens for r in ok) / max(1, len(ok)), 1),
              "mean_completion_tokens": round(sum(r.completion_tokens for r in ok) / max(1, len(ok)), 1)})
    return s


def judge_models(eps: list[llm.Endpoint], args) -> dict[str, dict | None]:
    """Model identity per endpoint, for the cache keys. Report-only runs ask no server: only the flags count."""
    shas, paths = dict(p.split("=", 1) for p in args.model_sha), dict(p.split("=", 1) for p in args.model_path)
    out: dict[str, dict | None] = {}
    for e in eps:
        if args.report_only:
            if e.name in shas:
                out[e.name] = {"sha": shas[e.name], "source": "--model-sha"}
            elif e.name in paths:
                out[e.name] = dict(zip(("sha", "source"), fingerprint.model_sha(paths[e.name], config.ROOT)))
            else:
                out[e.name] = None
            continue
        out[e.name] = fingerprint.endpoint_model(e.base_url, e.model, e.api_key, shas.get(e.name),
                                                 paths.get(e.name), config.ROOT)
        if not out[e.name].get("sha"):
            print(f"[eval] WARNING: no weights fingerprint for {e.name} ({out[e.name].get('source')}); its cache "
                  "is keyed by test set and templates only. Pass --model-sha or --model-path.", flush=True)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--judge", action="append", required=True, help="name=openai/model@base_url")
    ap.add_argument("--gpu-hourly", action="append", default=[], help="name=USD per GPU hour")
    ap.add_argument("--judge-key", action="append", default=[],
                    help="name=ENV_VAR holding that judge's API key (default VLLM_API_KEY)")
    ap.add_argument("--api-price", action="append", default=[],
                    help="name=IN,OUT USD per 1M tokens, for API judges (e.g. a closed model)")
    ap.add_argument("--structured", action="store_true", help="also evaluate with structured outputs")
    ap.add_argument("--report-only", action="store_true",
                    help="rebuild the report from cached predictions and timings (no model calls), e.g. after "
                         "adding human labels")
    ap.add_argument("--model-sha", action="append", default=[],
                    help="name=SHA: the judge's model identity, overriding the served-weights fingerprint")
    ap.add_argument("--model-path", action="append", default=[],
                    help="name=PATH_OR_HUB_ID of the weights the judge serves (default: the root vLLM reports)")
    ap.add_argument("--trust-legacy-cache", action="store_true",
                    help="reuse prediction caches written before fingerprints existed (no <judge>.meta.json)")
    ap.add_argument("--candidate", default="lorde")
    ap.add_argument("--baseline", default="base")
    ap.add_argument("--test", default=str(config.DATA / "test.jsonl"))
    ap.add_argument("--human-labels", default=str(config.DATA / "human_labels.jsonl"))
    ap.add_argument("--human-key", default=str(config.DATA / "human_sample_key.jsonl"),
                    help="private key mapping the blind sample's opaque ids to test uids")
    ap.add_argument("--calibration", default=str(config.DATA / "labels_32b.jsonl"))
    ap.add_argument("--concurrency", type=int, default=16)
    ap.add_argument("--perf-n", type=int, default=256)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default=str(config.RESULTS / "judge_eval"))
    args = ap.parse_args()

    test = jsonl.read(args.test)
    if args.limit:
        test = test[: args.limit]
    if not test:
        sys.exit("empty test set: run forge/03_make_splits.py first")
    uids = [t["uid"] for t in test]
    teacher = [{"ok": True, "score": t["teacher_score"], "verdict": t.get("teacher_verdict")} for t in test]
    langs = [t["lang"] for t in test]
    kinds = [t["kind"] for t in test]
    variant_keys = [f"{t['kind']}:{t.get('variant', '?')}" for t in test]
    prices = kv(args.gpu_hourly)

    keys = dict(p.split("=", 1) for p in args.judge_key)
    api_prices = {k: tuple(float(x) for x in v.split(",")) for k, v in (p.split("=", 1) for p in args.api_price)}
    eps = [llm.Endpoint.parse(s) for s in args.judge]
    for e in eps:
        e.api_key_env = keys.get(e.name, e.api_key_env)
    runs: list[tuple[str, llm.Endpoint, bool]] = [(e.name, e, False) for e in eps]
    if args.structured:
        runs += [(f"{e.name}+so", e, True) for e in eps]

    preds: dict[str, dict[str, dict]] = {}
    perf_stats: dict[str, dict] = {}
    cache_info: dict[str, dict] = {}
    previous = Path(args.out + ".json")
    old_judges = json.loads(previous.read_text())["judges"] if args.report_only and previous.exists() else {}
    models = judge_models(eps, args)
    for name, ep, structured in runs:
        cache = config.RESULTS / "preds" / f"{name}.jsonl"
        meta = cache_meta(models[ep.name], test, structured)
        if args.report_only:
            usable, status = fingerprint.check(cache, meta, args.trust_legacy_cache)
            if not usable:
                sys.exit(f"--report-only: cached predictions for {name} are not usable: {status} ({cache})")
            preds[name] = {r["uid"]: r for r in jsonl.read(cache)}  # the latest row per uid wins
            perf_stats[name] = old_judges.get(name, {}).get("perf", {})
            cache_info[name] = {"status": status, "model": (fingerprint.read_meta(cache) or {}).get("model")}
            continue
        print(f"[eval] judge {name} ({ep.model} @ {ep.base_url}, structured={structured})")
        preds[name] = predict(ep, test, structured, cache, args.concurrency, meta=meta,
                              trust_legacy=args.trust_legacy_cache)
        legacy = (fingerprint.read_meta(cache) or {}).get("legacy_trusted")
        cache_info[name] = {"status": "legacy, trusted by --trust-legacy-cache" if legacy else "fingerprinted",
                            "model": models[ep.name]}
        perf_stats[name] = perf(ep, test, structured, args.perf_n, args.concurrency)

    rows = {name: [as_metric_row(preds[name].get(u)) for u in uids] for name in preds}
    report: dict = {"n_test": len(test), "templates": contract.fingerprints(), "judges": {}}
    for name in rows:
        gpu = prices.get(name.removesuffix("+so"))
        api_price = api_prices.get(name.removesuffix("+so"))
        p = perf_stats[name]
        if api_price and p.get("mean_prompt_tokens") is not None:
            cost = metrics.cost_per_million_api(p["mean_prompt_tokens"], p["mean_completion_tokens"], *api_price)
        else:
            cost = metrics.cost_per_million_gpu(gpu, p.get("judgments_per_s")) if gpu else None
        report["judges"][name] = {
            "vs_teacher": metrics.compare(rows[name], teacher),
            "by_kind": metrics.grouped(rows[name], teacher, kinds),
            "by_lang": metrics.grouped(rows[name], teacher, langs),
            "by_variant": metrics.grouped(rows[name], teacher, variant_keys),
            "perf": p,
            "gpu_hourly": gpu,
            "api_price_per_1M_tokens": api_price,
            "cost_per_1M": cost,
            # unparseable replies count as rejects; transport failures are reported apart from them
            "errors": error_counts(preds[name], uids),
            "cache": cache_info.get(name),
        }

    # Human agreement: teacher is the ceiling.
    # Labels are keyed by opaque id (mapped back through the key) or, for older labels, by the test uid;
    # several labellers per item are averaged (score) and majority-voted (verdict).
    hl = human.load_labels(args.human_labels, args.human_key)
    hu = [i for i, u in enumerate(uids) if u in hl]
    if hu:
        href = [{"ok": True, "score": hl[uids[i]]["human_score"], "verdict": hl[uids[i]].get("human_verdict")}
                for i in hu]
        report["human"] = {"n": len(hu), "n_labelers": len({r.get("labeler") for r in jsonl.read(args.human_labels)}),
                           "teacher": metrics.compare([teacher[i] for i in hu], href)}
        for name in rows:
            report["human"][name] = metrics.compare([rows[name][i] for i in hu], href)

    # Calibration: agreement with Aya Expanse 32B (Cohere API) on the overlap.
    cal = {r["uid"]: r for r in jsonl.read(args.calibration) if not r.get("call_error")}
    ci = [i for i, u in enumerate(uids) if u in cal]
    if ci:
        cref = [{"ok": bool(cal[uids[i]]["ok"]), "score": cal[uids[i]]["score"], "verdict": cal[uids[i]].get("verdict")}
                for i in ci]
        report["calibration_32b"] = {"n": len(ci), "teacher_8b": metrics.compare([teacher[i] for i in ci], cref)}
        for name in rows:
            report["calibration_32b"][name] = metrics.compare([rows[name][i] for i in ci], cref)

    # API reference cost: Aya Expanse 32B at Cohere list price, with measured token counts.
    tok_src = list(cal.values()) or jsonl.read(config.DATA / "labels.jsonl")
    tok_src = [r for r in tok_src if r.get("prompt_tokens")]
    if tok_src:
        pt = sum(r["prompt_tokens"] for r in tok_src) / len(tok_src)
        ct = sum(r["completion_tokens"] for r in tok_src) / len(tok_src)
        price = config.COHERE_32B_PRICE_PER_M
        api = metrics.cost_per_million_api(pt, ct, price["input"], price["output"])
        report["api_reference"] = {"model": f"{config.CALIBRATION_MODEL} (Cohere API)",
                                   "mean_prompt_tokens": round(pt, 1), "mean_completion_tokens": round(ct, 1),
                                   "price_per_1M_tokens": price, "cost_per_1M_judgments": api,
                                   "token_source": "calibration labels" if cal else "teacher labels (8B tokenizer)"}

    # AuditKIT significance: does the candidate agree with the teacher more often than the baseline?
    ref_dec = [t["teacher_score"] >= config.THRESHOLD for t in test]

    def dec(name: str) -> list[bool]:
        return [bool(r["ok"] and r["score"] is not None and r["score"] >= config.THRESHOLD) for r in rows[name]]

    sig = {}
    for cand in (args.candidate, f"{args.candidate}+so"):
        for base in (args.baseline, f"{args.baseline}+so"):
            if cand in rows and base in rows and base.endswith("+so") == cand.endswith("+so"):
                sig[f"{cand} vs {base}"] = audit.decision_significance(uids, ref_dec, dec(base), dec(cand))
    report["significance_decision_agreement"] = sig

    report["acceptance"] = acceptance(report, args.candidate)
    jsonl.write_json(f"{args.out}.json", report)
    Path(f"{args.out}.md").write_text(render_md(report, args.candidate), encoding="utf-8")
    print(f"[eval] wrote {args.out}.md / .json")
    for c in report["acceptance"]:
        print(f"  {'PASS' if c['met'] else 'FAIL' if c['met'] is False else ' -- '}  {c['criterion']}: {c['value']}")


def acceptance(r: dict, cand: str) -> list[dict]:
    j = r["judges"].get(cand)
    if not j:
        return []
    vt = j["vs_teacher"]
    so = r["judges"].get(f"{cand}+so", {}).get("vs_teacher", {})
    out = []

    def add(criterion, value, ok, blocking=True, fmt_pct=True):
        out.append({"criterion": criterion, "value": fmt(value, pct=fmt_pct), "met": ok, "blocking": blocking})

    add("Gate-usable rate, unconstrained ≥ 99.0%", vt["gate_usable_rate"], vt["gate_usable_rate"] >= 0.99)
    if so:
        add("Gate-usable rate, structured outputs = 100%", so["gate_usable_rate"], so["gate_usable_rate"] >= 1.0)
    add("Decision agreement vs teacher ≥ 90%", vt["decision_agreement"], vt["decision_agreement"] >= 0.90)
    add("Cohen's κ vs teacher ≥ 0.75", vt["kappa"], vt["kappa"] is not None and vt["kappa"] >= 0.75, fmt_pct=False)
    add("Spearman ρ ≥ 0.80 (reported)", vt["spearman"], vt["spearman"] is not None and vt["spearman"] >= 0.80,
        blocking=False, fmt_pct=False)
    add("MAE ≤ 0.10 (reported)", vt["mae"], vt["mae"] is not None and vt["mae"] <= 0.10, blocking=False, fmt_pct=False)
    g = j["by_kind"].get("grounding", {})
    if g.get("verdict_accuracy") is not None:
        add("Grounding verdict accuracy ≥ 85%", g["verdict_accuracy"], g["verdict_accuracy"] >= 0.85)
    h = r.get("human")
    if h and cand in h:
        ceiling = h["teacher"]["decision_agreement"]
        add(f"Human decision agreement ≥ teacher's ({fmt(ceiling, True)}) − 3 pts",
            h[cand]["decision_agreement"], h[cand]["decision_agreement"] >= ceiling - 0.03)
    worst = min(((k, v["decision_agreement"]) for k, v in j["by_lang"].items()), key=lambda x: x[1])
    add(f"Per-language decision agreement ≥ 85% (worst: {worst[0]})", worst[1], worst[1] >= 0.85)
    p95 = j["perf"].get("p95_s")
    out.append({"criterion": "p95 latency ≤ 1.5 s at 16 concurrent (production on L4; reported on this GPU)",
                "value": f"{p95} s" if p95 is not None else "–", "met": p95 is not None and p95 <= 1.5,
                "blocking": False})
    return out


def render_md(r: dict, cand: str) -> str:
    js = r["judges"]
    lines = [f"# Lorde Bench: judge evaluation\n\nHeld-out test set: **{r['n_test']}** items "
             f"(grouped by prompt / source passage, never seen in training). Reference: teacher "
             f"(Aya Expanse 8B) decisions at threshold {config.THRESHOLD}. Unparseable replies count as rejects, "
             f"as in CuratorKIT.\n",
             f"Templates: {', '.join(t['id'] for t in r['templates']['templates'].values())}\n"]
    head = ["Judge", "Gate-usable", "Decision agr.", "κ", "Spearman", "MAE", "Verdict acc.", "Pass rate",
            "p50 s", "p95 s", "judg/s", "$ / 1M"]
    body = []
    for name, j in js.items():
        v, p = j["vs_teacher"], j["perf"]
        body.append([f"**{name}**" if name.startswith(cand) else name, fmt(v["gate_usable_rate"], True),
                     fmt(v["decision_agreement"], True), fmt(v["kappa"]), fmt(v["spearman"]), fmt(v["mae"]),
                     fmt(j["by_kind"].get("grounding", {}).get("verdict_accuracy"), True), fmt(v["pass_rate"], True),
                     fmt(p.get("p50_s")), fmt(p.get("p95_s")), fmt(p.get("judgments_per_s")),
                     fmt(j.get("cost_per_1M"))])
    lines += ["## Before / after\n", table(head, body), ""]
    if "api_reference" in r:
        a = r["api_reference"]
        lines += [f"API reference: {a['model']} at ${a['price_per_1M_tokens']['input']}/1M input and "
                  f"${a['price_per_1M_tokens']['output']}/1M output tokens, {a['mean_prompt_tokens']} prompt + "
                  f"{a['mean_completion_tokens']} completion tokens per judgment ({a['token_source']}) → "
                  f"**${a['cost_per_1M_judgments']} per 1M judgments**.\n"]
        c = js.get(cand, {}).get("cost_per_1M")
        if c:
            lines += [f"Lorde on this GPU: **${c} per 1M judgments**, {a['cost_per_1M_judgments'] / c:.0f}× cheaper.\n"]

    lines += ["## Acceptance criteria (spec 9.2)\n",
              table(["Criterion", "Value", "Met", "Blocking"],
                    [[c["criterion"], c["value"], fmt(c["met"]), fmt(c["blocking"])] for c in r["acceptance"]]), ""]

    langs = sorted(next(iter(js.values()))["by_lang"])
    lines += ["## Decision agreement by language\n",
              table(["Judge", *langs], [[n, *(fmt(j["by_lang"][lg]["decision_agreement"], True) for lg in langs)]
                                        for n, j in js.items()]), ""]
    var = sorted(next(iter(js.values()))["by_variant"])
    lines += ["## Pass rate by variant (teacher vs judges)\n",
              table(["Variant", "n", "teacher", *js], [[v, js[next(iter(js))]["by_variant"][v]["n"],
                    fmt(js[next(iter(js))]["by_variant"][v]["ref_pass_rate"], True),
                    *(fmt(j["by_variant"][v]["pass_rate"], True) for j in js.values())] for v in var]), ""]
    if "human" in r:
        h = r["human"]
        lines += [f"## Agreement with human labels (n = {h['n']})\n",
                  table(["Judge", "Decision agr.", "κ", "Spearman", "MAE"],
                        [[n, fmt(m["decision_agreement"], True), fmt(m["kappa"]), fmt(m["spearman"]), fmt(m["mae"])]
                         for n, m in h.items() if isinstance(m, dict)]), ""]
    if "calibration_32b" in r:
        c = r["calibration_32b"]
        lines += [f"## Agreement with Aya Expanse 32B (Cohere API, n = {c['n']})\n",
                  table(["Judge", "Decision agr.", "κ", "MAE"],
                        [[n, fmt(m["decision_agreement"], True), fmt(m["kappa"]), fmt(m["mae"])]
                         for n, m in c.items() if isinstance(m, dict)]), ""]
    if r.get("significance_decision_agreement"):
        lines += ["## Significance (AuditKIT paired bootstrap, decision agreement with teacher)\n",
                  table(["Comparison", "n", "baseline", "candidate", "Δ", "p", "significant"],
                        [[k, s.get("n"), fmt(s.get("mean_baseline"), True), fmt(s.get("mean_candidate"), True),
                          fmt(s.get("delta"), True), s.get("p_value"), fmt(s.get("significant"))]
                         for k, s in r["significance_decision_agreement"].items()]), ""]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
