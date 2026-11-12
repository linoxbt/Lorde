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

Latency and throughput are timed before any prediction, on a seeded random sample stratified by kind x
language (proportional to the test set), so no timed prompt is in the server's cache from this run; the
structured-outputs run of the same server gets a disjoint sample. The GPU is recorded (nvidia-smi, or
``--hardware``) and reported next to every timing.
``--structured`` also runs every judge with vLLM structured outputs ("<name>+so").

Writes results/judge_eval.{json,md} and results/preds/<judge>.jsonl (+ <judge>.meta.json).

Prediction caches are keyed by model identity and test set: results/preds/<judge>.meta.json records the
fingerprint of the weights the endpoint serves (hash of the local directory vLLM reports as the model root,
the Hub sha of a model id, or ``--model-sha``), the structured flag, the template fingerprints and a hash of
the test prompts. A cache that does not match is moved aside (<judge>.jsonl.stale) and recomputed. Caches
from before fingerprints existed have no meta file; they are reused only with ``--trust-legacy-cache``:

    python forge/06_judge_eval.py --judge base=... --judge lorde=... --structured --report-only --trust-legacy-cache

A trusted legacy cache is stamped with the weights the current run serves ("legacy_trusted": true in its
meta.json), which does not prove the earlier session used those weights, so the report says so ("Prediction
caches" in results/judge_eval.md, "cache" per judge in the JSON). To recompute such predictions, serve the
judges and run this step without ``--trust-legacy-cache`` (and without ``--report-only``): the legacy cache is
moved aside to results/preds/<judge>.jsonl.stale and every test item is asked again. On Kaggle, run the driver's
Session D step ``judge-timing`` with LORDE_TRUST_LEGACY_CACHE unset (the default).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lorde_forge import audit, canonical, config, contract, fingerprint, human, jsonl, llm, metrics, splits  # noqa: E402
from lorde_forge.judge import as_metric_row, cache_meta, error_counts, predict  # noqa: E402
from lorde_forge.report import fmt, table  # noqa: E402


def kv(pairs: list[str]) -> dict[str, float]:
    out = {}
    for p in pairs or []:
        k, _, v = p.partition("=")
        out[k] = float(v)
    return out


def detect_hardware() -> dict | None:
    """The GPU(s) of this machine, as nvidia-smi (or torch) names them; None when there is none."""
    import shutil
    import subprocess

    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], capture_output=True,
                                 text=True, timeout=20).stdout
            names = [x.strip() for x in out.splitlines() if x.strip()]
            if names:
                return {"name": names[0], "gpus_on_host": len(names), "source": "nvidia-smi"}
        except Exception:  # noqa: BLE001
            pass
    try:
        import torch

        if torch.cuda.is_available():
            return {"name": torch.cuda.get_device_name(0), "gpus_on_host": torch.cuda.device_count(),
                    "source": "torch"}
    except Exception:  # noqa: BLE001
        pass
    return None


def perf(ep: llm.Endpoint, sample: list[dict], structured: bool, concurrency: int, note: dict) -> dict:
    """Timed run at fixed concurrency (not cached), for latency and throughput."""
    reqs = [llm.Request(uid=r["uid"], messages=[{"role": "user", "content": r["prompt"]}],
                        params=llm.judge_params(r["kind"], structured)) for r in sample]
    res, wall = llm.run(ep, reqs, concurrency=concurrency, desc=f"perf {ep.name}")
    ok = [r for r in res if r.error is None]
    s = metrics.latency_stats([r.latency_s for r in ok], wall, n_ok=len(ok),
                              completion_tokens=sum(r.completion_tokens for r in ok))
    s.update({"n": len(sample), "concurrency": concurrency, "errors": len(res) - len(ok), "sample": note,
              "mean_prompt_tokens": round(sum(r.prompt_tokens for r in ok) / max(1, len(ok)), 1),
              "mean_completion_tokens": round(sum(r.completion_tokens for r in ok) / max(1, len(ok)), 1)})
    return s


