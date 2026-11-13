#!/usr/bin/env python3
"""
Convert the published loom-fun catalog (assets/loom-data.js) into the compact
JSON the demo serves.

Run:
    python3 scripts/build_catalog.py --input <loom-data.js> --output data/catalog.json

WHY THIS SCRIPT EXISTS (read before changing the extraction):

The upstream record carries benchmark numbers in TWO places:

    model.performance.metrics            <- often entirely null
    model.performance.devlab.metrics     <- the populated one, with provenance

Measured on the 2026-11-13 snapshot of the published catalog:

    top-level populated only ....  0 / 51
    devlab populated only ....... 28 / 51
    both populated .............. 23 / 51
    neither ......................  0 / 51

So reading `performance.metrics` alone silently drops 28 of 51 models (55%) to
"no data" while looking perfectly healthy. Where BOTH are populated the values
agree exactly (0 differing fields across all 9 workloads x 23 models), so
`devlab` is a strict superset and is the correct canonical read.

`devlab` additionally carries `date` and `source.url` (a GitHub Actions run),
which is the only per-number provenance available. That is why the demo reads
devlab first and falls back to top level, and why it records where each number
came from instead of flattening them together.

Second rule, inherited from how the upstream card generator behaves: a missing
number is NOT zero and NOT parity. It stays None and renders as "no evidence".
Never substitute 1.0 for a missing speedup - that invents parity with llama.cpp
that nobody measured.
"""

import argparse
import json
import pathlib
import statistics
import sys

# The nine benchmark workloads, in the order the demo displays them.
WORKLOADS = [
    "decode",
    "decode_2k",
    "decode_32k",
    "prefill_1k",
    "prefill_2k",
    "prefill_8k",
    "prefill_16k",
    "prefill_32k",
    "streams4",
]

WORKLOAD_LABELS = {
    "decode": "decode",
    "decode_2k": "decode @2k",
    "decode_32k": "decode @32k",
    "prefill_1k": "prefill 1k",
    "prefill_2k": "prefill 2k",
    "prefill_8k": "prefill 8k",
    "prefill_16k": "prefill 16k",
    "prefill_32k": "prefill 32k",
    "streams4": "4 streams",
}

# Validation gates, from the loom-skills README.
GATE_LABELS = {
    "kernel_tests": "kernel tests",
    "golden": "golden",
    "compare": "reference compare",
    "agent": "tool loop",
    "agent_think_off": "tool loop, thinking off",
    "consistency": "concurrency",
    "sampling": "sampling",
    "vision": "vision",
    "spec": "speculative drafting",
    "needle": "long context",
    "needle_full": "context limit",
    "over_capacity": "over capacity",
    "decision": "decision",
}


def parse_loom_data(path: pathlib.Path) -> dict:
    """Pull the JSON object out of `window.LOOM_DATA = {...};`.

    The upstream file is JS, not JSON, and the fetched copy may carry HTTP
    headers ahead of the body, so we locate the assignment rather than
    assuming the file starts with it.
    """
    raw = path.read_text(encoding="utf-8")
    marker = "window.LOOM_DATA"
    if marker not in raw:
        raise SystemExit(
            f"{path}: no `window.LOOM_DATA` assignment found. "
            "Is this really the catalog asset from loom-dev/assets/loom-data.js?"
        )
    body = raw[raw.index(marker):]
    body = body[body.index("=") + 1:].strip()
    # Trim a trailing semicolon and anything after the closing brace.
    depth = 0
    end = None
    for i, ch in enumerate(body):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end is None:
        raise SystemExit(f"{path}: unbalanced braces in LOOM_DATA object")
    return json.loads(body[:end])


def metric(model: dict, workload: str) -> dict:
    """Read one workload for one model, devlab first, top level as fallback.

    Returns loom / llamacpp / speedup plus the source the numbers came from.
    Missing stays None - never coerced to 0 or 1.0.
    """
    perf = model.get("performance") or {}
    devlab = perf.get("devlab") or {}
    dev_metrics = (devlab.get("metrics") or {}).get(workload) or {}
    top_metrics = (perf.get("metrics") or {}).get(workload) or {}

    if dev_metrics.get("loom") is not None or dev_metrics.get("speedup") is not None:
        chosen, origin = dev_metrics, "devlab"
    else:
        chosen, origin = top_metrics, "summary"

    has_any = any(chosen.get(k) is not None for k in ("loom", "llamacpp", "speedup"))
    return {
        "loom": chosen.get("loom"),
        "llamacpp": chosen.get("llamacpp"),
        "speedup": chosen.get("speedup"),
        "origin": origin if has_any else None,
    }


