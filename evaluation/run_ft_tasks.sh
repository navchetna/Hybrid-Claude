#!/usr/bin/env bash
# run_ft_tasks.sh — run the 20 fine-tune SWE-bench tasks (50 sessions) through the
# local-subagent plugin: Opus fixes each bug, the local Qwen contractor investigates.
# See README.md for setup + FAQ. Quick start:
#   export ANTHROPIC_BASE_URL=... ANTHROPIC_AUTH_TOKEN=...   # from /local-subagent:setup
#   ./run_ft_tasks.sh                       # all 20 tasks = 50 runs
#   ./run_ft_tasks.sh django__django-11087  # one task     REPEATS=1 = 1 run/task
# Output: out/<task>__rN.patch, out/<task>__rN.log, out/summary.tsv
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TASKS="$HERE/ft_tasks.jsonl"
WORK="${WORK:-$HERE/work}"          # repo checkouts live here (cached, reused)
OUT="${OUT:-$HERE/out}"             # per-run patch + log + summary
MODEL="${MODEL:-${ANTHROPIC_DEFAULT_SONNET_MODEL:-us.anthropic.claude-opus-4-8}}"     # main-loop (orchestrator) model
TASK_TIMEOUT="${TASK_TIMEOUT:-1800}"               # seconds per run
REPEATS="${REPEATS:-}"              # force N runs/task; default = the task's "runs" field
VARIANT="${VARIANT:-contract}"     # run.variant label written into the emitted traces
# Finetune-trace capture. When the proxy was started with CCX_TRACE_DIR (see
# /local-subagent:setup + README "Produce fine-tune-format traces"), it appends every
# model call to $CCX_TRACE_DIR/calls.jsonl. The driver records one run-window line per
# run (out/runs.jsonl) so emit_traces.py can attribute each call to a (task,repeat).
TRACE_DIR="${CCX_TRACE_DIR:-$OUT/traces}"
CALLS_LOG="$TRACE_DIR/calls.jsonl"
RUNS_LOG="$OUT/runs.jsonl"
EMIT="${EMIT_TRACES:-auto}"        # auto = emit if the proxy captured calls; off = never
# Accept either environment-variable naming convention automatically.
PROXY_BASE_URL="${ANTHROPIC_BASE_URL:-${ANTHROPIC_FOUNDRY_BASE_URL:-}}"
PROXY_API_KEY="${ANTHROPIC_AUTH_TOKEN:-${ANTHROPIC_FOUNDRY_API_KEY:-}}"

die() { printf 'run_ft_tasks: ERROR: %s\n' "$*" >&2; exit 1; }

# The plugin lives one directory above this script; resolve it relative to the
# evaluation folder so the user can run this script from anywhere in the repo.
PLUGIN_ROOT="$(cd "$HERE/.." && pwd)"

register_local_plugin() {
  echo "run_ft_tasks: registering local plugin from $PLUGIN_ROOT"
  if ! claude plugin marketplace add "$PLUGIN_ROOT" >/tmp/local_subagent_marketplace_add.log 2>&1; then
    # Ignore duplicate/previously-registered entries; the install step is the
    # important part for idempotent reruns.
    if ! claude plugin marketplace list 2>/dev/null | grep -Fq "$PLUGIN_ROOT"; then
      cat /tmp/local_subagent_marketplace_add.log >&2 || true
      die "failed to register local plugin at $PLUGIN_ROOT"
    fi
  fi

  echo "run_ft_tasks: installing local-subagent plugin"
  if ! claude plugin install local-subagent >/tmp/local_subagent_install.log 2>&1; then
    if ! claude plugin list 2>/dev/null | grep -qi 'local-subagent'; then
      cat /tmp/local_subagent_install.log >&2 || true
      die "failed to install local-subagent plugin"
    fi
  fi
}

# ── preflight ─────────────────────────────────────────────────────────────────
command -v claude >/dev/null || die "claude CLI not on PATH."
command -v git >/dev/null || die "git not on PATH."
command -v jq   >/dev/null || die "jq not on PATH."
register_local_plugin
[ -f "$TASKS" ] || die "task list not found: $TASKS"
[ -n "$PROXY_BASE_URL" ] || die "ANTHROPIC_BASE_URL or ANTHROPIC_FOUNDRY_BASE_URL not set — run /local-subagent:setup, then export one of them (see PREREQUISITES)."
[ -n "$PROXY_API_KEY" ] || die "ANTHROPIC_AUTH_TOKEN or ANTHROPIC_FOUNDRY_API_KEY not set — export one of them before running tasks."
curl -sf "$PROXY_BASE_URL/health/liveliness" -m 5 >/dev/null 2>&1 \
  || die "proxy at ANTHROPIC_BASE_URL=$PROXY_BASE_URL is not answering — is LiteLLM up?"
