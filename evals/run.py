"""Eval flow CLI — run Mai scenarios against the LIVE model and emit metrics.

    python -m evals.run                      # all scenarios, 1 run each
    python -m evals.run profile_pic_to_video # one scenario by name
    python -m evals.run --runs 3 --limit 2 --timeout 120

Every run writes a full transcript (inputs + the entire log + outputs + grade) to
evals/logs/. At the end it prints a metrics table and writes evals/logs/<ts>__summary.json.
Needs the same model env the app uses (e.g. OPENROUTER_API_KEY); exits 2 if none.

Exit code: 0 if every scenario's pass-rate >= --threshold, else 1 (CI-friendly).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

from evals.harness import (DEFAULT_TIMEOUT_S, DEFAULT_TOOL_CALL_LIMIT, LOGS_DIR,
                           RunDetail, RunRecord, RunSummary, ScenarioAggregate,
                           run_conv_scenario, run_execution_scenario, run_scenario, write_log)
from evals.report import write_report
from evals.scenarios import (CONV_SCENARIOS, EXECUTION_SCENARIOS, SCENARIOS, ConvScenario,
                             ExecutionScenario, get_scenario)


def _git_rev() -> str:
    """Short commit the eval ran against, '-dirty' if the tree has uncommitted changes —
    evals usually run on a working tree, so the dirty flag matters for reproducibility."""
    root = Path(__file__).resolve().parent.parent
    try:
        rev = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        if not rev:
            return "unknown"
        dirty = subprocess.run(["git", "-C", str(root), "status", "--porcelain"],
                              capture_output=True, text=True, timeout=5).stdout.strip()
        return f"{rev}-dirty" if dirty else rev
    except Exception:
        return "unknown"


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * pct
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 2)


def _aggregate(name: str, records: list[RunRecord]) -> ScenarioAggregate:
    n = len(records)
    passes = sum(r.passed for r in records)
    durations = [r.duration_s for r in records]
    # Coverage spans BOTH call layers (work + action): fraction of all expected calls hit.
    coverage = [(r.grade.calls.n_passed / r.grade.calls.n_expected)
                if (r.grade and r.grade.calls.n_expected) else 0.0 for r in records]
    in_tok = [r.run_meta.input_tokens or 0 for r in records]
    out_tok = [r.run_meta.output_tokens or 0 for r in records]
    tool_calls = [r.n_tool_calls for r in records]

    def _stdev(xs: list[float]) -> float:
        if len(xs) < 2:
            return 0.0
        mu = sum(xs) / len(xs)
        return round((sum((x - mu) ** 2 for x in xs) / len(xs)) ** 0.5, 2)

    return ScenarioAggregate(
        scenario=name,
        runs=n,
        passes=passes,
        pass_rate=round(passes / n, 3) if n else 0.0,
        expected_call_coverage=round(sum(coverage) / n, 3) if n else 0.0,
        latency_p50_s=_percentile(durations, 0.50),
        latency_p95_s=_percentile(durations, 0.95),
        latency_mean_s=round(sum(durations) / n, 2) if n else 0.0,
        # --- the three layers, each aggregated on its own axis ---
        avg_tool_calls=round(sum(tool_calls) / n, 2) if n else 0.0,     # agno loop length
        tool_calls_p95=_percentile([float(x) for x in tool_calls], 0.95),
        tool_calls_stdev=_stdev([float(x) for x in tool_calls]),        # predictability
        avg_code_exec_calls=round(sum(r.n_code_exec_calls for r in records) / n, 2) if n else 0.0,
        avg_sdk_calls=round(sum(r.n_sdk_calls for r in records) / n, 2) if n else 0.0,  # sandbox
        avg_wasted_calls=round(sum(r.n_wasted_calls for r in records) / n, 2) if n else 0.0,  # waste
        avg_reasoning_steps=round(sum(r.reasoning.n_reasoning_steps
                                      for r in records) / n, 2) if n else 0.0,  # thinking
        timeouts=sum(r.timed_out for r in records),
        errors=sum(bool(r.error) and not r.timed_out for r in records),
        mean_tokens_in=round(sum(in_tok) / n) if n else 0,
        mean_tokens_out=round(sum(out_tok) / n) if n else 0,
        cost_usd=round(sum(r.run_meta.cost or 0.0 for r in records), 6),  # real provider cost
        executions=[RunDetail(
            index=i, passed=r.passed, duration_s=r.duration_s, n_tool_calls=r.n_tool_calls,
            n_sdk_calls=r.n_sdk_calls, n_wasted_calls=r.n_wasted_calls,
            n_reasoning_steps=r.reasoning.n_reasoning_steps,
            tokens_out=r.run_meta.output_tokens or 0, cost_usd=r.run_meta.cost or 0.0,
            log_file=r.log_file,
        ) for i, r in enumerate(records, 1)],
    )


def _print_table(rows: list[ScenarioAggregate]) -> None:
    # tool/sdk/wasted are the three abstraction layers; tc_std is run-to-run predictability.
    cols = [("scenario", 22), ("runs", 5), ("pass_rate", 10), ("coverage", 9),
            ("p50_s", 7), ("tool", 6), ("tc_std", 7), ("sdk", 5), ("wasted", 7),
            ("think", 6), ("tok_out", 8), ("cost_$", 9)]
    header = "  ".join(h.ljust(w) for h, w in cols)
    print("\n" + header)
    print("-" * len(header))
    for r in rows:
        line = "  ".join(str(v).ljust(w) for v, (_, w) in zip([
            r.scenario, r.runs, f'{r.pass_rate:.0%}', f'{r.expected_call_coverage:.0%}',
            r.latency_p50_s, r.avg_tool_calls, r.tool_calls_stdev,
            r.avg_sdk_calls, r.avg_wasted_calls, r.avg_reasoning_steps,
            r.mean_tokens_out, f'{r.cost_usd:.4f}',
        ], cols))
        print(line)
    print(f"\ntotal cost: ${sum(r.cost_usd for r in rows):.4f}")


def _resolve_model():
    from backend.config import settings
    from backend.models.factory import get_model
    try:
        m = get_model()
    except Exception as exc:
        print(f"No model configured ({exc}).\n"
              f"Set MODEL_PROVIDER/MODEL_ID + an API key in .env (provider={settings.model_provider}).",
              file=sys.stderr)
        sys.exit(2)
    if m is None:
        print("No model configured (get_model() returned None).", file=sys.stderr)
        sys.exit(2)
    # Ask OpenRouter to return the REAL per-call cost (usage accounting). Observability
    # only — it doesn't change the request's behavior/output, so no drift vs production.
    if settings.model_provider == "openrouter" and hasattr(m, "extra_body"):
        m.extra_body = {**(m.extra_body or {}), "usage": {"include": True}}
    return m


# --- one run ---------------------------------------------------------------
def _run_one(sc, model, args, git_rev) -> RunRecord:
    """Dispatch one scenario-run to the right runner and stamp the git rev."""
    if isinstance(sc, ExecutionScenario):
        rec = run_execution_scenario(sc, model)
    elif isinstance(sc, ConvScenario):
        rec = run_conv_scenario(sc, model)
    else:
        rec = run_scenario(sc, model, tool_call_limit=args.limit,
                           timeout_s=args.timeout, tool_choice=args.tool_choice)
    rec.git_rev = git_rev
    return rec


def _print_run_line(sc_name: str, i: int, runs: int, rec, log_name: str) -> None:
    """`rec` may be a real RunRecord (sequential) or a result namespace (parallel) — both
    expose the same attributes used here."""
    flag = "PASS" if rec.passed else ("TIMEOUT" if rec.timed_out else "FAIL")
    actual = rec.grade.calls.work.actual if rec.grade else []
    print(f"  [{sc_name} {i}/{runs}] {flag}  {rec.duration_s}s  "
          f"tool={rec.n_tool_calls}(ce={rec.n_code_exec_calls},waste={rec.n_wasted_calls})  "
          f"sdk={rec.n_sdk_calls}  think={rec.reasoning.n_reasoning_steps}  "
          f"actual={actual}  -> {log_name}")


# --- IPC between the parent orchestrator and the per-run child subprocess ----
def _flat_result(sc_name: str, i: int, rec: RunRecord) -> dict:
    """The minimal, JSON-flat view a child emits for the parent to aggregate (computed in
    the child, where RunRecord's properties like grade.calls.n_passed are live)."""
    return {
        "scenario": sc_name, "run_index": i, "model_id": rec.model_id,
        "passed": rec.passed, "timed_out": rec.timed_out, "error": rec.error,
        "duration_s": rec.duration_s, "n_tool_calls": rec.n_tool_calls,
        "n_code_exec_calls": rec.n_code_exec_calls, "n_sdk_calls": rec.n_sdk_calls,
        "n_wasted_calls": rec.n_wasted_calls, "n_reasoning_steps": rec.reasoning.n_reasoning_steps,
        "input_tokens": rec.run_meta.input_tokens, "output_tokens": rec.run_meta.output_tokens,
        "cost": rec.run_meta.cost,
        "n_passed": rec.grade.calls.n_passed if rec.grade else 0,
        "n_expected": rec.grade.calls.n_expected if rec.grade else 0,
        "work_actual": rec.grade.calls.work.actual if rec.grade else [],
        "log_file": rec.log_file,
    }


def _failed_result(sc_name: str, i: int, error: str, *, timed_out: bool = False) -> dict:
    """A child that crashed/timed out before emitting — recorded as a failed run, not lost."""
    return {"scenario": sc_name, "run_index": i, "model_id": "?", "passed": False,
            "timed_out": timed_out, "error": error, "duration_s": 0.0, "n_tool_calls": 0,
            "n_code_exec_calls": 0, "n_sdk_calls": 0, "n_wasted_calls": 0, "n_reasoning_steps": 0,
            "input_tokens": 0, "output_tokens": 0, "cost": 0.0, "n_passed": 0, "n_expected": 0,
            "work_actual": [], "log_file": ""}


def _result_ns(d: dict):
    """Wrap a flat result dict in the attribute shape `_aggregate`/`_print_run_line` expect."""
    return SimpleNamespace(
        passed=d["passed"], timed_out=d["timed_out"], error=d["error"], duration_s=d["duration_s"],
        n_tool_calls=d["n_tool_calls"], n_code_exec_calls=d["n_code_exec_calls"],
        n_sdk_calls=d["n_sdk_calls"], n_wasted_calls=d["n_wasted_calls"],
        reasoning=SimpleNamespace(n_reasoning_steps=d["n_reasoning_steps"]),
        run_meta=SimpleNamespace(input_tokens=d["input_tokens"], output_tokens=d["output_tokens"],
                                 cost=d["cost"]),
        grade=SimpleNamespace(calls=SimpleNamespace(
            n_passed=d["n_passed"], n_expected=d["n_expected"],
            work=SimpleNamespace(actual=d["work_actual"]))),
        log_file=d["log_file"])


def _run_child(sc_name: str, i: int, args) -> dict:
    """Run ONE scenario-run in a fresh subprocess (full process isolation — its own module
    globals, get_agno_db cache, monkeypatches), collect its emitted flat result."""
    emit = Path("tmp") / f".emit_{sc_name}_{i}.json"
    cmd = [sys.executable, "-m", "evals.run", sc_name, "--runs", "1", "--no-open",
           "--emit", str(emit), "--run-index", str(i),
           "--limit", str(args.limit), "--timeout", str(args.timeout)]
    if args.tool_choice:
        cmd += ["--tool-choice", args.tool_choice]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=args.timeout + 90)
    except subprocess.TimeoutExpired:
        return _failed_result(sc_name, i, "child subprocess timed out", timed_out=True)
    if emit.exists():
        try:
            d = json.loads(emit.read_text())
            emit.unlink()
            return d
        except Exception as exc:
            return _failed_result(sc_name, i, f"unreadable emit: {exc}")
    return _failed_result(sc_name, i, f"child exited {proc.returncode}: {(proc.stderr or '')[-300:]}")


