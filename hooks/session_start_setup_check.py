#!/usr/bin/env python3
"""SessionStart hook — one-time setup nudge for the xeon-subagent plugin.

Makes setup "ask once": if the plugin has not been configured (no config marker
written by /xeon-subagent:setup), emit additionalContext asking Claude to offer
to run setup. Once the marker exists, stay completely silent.

Hooks cannot run interactive prompts, so we inject context rather than asking
directly; the model then offers /xeon-subagent:setup to the user. Silent and
best-effort: any error exits 0 with no output so a session never breaks.
"""
import os
import sys
import json
import tempfile


def data_dir() -> str:
    d = os.environ.get("CLAUDE_PLUGIN_DATA")
    if d:
        return d
    return os.path.join(tempfile.gettempdir(), "xeon-subagent")


def main() -> None:
    # drain stdin (SessionStart payload) but we don't need it
    try:
        sys.stdin.read()
    except Exception:
        pass

    marker = os.path.join(data_dir(), "config.json")
    configured = False
    try:
        if os.path.exists(marker):
            with open(marker) as f:
                configured = bool(json.load(f).get("configured"))
    except Exception:
        configured = False

    if configured:
        sys.exit(0)  # already set up — say nothing

    msg = (
        "The `xeon-subagent` plugin is installed but not yet configured. It routes a "
        "free local Qwen `contractor` subagent through a LiteLLM proxy. Before the user "
        "runs `/xeon-subagent:contract`, offer to run `/xeon-subagent:setup` once to set "
        "the LiteLLM base URL (`ANTHROPIC_BASE_URL`). Mention this only if the user's "
        "request involves fixing a bug or using this plugin; otherwise stay quiet."
    )
    out = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": msg,
        }
    }
    print(json.dumps(out))
    sys.exit(0)


if __name__ == "__main__":
    main()