claude plugin list 2>/dev/null | grep -qi 'local-subagent' \
  || echo "run_ft_tasks: WARN: 'local-subagent' not shown by 'claude plugin list' — ensure it is installed & enabled." >&2

mkdir -p "$WORK" "$OUT"
: > "$OUT/summary.tsv"
: > "$RUNS_LOG"                     # per-run windows for trace attribution (fresh each launch)

printf 'instance_id\trun\tpatch_lines\texit_code\n' > "$OUT/summary.tsv"

# Note the trace-capture state up front so the operator isn't surprised at the end.
if [ -f "$CALLS_LOG" ]; then
  echo "run_ft_tasks: trace capture ON — proxy is appending to $CALLS_LOG"
else
  echo "run_ft_tasks: trace capture not detected at $CALLS_LOG."
  echo "              To emit fine-tune-format traces, start the proxy with CCX_TRACE_DIR set"
  echo "              (see README 'Produce fine-tune-format traces'). Patches still generate normally."
fi

# Which tasks: all, or the instance_ids passed as args.
mapfile -t ALL_IDS < <(jq -r '.instance_id' "$TASKS")
if [ "$#" -gt 0 ]; then IDS=("$@"); else IDS=("${ALL_IDS[@]}"); fi

# Planned total runs = sum of each selected task's run count (REPEATS overrides).
PLANNED=0
for IID in "${IDS[@]}"; do
  n="$(jq -r --arg i "$IID" 'select(.instance_id==$i) | .runs // 1' "$TASKS")"
  [ -n "$n" ] || continue
  PLANNED=$(( PLANNED + ${REPEATS:-$n} ))
done
echo "run_ft_tasks: ${#IDS[@]} task(s), ${PLANNED} planned run(s); model=$MODEL; timeout=${TASK_TIMEOUT}s; proxy=$PROXY_BASE_URL"