def excluded_from_test() -> dict:
    """Items left out of the test set because the teacher's reply was unparseable (from step 3's summary)."""
    s = json.loads((config.DATA / "splits_summary.json").read_text()) if (
        config.DATA / "splits_summary.json").exists() else {}
    dropped, in_test = s.get("dropped") or {}, s.get("dropped_from_test")
    return {"teacher_unparseable_in_test_split": None if in_test is None else in_test.get("teacher_unparseable", 0),
            "teacher_unparseable_all_splits": dropped.get("teacher_unparseable", 0) if s else None,
            "too_long_all_splits": dropped.get("too_long", 0) if s else None,
            "note": "the test set only holds items whose teacher reply CuratorKIT could parse"}


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
    ap.add_argument("--min-lang-n", type=int, default=MIN_LANG_N,
                    help="test items a language needs before the per-language criterion can judge it")
    ap.add_argument("--perf-n", type=int, default=256, help="items timed per judge (stratified random sample)")
    ap.add_argument("--perf-seed", type=int, default=0)
    ap.add_argument("--hardware", default=None,
                    help="GPU the timings were measured on (default: nvidia-smi; in --report-only: the previous report)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default=str(config.RESULTS / "judge_eval"))
    args = ap.parse_args()

    test = jsonl.read(args.test)
    if args.limit:
        test = test[: args.limit]
    if not test:
        sys.exit("empty test set: run forge/03_make_splits.py first")
    uids = [t["uid"] for t in test]
    # Reference verdict = the canonical training target's verdict (test.jsonl from before the fix held the
    # raw teacher verdict; it is canonicalised here, so old test sets give the same reference).
    teacher = [{"ok": True, "score": t["teacher_score"], "verdict": canonical.reference_verdict(t)} for t in test]
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
    old = json.loads(previous.read_text()) if args.report_only and previous.exists() else {}
    old_judges = old.get("judges", {})
    if args.hardware:
        hardware = {"name": args.hardware, "source": "--hardware"}
    else:
        hardware = old.get("hardware") if args.report_only else detect_hardware()
    models = judge_models(eps, args)
    if not args.report_only:
        # Timings first, on a fresh server: no timed prompt was sent before in this run. Plain and structured
        # runs of one server get disjoint samples; every judge is timed on the same items.
        draws = splits.stratified_draws(test, args.perf_n, k=2 if args.structured else 1, seed=args.perf_seed)
        for name, ep, structured in runs:
            sample = draws[1 if structured else 0]
            note = {"method": "seeded random sample, stratified by kind x lang", "seed": args.perf_seed,
                    "by_stratum": dict(Counter(f"{t['kind']}:{t['lang']}" for t in sample)),
                    "timed_before_predictions": True, "disjoint_from_other_timed_runs": True,
                    "hardware": (hardware or {}).get("name")}
            print(f"[eval] timing {name} on {len(sample)} items")
            perf_stats[name] = perf(ep, sample, structured, args.concurrency, note)
    for name, ep, structured in runs:
        cache = config.RESULTS / "preds" / f"{name}.jsonl"
        meta = cache_meta(models[ep.name], test, structured)
        if args.report_only:
            usable, status = fingerprint.check(cache, meta, args.trust_legacy_cache)
            if not usable:
                sys.exit(f"--report-only: cached predictions for {name} are not usable: {status} ({cache})")
            preds[name] = {r["uid"]: r for r in jsonl.read(cache)}  # the latest row per uid wins
            perf_stats[name] = old_judges.get(name, {}).get("perf", {})
            cache_info[name] = cache_status(cache, status)
            continue
        print(f"[eval] judge {name} ({ep.model} @ {ep.base_url}, structured={structured})")
        preds[name] = predict(ep, test, structured, cache, args.concurrency, meta=meta,
                              trust_legacy=args.trust_legacy_cache)
        cache_info[name] = cache_status(cache, None, models[ep.name])

    rows = {name: [as_metric_row(preds[name].get(u)) for u in uids] for name in preds}
    report: dict = {"n_test": len(test), "templates": contract.fingerprints(), "hardware": hardware,
                    "test_excluded": excluded_from_test(), "judges": {}}
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

    report["acceptance"] = acceptance(report, args.candidate, args.min_lang_n)
    report["promotable"] = promotable(report["acceptance"])
    jsonl.write_json(f"{args.out}.json", report)
    Path(f"{args.out}.md").write_text(render_md(report, args.candidate), encoding="utf-8")
    print(f"[eval] wrote {args.out}.md / .json")
    for c in report["acceptance"]:
        print(f"  {'PASS' if c['met'] else 'FAIL' if c['met'] is False else ' -- '}  {c['criterion']}: {c['value']}"
              + (f" ({c['reason']})" if c.get("reason") else ""))
    print(f"  promotable: {report['promotable']}")


MIN_LANG_N = 20  # a language with fewer test items cannot pass or fail the per-language criterion


def acceptance(r: dict, cand: str, min_lang_n: int = MIN_LANG_N) -> list[dict]:
    """Every spec 9.2 criterion that step 6 can measure, always listed: ``met`` is True/False, or None with a
    ``reason`` when its inputs are missing. Promotable = every blocking criterion met (none None)."""
    j = r["judges"].get(cand) or {}
    vt = j.get("vs_teacher") or {}
    so = (r["judges"].get(f"{cand}+so") or {}).get("vs_teacher") or {}
    out: list[dict] = []
    missing_cand = None if j else f"no judge named {cand!r} was evaluated"

    def add(criterion, value, ok, blocking=True, fmt_pct=True, reason=None):
        reason = missing_cand or reason
        if reason or value is None:
            ok = None
            reason = reason or "not measured"
        out.append({"criterion": criterion, "value": fmt(value, pct=fmt_pct), "met": ok, "blocking": blocking,
                    "reason": reason})

    def ge(v, t):
        return None if v is None else v >= t

    add("Gate-usable rate, unconstrained ≥ 99.0%", vt.get("gate_usable_rate"), ge(vt.get("gate_usable_rate"), 0.99))
    add("Gate-usable rate, structured outputs = 100%", so.get("gate_usable_rate"),
        ge(so.get("gate_usable_rate"), 1.0), reason=None if so else "structured outputs not evaluated (--structured)")
    add("Decision agreement vs teacher ≥ 90%", vt.get("decision_agreement"), ge(vt.get("decision_agreement"), 0.90))
    add("Cohen's κ vs teacher ≥ 0.75", vt.get("kappa"), ge(vt.get("kappa"), 0.75), fmt_pct=False,
        reason=None if vt.get("kappa") is not None else "κ undefined")
    add("Spearman ρ ≥ 0.80 (reported)", vt.get("spearman"), ge(vt.get("spearman"), 0.80), blocking=False,
        fmt_pct=False)
    mae = vt.get("mae")
    add("MAE ≤ 0.10 (reported)", mae, None if mae is None else mae <= 0.10, blocking=False, fmt_pct=False)
    g = (j.get("by_kind") or {}).get("grounding", {})
    add("Grounding verdict accuracy ≥ 85%", g.get("verdict_accuracy"), ge(g.get("verdict_accuracy"), 0.85),
        reason=None if g.get("verdict_accuracy") is not None else "no grounding items with a teacher verdict")
    h = r.get("human") or {}
    if h.get(cand) and h.get("teacher"):
        ceiling = h["teacher"]["decision_agreement"]
        add(f"Human decision agreement ≥ teacher's ({fmt(ceiling, True)}) − 3 pts (n = {h['n']})",
            h[cand]["decision_agreement"], h[cand]["decision_agreement"] >= ceiling - 0.03)
    else:
        add("Human decision agreement ≥ teacher's − 3 pts", None, None,
            reason="no human labels for test items (forge/04_label_tool.py)")
    by_lang = j.get("by_lang") or {}
    big = {k: v for k, v in by_lang.items() if v.get("n", 0) >= min_lang_n}
    # every training language must be shown to pass; one with too few (or no) test items cannot be
    small = sorted({*config.LANG_CODES, *by_lang} - set(big))
    if big:
        worst = min(big.items(), key=lambda kv: kv[1]["decision_agreement"])
        reason = None
        if worst[1]["decision_agreement"] < 0.85:
            ok = False
        elif small:
            ok, reason = None, f"too few test items (< {min_lang_n}) in {', '.join(small)}"
        else:
            ok = True
        add(f"Per-language decision agreement ≥ 85% (worst: {worst[0]}, n = {worst[1]['n']})",
            worst[1]["decision_agreement"], ok, reason=reason)
    else:
        add("Per-language decision agreement ≥ 85%", None, None,
            reason=f"no language has {min_lang_n} or more test items" if by_lang else None)
    p95 = (j.get("perf") or {}).get("p95_s")
    hw = (r.get("hardware") or {}).get("name") or "hardware not recorded"
    out.append({"criterion": f"p95 latency ≤ 1.5 s at 16 concurrent (spec: 1× L4 in production; measured on {hw})",
                "value": f"{p95} s" if p95 is not None else "–", "met": None if p95 is None else p95 <= 1.5,
                "blocking": False, "reason": None if p95 is not None else "not timed"})
    return out


LEGACY_STATUS = "legacy, trusted by --trust-legacy-cache"


def cache_status(cache: Path, status: str | None, model: dict | None = None) -> dict:
    """Where a judge's predictions came from, for the report: freshly fingerprinted, or a legacy cache (written
    before caches carried a weights fingerprint) that this run trusted and stamped with the weights it served."""
    meta = fingerprint.read_meta(cache) or {}
    legacy = bool(meta.get("legacy_trusted")) or (status or "").startswith("legacy")
    return {"status": LEGACY_STATUS if legacy else "fingerprinted", "legacy_trusted": legacy,
            "model": model if model is not None else meta.get("model"), "stamped": meta.get("written")}


def cache_md(r: dict) -> str:
    """Provenance of the predictions behind every number above (audit 2, B7)."""
    js = r.get("judges", {})
    if not any(j.get("cache") for j in js.values()):
        return ""
    rows, legacy = [], []
    for name, j in js.items():
        c = j.get("cache") or {}
        is_legacy = bool(c.get("legacy_trusted")) or str(c.get("status", "")).startswith("legacy")
        legacy += [name] if is_legacy else []
        sha = str((c.get("model") or {}).get("sha") or "")[:12] or "–"
        rows.append([name, ("legacy cache, trusted (`--trust-legacy-cache`)" if is_legacy else
                            c.get("status") or "not recorded"), f"`{sha}`" if sha != "–" else sha,
                     (c.get("stamped") or "–")[:10]])
    out = ["## Prediction caches\n", table(["Judge", "Predictions", "Weights sha (stamped)", "Stamped"], rows), ""]
    if legacy:
        out += [f"**{', '.join(legacy)}:** these predictions were cached by an earlier session, before caches "
                "carried a weights fingerprint, and reused here with `--trust-legacy-cache`. This run stamped them "
                "with the weights it was serving; nothing proves the earlier session served the same weights. To "
                "recompute them, re-run this step without `--trust-legacy-cache` (the old cache is moved aside to "
                "`results/preds/<judge>.jsonl.stale` and the server is asked again, "
                f"{r.get('n_test', '?')} items per judge).\n"]
    return "\n".join(out)


def promotable(criteria: list[dict]) -> bool:
    blocking = [c for c in criteria if c["blocking"]]
    return bool(blocking) and all(c["met"] is True for c in blocking)


def excluded_md(r: dict) -> str:
    e = r.get("test_excluded") or {}
    if e.get("teacher_unparseable_in_test_split") is not None:
        return (f" Items whose teacher reply was unparseable have no reference and are excluded: "
                f"{e['teacher_unparseable_in_test_split']} from the test split "
                f"({e['teacher_unparseable_all_splits']} over all splits).")
    if e.get("teacher_unparseable_all_splits") is not None:
        return (f" Items whose teacher reply was unparseable have no reference and are excluded "
                f"({e['teacher_unparseable_all_splits']} over all splits).")
    return ""


def timing_md(r: dict) -> str:
    hw = (r.get("hardware") or {}).get("name")
    perf = next((j.get("perf") for j in r["judges"].values() if j.get("perf")), {}) or {}
    smp = perf.get("sample") or {}
    where = f"measured on {hw}" if hw else "hardware not recorded"
    how = (f"{perf.get('n')} items per judge, seeded random sample stratified by kind x language, timed before any "
           "prediction" if smp else "the first test items, after the predictions (older run: cache may be warm)")
    return f"{where}; {how}; {perf.get('concurrency', '?')} concurrent requests."


def render_md(r: dict, cand: str) -> str:
    js = r["judges"]
    lines = [f"# Lorde Bench: judge evaluation\n\nHeld-out test set: **{r['n_test']}** items "
             f"(grouped by prompt / source passage, never seen in training). Reference: teacher "
             f"(Aya Expanse 8B) decisions at threshold {config.THRESHOLD}. Unparseable replies count as rejects, "
             f"as in CuratorKIT.{excluded_md(r)}\n",
             f"Timings: {timing_md(r)}\n",
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
            hw = (r.get("hardware") or {}).get("name") or "the evaluation GPU (not recorded)"
            lines += [f"Lorde on {hw}: **${c} per 1M judgments**, {a['cost_per_1M_judgments'] / c:.0f}× cheaper.\n"]

    prom = r.get("promotable")
    lines += ["## Acceptance criteria (spec 9.2)\n",
              f"**Promotable: {'yes' if prom else 'no'}**"
              + ("" if prom else " (every blocking criterion must be met; unmeasured ones count as not met)") + "\n",
              table(["Criterion", "Value", "Met", "Blocking", "Note"],
                    [[c["criterion"], c["value"], "not measured" if c["met"] is None else fmt(c["met"]),
                      fmt(c["blocking"]), c.get("reason") or ""] for c in r["acceptance"]]), ""]
    errs = {n: j.get("errors") for n, j in js.items() if j.get("errors")}
    if errs:
        lines += ["Replies that failed: call errors (transport) are retried and never cached; parse failures are "
                  "replies CuratorKIT could not read (counted as rejects above).\n",
                  table(["Judge", "Call errors", "Parse failures", "Missing"],
                        [[n, e["call_errors"], e["parse_failures"], e["missing"]] for n, e in errs.items()]), ""]

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
    if cache_md(r):
        lines += [cache_md(r)]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
