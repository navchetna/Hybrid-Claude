#!/usr/bin/env python3
"""emit_traces.py — reshape the plugin proxy's raw call log into finetune traces.

Turns the raw per-call log written by the proxy's trace callback
(scripts/litellm_trace_logger.py -> out/traces/calls.jsonl) into the SAME SFT
`input -> output` finetune format the cost study shipped
(experiment/results/finetune/README.md), MINUS the evaluation-only fields
(resolved_full / resolved_f2p / run_status) — this driver produces patches, it does
not run SWE-bench evaluation, so those are omitted.

Two files are written, split by the call's model, mirroring the study's names:
  opus_contract_traces.jsonl   <- orchestrator (Opus / any non-Qwen) calls
  qwen_contract_traces.jsonl   <- subagent (Qwen contractor) calls

WHY A RESHAPER (not the log itself): capture is route-blind (the proxy has no idea
which task/run a call belongs to); attribution is driver-aware. The driver writes
out/runs.jsonl (one line per run: task, repeat, start/end epoch) and runs strictly
sequentially, so each raw call is assigned to the run whose [start,end] window
contains its start_epoch. That is also how stale/other-session calls get filtered.

Record schema per line (matches the study's, minus eval fields):
  input.{system[], messages[], tools[]}          <- the request body sent to the model
  output.{role, content, tool_calls, function_call}   <- the model's reply
  model, provider, request_id, session_id, start_time, status, finish_reason,
  prompt_tokens, completion_tokens, total_tokens, spend
  turn_index (0-based within a run+model), is_final_turn (finish_reason == "stop")
  run.{task, alias, tier, variant, repeat, snapshot}   <- NO resolved_*/run_status

TRUNCATION CAVEAT carries over: most contractor calls end finish_reason=="tool_calls"
(mid-investigation stubs), not "stop". Use is_final_turn / --final-only to filter.

Usage (the driver calls this automatically at the end of a run; also runnable by hand):
  ./emit_traces.py                    # reads out/traces/calls.jsonl + out/runs.jsonl
  ./emit_traces.py --final-only       # subagent file: keep only finish_reason=="stop"
  ./emit_traces.py --calls X --runs Y --out-dir Z
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def classify(model: str) -> str:
    """Bucket a model name into orchestrator ('opus') vs subagent ('qwen')."""
    m = (model or "").lower()
    if "qwen" in m:
        return "qwen"
    if "haiku" in m:
        return "haiku"
    return "opus"   # orchestrator is Claude (opus by default); anything non-Qwen


def read_jsonl(path: str):
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"  (skipping unparseable line in {os.path.basename(path)}: {e})",
                      file=sys.stderr)
    return rows


def load_runs(path: str):
    """Sorted list of run windows: (start_epoch, end_epoch, run_meta_dict)."""
    runs = []
    for r in read_jsonl(path):
        task = r.get("task")
        runs.append((
            float(r.get("start_epoch") or 0.0),
            float(r.get("end_epoch") or 0.0),
            {
                "task":    task,
                "alias":   task,
                "tier":    r.get("tier", ""),
                "variant": r.get("variant", "contract"),
                "repeat":  r.get("repeat"),
                "snapshot": None,   # no campaign snapshot — this is a live plugin run
            },
        ))
    runs.sort(key=lambda x: x[0])
    return runs


def attribute(call_epoch, runs):
    """Return the run_meta whose [start,end] window contains call_epoch, else None."""
    if call_epoch is None:
        return None
    for start, end, meta in runs:
        # end may be 0 if the driver crashed before stamping it; treat as open-ended.
        if start <= call_epoch and (end == 0.0 or call_epoch <= end):
            return meta
    return None


def to_record(call: dict, run_meta: dict, turn_index: int) -> dict:
    """One raw call + its run -> one finetune-schema line (eval fields omitted)."""
    fr = call.get("finish_reason")
    out = call.get("output") or {}
    return {
        "input": call.get("input") or {"system": [], "messages": [], "tools": []},
        "output": {
            "role": out.get("role", "assistant"),
            "content": out.get("content", ""),
            "tool_calls": out.get("tool_calls"),
            "function_call": out.get("function_call"),
        },
        "model":             call.get("model"),
        "provider":          call.get("provider"),
        "request_id":        call.get("request_id"),
        "session_id":        call.get("session_hint"),
        "start_time":        call.get("start_time"),
        "status":            call.get("status", "success"),
        "finish_reason":     fr,
        "prompt_tokens":     call.get("prompt_tokens"),
        "completion_tokens": call.get("completion_tokens"),
        "total_tokens":      call.get("total_tokens"),
        "spend":             call.get("spend", 0),
        "turn_index":        turn_index,
        "is_final_turn":     (fr == "stop"),
        "run":               run_meta,   # no resolved_*/run_status — not evaluated here
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--calls", default=os.path.join(HERE, "out", "traces", "calls.jsonl"),
                    help="raw per-call log written by the proxy trace callback")
    ap.add_argument("--runs", default=os.path.join(HERE, "out", "runs.jsonl"),
                    help="per-run windows written by run_ft_tasks.sh")
    ap.add_argument("--out-dir", default=os.path.join(HERE, "out"),
                    help="where to write the *_traces.jsonl files")
    ap.add_argument("--variant", default="contract",
                    help="run.variant label + trace filename infix (default: contract)")
    ap.add_argument("--final-only", action="store_true",
                    help="subagent file: keep only finish_reason=='stop' (drop stubs)")
    args = ap.parse_args()

    if not os.path.exists(args.calls):
        sys.exit(f"no raw call log at {args.calls} — was the proxy started with "
                 f"CCX_TRACE_DIR set? (run_ft_tasks.sh sets it). Nothing to emit.")
    calls = read_jsonl(args.calls)
    runs = load_runs(args.runs) if os.path.exists(args.runs) else []
    if not runs:
        print(f"  (no run windows at {args.runs}; run.* will be null and all calls kept)",
              file=sys.stderr)

    # chronological, so turn_index counts up within each (run, model) as calls happen.
    calls.sort(key=lambda c: (c.get("start_epoch") or 0.0))

    buckets = {"opus": [], "qwen": [], "haiku": []}
    turn_idx = {}          # (run_task, repeat, bucket) -> last index
    unattributed = 0
    for c in calls:
        meta = attribute(c.get("start_epoch"), runs) if runs else {
            "task": None, "alias": None, "tier": "", "variant": args.variant,
            "repeat": None, "snapshot": None}
        if runs and meta is None:
            unattributed += 1
            continue   # a call outside every run window (stale / other session)
        bucket = classify(c.get("model"))
        key = (meta.get("task"), meta.get("repeat"), bucket)
        turn_idx[key] = turn_idx.get(key, -1) + 1
        buckets.setdefault(bucket, []).append(to_record(c, meta, turn_idx[key]))

    subagent = max(("qwen", "haiku"), key=lambda b: len(buckets.get(b, [])))
    os.makedirs(args.out_dir, exist_ok=True)
    written = {}
    for bucket, recs in buckets.items():
        if not recs:
            continue
        if bucket == subagent and args.final_only:
            recs = [r for r in recs if r["is_final_turn"]]
        path = os.path.join(args.out_dir, f"{bucket}_{args.variant}_traces.jsonl")
        with open(path, "w") as f:
            for r in recs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        written[os.path.basename(path)] = len(recs)

    # report + subagent truncation breakdown (mirrors the study's manifest)
    print(f"emit_traces: {len(calls)} raw calls; "
          f"{unattributed} unattributed (dropped)", file=sys.stderr)
    for name, n in written.items():
        print(f"  {name}: {n} records", file=sys.stderr)
    fr_counts = {}
    for r in buckets.get(subagent, []):
        fr = r.get("finish_reason") or "null"
        fr_counts[fr] = fr_counts.get(fr, 0) + 1
    if fr_counts:
        print(f"  {subagent} finish_reason breakdown (stub = 'tool_calls'):", file=sys.stderr)
        for fr, n in sorted(fr_counts.items(), key=lambda x: -x[1]):
            print(f"    {fr:12s} {n}", file=sys.stderr)


if __name__ == "__main__":
    main()
