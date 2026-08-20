"""Render a shareable, self-contained HTML eval report from `report_template.html`.

One file, inline CSS, no external assets — open it or send it as-is. Fed by the same
summary dict run.py writes to `<ts>__summary.json` (model, git rev, per-scenario metrics).
Deliberately tight: the page chrome lives in the template; this only fills the table.

Tags: scenario tags (evals/scenarios.py::SCENARIO_TAGS) are joined by NAME at render
time, so any existing summary.json gains the per-tag panel on re-render — no rerun:

    ./.venv/bin/python -m evals.report evals/logs/<ts>__summary.json
"""
from __future__ import annotations

import html
from pathlib import Path

from evals.harness import RunDetail, RunSummary, ScenarioAggregate
from evals.scenarios import tags_for

TEMPLATE = Path(__file__).resolve().parent / "report_template.html"

_ROW_PARENT = """      <tr class="parent" data-cls="c{i}" data-tags="{tags}">
        <td><span class="caret">&#9656;</span>{name}{pills}</td>
        <td><span class="badge {cls}">{status}</span></td>
        <td>{pass_rate}</td>
        <td>{coverage}</td>
        <td>{tool}</td>
        <td>{sdk}</td>
        <td>{wasted}</td>
        <td>{think}</td>
        <td>{p50}</td>
        <td>{tok}</td>
        <td>{cost}</td>
      </tr>"""

_ROW_CHILD = """      <tr class="child c{i}" data-tags="{tags}">
        <td class="run">{run_cell}</td>
        <td><span class="badge {cls}">{status}</span></td>
        <td></td>
        <td></td>
        <td>{tool}</td>
        <td>{sdk}</td>
        <td>{wasted}</td>
        <td>{think}</td>
        <td>{dur}</td>
        <td>{tok}</td>
        <td>{cost}</td>
      </tr>"""


def _rows(scenarios: list[ScenarioAggregate], threshold: float) -> str:
    rows = []
    for i, s in enumerate(scenarios):
        passed = s.pass_rate >= threshold
        tags = tags_for(s.scenario)
        pills = "".join(f'<span class="tag">{html.escape(t)}</span>' for t in tags)
        rows.append(_ROW_PARENT.format(
            i=i,
            name=html.escape(s.scenario),
            tags=html.escape(" ".join(tags)),
            pills=pills,
            cls="pass" if passed else "fail",
            status="PASS" if passed else "FAIL",
            pass_rate=f"{s.pass_rate:.0%}",
            coverage=f"{s.expected_call_coverage:.0%}",
            tool=s.avg_tool_calls,
            sdk=s.avg_sdk_calls,
            wasted=s.avg_wasted_calls,
            think=s.avg_reasoning_steps,
            p50=s.latency_p50_s,
            tok=s.mean_tokens_out,
            cost=f"{s.cost_usd:.4f}",
        ))
        for e in s.executions:
            # The run label links to its .txt transcript (same logs/ dir as this report).
            label = f"&#8627; run {e.index}"
            run_cell = (f'<a href="{html.escape(e.log_file)}">{label}</a>' if e.log_file else label)
            rows.append(_ROW_CHILD.format(
                i=i,
                tags=html.escape(" ".join(tags)),
                run_cell=run_cell,
                cls="pass" if e.passed else "fail",
                status="PASS" if e.passed else "FAIL",
                tool=e.n_tool_calls,
                sdk=e.n_sdk_calls,
                wasted=e.n_wasted_calls,
                think=e.n_reasoning_steps,
                dur=e.duration_s,
                tok=e.tokens_out,
                cost=f"{e.cost_usd:.4f}",
            ))
    return "\n".join(rows)


def _tag_panel(scenarios: list[ScenarioAggregate], threshold: float) -> str:
    """Per-tag rollup: pass rate over RUNS (not scenario averages — a 10-run tag and a
    3-run tag weigh honestly), rendered as pure-CSS bars + clickable filter chips."""
    agg: dict[str, dict] = {}
    for s in scenarios:
        for t in tags_for(s.scenario):
            a = agg.setdefault(t, {"runs": 0, "passes": 0, "scenarios": 0})
            a["runs"] += s.runs
            a["passes"] += s.passes
            a["scenarios"] += 1
    if not agg:
        return ""
    # Sort worst-first: the tags needing attention lead.
    items = sorted(agg.items(), key=lambda kv: (kv[1]["passes"] / max(1, kv[1]["runs"]),
                                                -kv[1]["runs"]))
    chips = ['<span class="chip active" data-tag="">all</span>'] + [
        f'<span class="chip" data-tag="{html.escape(t)}">{html.escape(t)}</span>'
        for t, _ in sorted(agg.items())]
    bars = []
    for t, a in items:
        rate = a["passes"] / max(1, a["runs"])
        ok = rate >= threshold
        bars.append(
            f'<div class="tagrow" data-tag="{html.escape(t)}">'
            f'<span class="tagname">{html.escape(t)}</span>'
            f'<span class="bar"><span class="fill {"ok" if ok else "bad"}" '
            f'style="width:{rate:.0%}"></span></span>'
            f'<span class="tagnum">{rate:.0%}</span>'
            f'<span class="tagn">{a["passes"]}/{a["runs"]} runs &middot; '
            f'{a["scenarios"]} scen</span></div>')
    return ('<div class="chips">' + "".join(chips) + "</div>\n"
            '<div class="tagbars">' + "\n".join(bars) + "</div>")


def render(summary: RunSummary) -> str:
    repl = {
        "{{MODEL}}": html.escape(summary.model_id),
        "{{GIT_REV}}": html.escape(summary.git_rev),
        "{{GENERATED_AT}}": html.escape(summary.generated_at),
        "{{RUNS}}": str(summary.runs_per_scenario),
        "{{TOOL_CALL_LIMIT}}": str(summary.tool_call_limit),
        "{{TIMEOUT}}": str(summary.timeout_s),
        "{{THRESHOLD}}": f"{summary.threshold:.0%}",
        "{{TOTAL_COST}}": f"{summary.total_cost_usd:.4f}",
        "{{TAG_PANEL}}": _tag_panel(summary.scenarios, summary.threshold),
        "{{ROWS}}": _rows(summary.scenarios, summary.threshold),
    }
    tpl = TEMPLATE.read_text(encoding="utf-8")
    for token, value in repl.items():
        tpl = tpl.replace(token, value)
    return tpl


def write_report(summary: RunSummary, path: Path) -> Path:
    path.write_text(render(summary), encoding="utf-8")
    return path


def load_summary(path: Path) -> RunSummary:
    """Rehydrate a RunSummary from a written <ts>__summary.json (asdict round-trip),
    so an EXISTING run's report can be re-rendered — with tags — without rerunning."""
    import json

    d = json.loads(path.read_text(encoding="utf-8"))
    scenarios = [ScenarioAggregate(**{**s, "executions": [RunDetail(**e)
                                                          for e in s.get("executions", [])]})
                 for s in d.pop("scenarios", [])]
    return RunSummary(**{**d, "scenarios": scenarios})


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        sys.exit("usage: python -m evals.report <path/to/__summary.json>")
    src = Path(sys.argv[1])
    out = write_report(load_summary(src), Path(str(src).replace("__summary.json", "__report.html")))
    print(f"report -> {out}")
