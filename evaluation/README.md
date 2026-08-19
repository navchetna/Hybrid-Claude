# Run the fine-tune SWE-bench tasks through the plugin

This runs the **20 fine-tune SWE-bench Verified tasks** (50 sessions total) through the
`xeon-subagent` plugin: **Opus fixes each bug, the free local Qwen `contractor` does all
the investigation.** You get one patch per run.

## 3 steps

**1. Serve Qwen** (needs a GPU/CPU box; ~30B model):

```bash
vllm serve Qwen/Qwen3-Coder-30B-A3B-Instruct \
  --served-model-name Qwen/Qwen3-Coder-30B-A3B-Instruct \
  --enable-auto-tool-choice --tool-call-parser qwen3_coder --port 8004
```

**2. Set up routing** — in Claude Code, run once:

```
/xeon-subagent:setup
```

It prints a base URL + key. Export them in your terminal:

```bash
export ANTHROPIC_BASE_URL=http://localhost:4000
export ANTHROPIC_AUTH_TOKEN=<the sk-xeon-... key it printed>
```

**3. Run it:**

```bash
./run_ft_tasks.sh                 # all 20 tasks = 50 runs (run detached; it's long)
```

That's it. Results land in `out/`.

## Output

| File | What |
|---|---|
| `out/<task>__rN.patch` | the fix that run produced (empty = no change) |
| `out/<task>__rN.log`   | the full transcript of that run |
| `out/summary.tsv`      | one row per run: task, run#, patch lines, exit code |

## Produce fine-tune-format traces (optional)

Want the per-call model traces in the **exact same SFT `input → output` format as the
shipped fine-tune data** (`experiment/results/finetune/README.md`) — minus the
evaluation fields (`resolved_full`/`resolved_f2p`/`run_status`), since this driver
generates patches, it doesn't run SWE-bench evaluation? Turn on trace capture.

The trick: `claude`'s stream output never exposes the *request* payloads
(system/messages/tools) and hides the subagent's own model calls, so traces **cannot**
be rebuilt from the CLI. They're captured at the **LiteLLM proxy** — the one place that
sees every call (Opus orchestrator **and** Qwen contractor) with full request bodies.

**1. Start the proxy with capture on.** When you run `/xeon-subagent:setup` (or the
helper by hand), set `CCX_TRACE_DIR` so it registers the trace callback:

```bash
CCX_TRACE_DIR="$PWD/out/traces" \
QWEN_ENDPOINT="http://localhost:8004/v1" PROVIDER="bedrock" AWS_REGION_NAME="us-east-1" \
AWS_BEARER_TOKEN_BEDROCK="<token>" \
bash "$(claude plugin path xeon-subagent 2>/dev/null || echo ~/.claude/plugins/.../xeon-subagent)/scripts/litellm_up.sh"
```

Point `CCX_TRACE_DIR` at **`<this dir>/out/traces`** so the driver finds the log
automatically. Without `CCX_TRACE_DIR` the proxy behaves exactly as before (no capture).

**2. Run the driver** as normal. It records a run-window per run (`out/runs.jsonl`) and,
when it finishes, reshapes the captured calls into two files:

| File | What |
|---|---|
| `out/opus_contract_traces.jsonl` | **orchestrator** (Opus) calls, FT schema |
| `out/qwen_contract_traces.jsonl` | **subagent** (Qwen contractor) calls, FT schema |

Each line matches the study schema exactly (`input.{system,messages,tools}` →
`output`, plus `model`/tokens/`finish_reason`/`turn_index`/`is_final_turn`/`run.*`),
with the eval fields omitted.

**3. (Optional) keep only completed subagent answers.** Most contractor calls end
`finish_reason=="tool_calls"` (mid-investigation stubs), same 15-turn caveat as the
study. To drop them:

```bash
./emit_traces.py --final-only     # rewrites qwen_contract_traces.jsonl, stops-only
```

## FAQ

**Q: How do I run just one task, or a quick smoke test?**
```bash
./run_ft_tasks.sh django__django-11087   # one task (still runs it x its repeat count)
REPEATS=1 ./run_ft_tasks.sh              # every task exactly once (20 runs, fast-ish)
```

**Q: Why 50 runs for 20 tasks?**
To match the fine-tune traces: 10 tasks run 3×, 10 run 2× (the per-task counts live in
`ft_tasks.jsonl`). Override with `REPEATS`.

**Q: It's very slow / seems stuck.**
Normal. Each run is a real repo + real test = several minutes. Run it detached:
`nohup ./run_ft_tasks.sh > run.log 2>&1 &` and watch `out/summary.tsv` fill up.

**Q: I got `400 The provided model identifier is invalid`.**
Your shell has `CLAUDE_CODE_USE_BEDROCK=1` (or `ANTHROPIC_API_KEY`) set, which makes
Claude Code skip the proxy. The script already unsets these for its runs, so this only
bites if you call `claude` by hand — just unset them.

**Q: How do I know the Qwen contractor actually did the work (not Opus)?**
```bash
docker logs xeon-subagent-litellm 2>&1 | grep -c qwen-guard
```
That line is logged **only** on the Qwen route. A count > 0 = the contractor ran on Qwen.

**Q: Nothing hits the proxy / `claude plugin list` doesn't show xeon-subagent.**
Make sure the plugin is installed and enabled (`/plugin install xeon-subagent@xeon-subagent`)
and that you `export`ed `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN` in the same terminal.

**Q: A run says the contractor "budget exhausted" but still fixed it.**
That's just the Qwen model being chatty; the fix still happened. Nothing to do.

**Q: Can I change the orchestrator model or per-run timeout?**
```bash
MODEL=us.anthropic.claude-opus-4-8 TASK_TIMEOUT=1800 ./run_ft_tasks.sh
```

**Q: Are these the same tasks the model was fine-tuned on?**
Yes — same 20 instance IDs, same repeat counts. Note the traces used **Opus as the
orchestrator and Qwen as the sub-contractor**, which is exactly what this runs. Benching
Qwen as a *solo* agent (no Opus) is a different setup.

**Q: I turned on `CCX_TRACE_DIR` but no `*_traces.jsonl` appeared.**
The proxy writes calls only if it was **started** with `CCX_TRACE_DIR` set — setting it
just for the driver isn't enough (the driver doesn't start the proxy). Re-run
`/xeon-subagent:setup` (or the helper) with `CCX_TRACE_DIR` exported, pointing at
`out/traces`, then re-run the driver. Check the proxy picked it up:
`docker inspect xeon-subagent-litellm --format '{{range .Config.Env}}{{println .}}{{end}}' | grep CCX_TRACE_LOG`.
The driver prints `trace capture ON` at the top when it sees the log.

**Q: Can I re-emit traces without re-running the tasks?**
Yes — the raw capture (`out/traces/calls.jsonl`) and run windows (`out/runs.jsonl`)
persist. Just re-run `./emit_traces.py` (add `--final-only` to drop the stub turns).
