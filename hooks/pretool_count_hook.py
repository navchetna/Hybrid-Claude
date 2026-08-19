#!/usr/bin/env python3
"""PreToolUse counting/denying hook — 'force the emit BEFORE the cap'.

Ported from cc-xeon experiment/hooks/contract_merged/pretool_count_hook.py
(validated 2026-06-25: usable-return 5/5 vs 0/17 without it). Behaviour is
identical; the ONLY changes make it portable outside the experiment harness:

  1. AGENT MATCH IS A SUBSTRING. Claude Code namespaces a plugin's agents as
     "<plugin>:contractor" (e.g. "xeon-subagent:contractor"). We match when the
     configured TARGET ("contractor") is a SUBSTRING of the payload's agent_type,
     so the cap fires regardless of the namespace prefix CC prepends.
  2. PORTABLE PATHS. Counters + log default under CLAUDE_PLUGIN_DATA (per-plugin
     persistent dir CC provides), falling back to a temp dir. No /ccx-out, no
     /tmp/.fp. Logging is best-effort and silent on failure.

Mechanism (unchanged): a PreToolUse hook fires for subagent tool calls; the stdin
payload carries agent_type + agent_id + tool_name. We count + deny ONLY the
contractor's investigation calls (Read/Grep/Glob/Bash), keyed per-delegation by
agent_id so a re-delegation gets a fresh budget. The orchestrator's own calls
(agent_type absent / not matching) ALWAYS pass, so its apply-phase Read/Edit/Bash
is never touched. The deny is a MODEL-VISIBLE permissionDecision:"deny" whose
reason text pairs with the agent's DENIAL PROTOCOL: "stop and EMIT the citation
map" instead of retrying.

Env (all optional):
  XEON_HOOK_N       deny threshold (default 12)
  XEON_HOOK_AGENT   target subagent substring (default "contractor")
  XEON_HOOK_DIR     dir for per-agent counter files (default <data>/counters)
  XEON_HOOK_LOG     log path (default <data>/hook.log); set empty to disable
"""
import os
import sys
import json
import tempfile


def _data_dir() -> str:
    d = os.environ.get("CLAUDE_PLUGIN_DATA")
    if d:
        return d
    return os.path.join(tempfile.gettempdir(), "xeon-subagent")


N = int(os.environ.get("XEON_HOOK_N", "12"))
TARGET = os.environ.get("XEON_HOOK_AGENT", "contractor")
CDIR = os.environ.get("XEON_HOOK_DIR") or os.path.join(_data_dir(), "counters")
LOG = os.environ.get("XEON_HOOK_LOG", os.path.join(_data_dir(), "hook.log"))
INVESTIGATE = {"Read", "Grep", "Glob", "Bash"}

DENY_MSG = (
    "INVESTIGATION BUDGET EXHAUSTED ({n} tool calls used). Tools are now DISABLED — "
    "every further Read/Grep/Glob/Bash call WILL be denied, so retrying one is wasted. "
    "Your ONLY valid next action is to STOP calling tools and write a PLAIN-TEXT message "
    "containing your final COMPACT CITATION MAP (ROOT CAUSE / EDITS: path + AT line-range "
    "+ concrete CHANGE / TEST) from what you have ALREADY gathered. Mark any uncertain "
    "site with a trailing '?'. A partial map is useful; an empty message is the worst "
    "possible outcome. Emit the map now."
)


def log(msg: str) -> None:
    if not LOG:
        return
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def main() -> None:
    raw = sys.stdin.read()
    try:
        ev = json.loads(raw)
    except Exception as e:
        log(f"PARSE-ERR {e}; head={raw[:300]!r}")
        sys.exit(0)

    tool = ev.get("tool_name") or "?"
    atype = ev.get("agent_type")
    aid = ev.get("agent_id") or "noid"

    # only the contractor subagent is budgeted; orchestrator + other agents pass freely.
    # substring match survives CC's plugin namespacing ("xeon-subagent:contractor").
    if not atype or TARGET not in atype:
        log(f"PASS-NONTARGET tool={tool} agent_type={atype}")
        sys.exit(0)
    if tool not in INVESTIGATE:
        log(f"PASS-NONTOOL tool={tool} agent_type={atype}")
        sys.exit(0)

    try:
        os.makedirs(CDIR, exist_ok=True)
    except Exception:
        pass
    cfile = os.path.join(CDIR, f"count_{aid}")
    try:
        c = int(open(cfile).read().strip()) if os.path.exists(cfile) else 0
    except Exception:
        c = 0
    c += 1
    try:
        open(cfile, "w").write(str(c))
    except Exception:
        pass

    if c < N:
        log(f"PASS tool={tool} count={c}/{N} agent_id={aid}")
        sys.exit(0)

    log(f"DENY tool={tool} count={c}/{N} agent_id={aid} -> force emit")
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": DENY_MSG.format(n=N),
        }
    }
    print(json.dumps(out))
    sys.exit(0)


if __name__ == "__main__":
    main()