# ── per-task, per-run ─────────────────────────────────────────────────────────────
for IID in "${IDS[@]}"; do
  REC="$(jq -c --arg i "$IID" 'select(.instance_id==$i)' "$TASKS")"
  [ -n "$REC" ] || { echo "run_ft_tasks: SKIP $IID (not in task list)"; continue; }
  REPO="$(jq -r '.repo' <<<"$REC")"                 # e.g. django/django
  BASE="$(jq -r '.base_commit' <<<"$REC")"
  F2P="$(jq -r '.FAIL_TO_PASS | if type=="array" then join(", ") else . end' <<<"$REC")"
  NRUNS="${REPEATS:-$(jq -r '.runs // 1' <<<"$REC")}"   # per-task runs, per SHARED_TASK_LIST
  SLUG="${REPO//\//__}"
  CLONE="$WORK/$SLUG"
  echo "──────── $IID  ($REPO @ ${BASE:0:10})  ×${NRUNS} run(s) ────────"

  # one cached clone per repo (fetched once; each run re-checks-out clean below)
  if [ ! -d "$CLONE/.git" ]; then
    echo "  cloning https://github.com/$REPO ..."
    git clone -q "https://github.com/$REPO" "$CLONE" || { echo "  clone FAILED"; for r in $(seq 1 "$NRUNS"); do printf '%s\t%s\t-\tclone_fail\n' "$IID" "$r" >>"$OUT/summary.tsv"; done; continue; }
  fi
  git -C "$CLONE" fetch -q origin "$BASE" 2>/dev/null || git -C "$CLONE" fetch -q --all 2>/dev/null || true

  # build the contract prompt once (problem + the failing test); reused each run
  PROMPT_FILE="$(mktemp)"
  { jq -r '.problem_statement' <<<"$REC"
    printf '\n\nThe failing test(s) to make pass: %s\n' "$F2P"
  } > "$PROMPT_FILE"

  for r in $(seq 1 "$NRUNS"); do
    TAG="${IID}__r${r}"
    # fresh checkout of base_commit for EVERY run (each run mutates the tree)
    if ! git -C "$CLONE" checkout -qf "$BASE" 2>/dev/null; then
      echo "  [r$r] checkout $BASE FAILED"; printf '%s\t%s\t-\tcheckout_fail\n' "$IID" "$r" >>"$OUT/summary.tsv"; continue
    fi
    git -C "$CLONE" clean -qfdx

    # run headless, IN the checkout, routed through the plugin's LiteLLM.
    # env -u CLAUDE_CODE_USE_BEDROCK is CRUCIAL: with it set, Claude Code talks to
    # Bedrock DIRECTLY and ignores ANTHROPIC_BASE_URL, so the contractor's Qwen model
    # name would 400. Unsetting it makes the whole session flow through the proxy,
    # which does the Claude passthrough for the main loop and Qwen for the contractor.
    # Bracket the run with epoch stamps so emit_traces.py can attribute each captured
    # model call to THIS (task, repeat). Runs are strictly sequential -> windows don't overlap.
    T0="$(date +%s.%N)"
    ( cd "$CLONE" && \
      env -u CLAUDE_CODE_USE_BEDROCK \
          ANTHROPIC_BASE_URL="$PROXY_BASE_URL" \
          ANTHROPIC_FOUNDRY_BASE_URL="$PROXY_BASE_URL" \
          ANTHROPIC_AUTH_TOKEN="$PROXY_API_KEY" \
          ANTHROPIC_FOUNDRY_API_KEY="$PROXY_API_KEY" \
          ANTHROPIC_API_KEY="$PROXY_API_KEY" \
          timeout "$TASK_TIMEOUT" \
          claude -p "/local-subagent:contract $(cat "$PROMPT_FILE")" \
            --permission-mode acceptEdits \
            --allowedTools "Task,Read,Edit,Write,Bash,Grep,Glob" \
            --model "$MODEL" \
            --output-format text ) \
        > "$OUT/$TAG.log" 2>&1
    RC=$?
    T1="$(date +%s.%N)"
    # one run-window line (task, repeat, variant, [start,end]) for trace attribution
    printf '{"task":"%s","repeat":%s,"variant":"%s","start_epoch":%s,"end_epoch":%s}\n' \
      "$IID" "$r" "$VARIANT" "$T0" "$T1" >> "$RUNS_LOG"

    git -C "$CLONE" diff > "$OUT/$TAG.patch"
    LINES=$(wc -l < "$OUT/$TAG.patch")
    printf '%s\t%s\t%s\t%s\n' "$IID" "$r" "$LINES" "$RC" >> "$OUT/summary.tsv"
    echo "  [r$r] → patch: $LINES diff lines, exit $RC  (out/$TAG.patch, out/$TAG.log)"
  done
  rm -f "$PROMPT_FILE"
done

echo "════════ done ════════"
echo "summary (instance_id / run / patch_lines / exit_code):"
column -t "$OUT/summary.tsv"
NRUN=$(($(wc -l < "$OUT/summary.tsv") - 1))
NPATCH=$(awk -F'\t' 'NR>1 && $3>0{n++} END{print n+0}' "$OUT/summary.tsv")
echo "completed $NRUN run(s); produced a non-empty patch on $NPATCH of them."

# ── emit fine-tune-format traces (only if the proxy captured any calls) ─────────────
if [ "$EMIT" != "off" ] && [ -s "$CALLS_LOG" ]; then
  echo "──────── emitting fine-tune-format traces ────────"
  if python3 "$HERE/emit_traces.py" --calls "$CALLS_LOG" --runs "$RUNS_LOG" \
       --out-dir "$OUT" --variant "$VARIANT"; then
    echo "traces: out/opus_${VARIANT}_traces.jsonl (orchestrator) + out/qwen_${VARIANT}_traces.jsonl (contractor)"
    echo "        format matches experiment/results/finetune/README.md, minus the eval fields."
  else
    echo "traces: emit_traces.py failed (see above) — raw calls are still at $CALLS_LOG" >&2
  fi
elif [ "$EMIT" != "off" ]; then
  echo "traces: none emitted (no captured calls at $CALLS_LOG; start the proxy with CCX_TRACE_DIR to capture)."
fi