def geomean(values):
    vals = [v for v in values if v and v > 0]
    if not vals:
        return None
    return round(statistics.geometric_mean(vals), 3)


def convert(data: dict) -> dict:
    models_out = []

    for m in data.get("models", []):
        perf = m.get("performance") or {}
        devlab = perf.get("devlab") or {}
        source = devlab.get("source") or {}

        metrics = {w: metric(m, w) for w in WORKLOADS}
        speedups = [metrics[w]["speedup"] for w in WORKLOADS]

        gates_status = (m.get("gates") or {}).get("status") or {}
        gates_summary = (m.get("gates") or {}).get("summary") or {}

        serve = m.get("serve") or {}
        weights = m.get("weights") or {}
        checkpoint = weights.get("checkpoint") or {}

        models_out.append({
            "name": m.get("name"),
            "checkpoint": m.get("checkpoint"),
            "repo": m.get("repo"),
            "license": m.get("license"),
            "service": m.get("service") or {},
            "serve": {
                "device": serve.get("device"),
                "memory_bytes": serve.get("memory_bytes"),
                "context_tokens": serve.get("context_tokens"),
                "rows": serve.get("rows"),
                "max_tokens": serve.get("max_tokens"),
            },
            "source_checkpoint": {
                "repo": checkpoint.get("repo"),
                "file": checkpoint.get("file"),
                "quant": checkpoint.get("quant"),
                "size": checkpoint.get("size"),
            },
            "has_drafter": bool(weights.get("drafter")),
            "has_projector": bool(weights.get("projector")),
            "features": m.get("features") or {},
            "metrics": metrics,
            "geomean_speedup": geomean(speedups),
            "measured_workloads": sum(1 for s in speedups if s is not None),
            "regressions": sorted(
                WORKLOAD_LABELS[w] for w in WORKLOADS
                if metrics[w]["speedup"] is not None and metrics[w]["speedup"] < 1.0
            ),
            "llamacpp_build": perf.get("llamacpp_build"),
            "gates": {"status": gates_status, "summary": gates_summary},
            "evidence": {
                "date": devlab.get("date"),
                "kind": source.get("kind"),
                "run": source.get("run"),
                "url": source.get("url"),
            },
        })

    # Rank by geometric-mean speedup, models with no evidence last.
    models_out.sort(key=lambda x: (x["geomean_speedup"] is None,
                                   -(x["geomean_speedup"] or 0)))

    # Catalog-wide roll-ups, computed over whatever is actually measured.
    def spread(workload):
        vals = [m["metrics"][workload]["speedup"] for m in models_out]
        vals = [v for v in vals if v is not None]
        if not vals:
            return None
        return {
            "n": len(vals),
            "min": round(min(vals), 2),
            "median": round(statistics.median(vals), 2),
            "max": round(max(vals), 2),
            "slower_than_llamacpp": sum(1 for v in vals if v < 1.0),
        }

    gate_tally = {}
    for m in models_out:
        for status in m["gates"]["status"].values():
            gate_tally[status] = gate_tally.get(status, 0) + 1

    origin_tally = {}
    for m in models_out:
        for w in WORKLOADS:
            o = m["metrics"][w]["origin"]
            if o:
                origin_tally[o] = origin_tally.get(o, 0) + 1

    return {
        "generated": data.get("generated"),
        "model_count": len(models_out),
        "workloads": WORKLOADS,
        "workload_labels": WORKLOAD_LABELS,
        "gate_labels": GATE_LABELS,
        "spread": {w: spread(w) for w in WORKLOADS},
        "gate_tally": gate_tally,
        "origin_tally": origin_tally,
        "models": models_out,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True, type=pathlib.Path,
                    help="path to loom-data.js from loom-fun.github.io/loom-dev/assets/")
    ap.add_argument("--output", required=True, type=pathlib.Path)
    args = ap.parse_args()

    out = convert(parse_loom_data(args.input))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(out, indent=1), encoding="utf-8")

    print(f"wrote {args.output}")
    print(f"  catalog generated : {out['generated']}")
    print(f"  models            : {out['model_count']}")
    print(f"  metric origins    : {out['origin_tally']}")
    print(f"  gate tally        : {out['gate_tally']}")
    p32 = out["spread"]["prefill_32k"]
    if p32:
        print(f"  prefill_32k       : n={p32['n']} median={p32['median']}x "
              f"(min {p32['min']}x, max {p32['max']}x, "
              f"{p32['slower_than_llamacpp']} slower than llama.cpp)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