# --- run all scenarios, sequential or subprocess-parallel -------------------
def _run_sequential(scenarios, args, model, git_rev) -> list[ScenarioAggregate]:
    rows = []
    for sc in scenarios:
        recs = []
        for i in range(1, args.runs + 1):
            rec = _run_one(sc, model, args, git_rev)
            log_path = write_log(rec, run_index=i if args.runs > 1 else None)
            rec.log_file = log_path.with_suffix(".txt").name
            _print_run_line(sc.name, i, args.runs, rec, log_path.name)
            recs.append(rec)
        rows.append(_aggregate(sc.name, recs))
    return rows


def _run_parallel(scenarios, args) -> list[ScenarioAggregate]:
    tasks = [(sc, i) for sc in scenarios for i in range(1, args.runs + 1)]
    print(f"parallel: {len(tasks)} runs across {args.workers} subprocess workers\n")
    Path("tmp").mkdir(parents=True, exist_ok=True)
    results: dict[str, list[tuple[int, dict]]] = {sc.name: [] for sc in scenarios}
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(_run_child, sc.name, i, args): (sc.name, i) for sc, i in tasks}
        for fut in as_completed(futs):
            sc_name, i = futs[fut]
            d = fut.result()
            results[sc_name].append((i, d))
            _print_run_line(sc_name, i, args.runs, _result_ns(d), d.get("log_file") or "(no log)")
    return [_aggregate(sc.name, [_result_ns(d) for _, d in sorted(results[sc.name])])
            for sc in scenarios]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run Mai evals against the live model.")
    ap.add_argument("scenarios", nargs="*", help="scenario names (default: all)")
    ap.add_argument("--runs", type=int, default=1, help="runs per scenario (default 1)")
    ap.add_argument("--limit", type=int, default=DEFAULT_TOOL_CALL_LIMIT,
                    help=f"tool_call_limit / max code_exec calls (default {DEFAULT_TOOL_CALL_LIMIT})")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S,
                    help=f"per-run wall-clock bound in seconds (default {DEFAULT_TIMEOUT_S})")
    ap.add_argument("--threshold", type=float, default=0.66,
                    help="min pass-rate per scenario for exit 0 (default 0.66)")
    ap.add_argument("--tool-choice", default=None,
                    help="agno tool_choice, e.g. 'required' (default: unset, model chooses)")
    ap.add_argument("--no-open", action="store_true",
                    help="don't open the HTML report in a browser (default: open it; use in CI/headless)")
    ap.add_argument("--workers", type=int, default=1,
                    help="run this many runs in parallel, each in an ISOLATED subprocess "
                         "(default 1 = sequential, in-process)")
    ap.add_argument("--emit", default=None, help=argparse.SUPPRESS)        # child: write flat result here
    ap.add_argument("--run-index", type=int, default=None, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    scenarios = ([get_scenario(n) for n in args.scenarios] if args.scenarios else
                 [*SCENARIOS, *EXECUTION_SCENARIOS, *CONV_SCENARIOS])
    git_rev = _git_rev()

    # CHILD mode: run exactly one (scenario, run), write its log, emit the flat result, done.
    if args.emit:
        rec = _run_one(scenarios[0], _resolve_model(), args, git_rev)
        log_path = write_log(rec, run_index=args.run_index)
        rec.log_file = log_path.with_suffix(".txt").name
        Path(args.emit).write_text(json.dumps(_flat_result(scenarios[0].name,
                                                            args.run_index or 1, rec)))
        return 0

    # PARENT: resolve the model once (fails fast on a bad key + gives the report header its id).
    # In parallel mode children resolve their own model; the parent doesn't run scenarios itself.
    model = _resolve_model()
    model_id = str(getattr(model, "id", "?"))
    print(f"model={model_id}  rev={git_rev}  runs={args.runs}  tool_call_limit={args.limit}  "
          f"timeout={args.timeout}s  workers={args.workers}  (production tool_call_limit=6)")
    print(f"logs -> {LOGS_DIR}")

    all_rows = (_run_parallel(scenarios, args) if args.workers > 1
                else _run_sequential(scenarios, args, model, git_rev))

    _print_table(all_rows)

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    summary = RunSummary(
        model_id=model_id, git_rev=git_rev, runs_per_scenario=args.runs,
        tool_call_limit=args.limit, timeout_s=args.timeout, threshold=args.threshold,
        generated_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        total_cost_usd=round(sum(r.cost_usd for r in all_rows), 6),
        scenarios=all_rows,
    )
    summary_path = LOGS_DIR / f"{ts}__summary.json"
    summary_path.write_text(json.dumps(asdict(summary), indent=2, ensure_ascii=False),
                            encoding="utf-8")
    report_path = write_report(summary, LOGS_DIR / f"{ts}__report.html")
    print(f"\nsummary -> {summary_path}")
    print(f"report  -> {report_path}")
    if not args.no_open:
        try:  # opening the report must never affect the run's exit status
            webbrowser.open(report_path.as_uri())
        except Exception:
            pass

    failed = [r.scenario for r in all_rows if r.pass_rate < args.threshold]
    if failed:
        print(f"\nBELOW THRESHOLD ({args.threshold:.0%}): {failed}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
