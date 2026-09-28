"""Shadow-Hypothesis Ledger (SHL) as a Hermes Agent plugin.

What the plugin registers:
  pre_llm_call          checks the person's message against parked assumptions (plain code,
                        no model call), then adds the SHL GATE block: ACTIVE assumptions as
                        text, rejected assumptions by id only
  transform_llm_output  frames the reply with SHL's parts: the session note at the start of
                        a session, short confirmations at the end of a turn
  pre_tool_call         screens memory writes so a rejected assumption isn't saved as memory
  tool `shl`            for the agent (never returns parked text)
  /shl, `hermes shl`    for the person
  skill shl-context     optional reference, loaded on request

The ledger itself (ledger, rules, scoring, inject, screen, hooks) has no Hermes
dependency and can be used from any agent framework.
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from . import hooks, inject, ledger, rules, screen, settings, tools

__version__ = "0.3.0"
__all__ = ["register", "hooks", "inject", "ledger", "rules", "screen", "settings", "tools", "__version__"]
logger = logging.getLogger(__name__)

_state = {"rules_in_system": False}


def _as_text(message) -> str:
    """The person's message as text. Hermes may pass a list of content parts (text, images)."""
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        parts = []
        for p in message:
            if isinstance(p, str):
                parts.append(p)
            elif isinstance(p, dict) and isinstance(p.get("text"), str):
                parts.append(p["text"])
        return "\n".join(parts)
    return "" if message is None else str(message)


def _on_pre_llm_call(*, user_message="", session_id: str = "", turn_id: str = "", platform: str = "", **kwargs):
    del kwargs  # conversation_history is ignored on purpose: only the person's own message counts
    block = hooks.before_model(
        _as_text(user_message),
        session_id=session_id,
        turn_id=turn_id,
        platform=platform,
        rules_in_system=_state["rules_in_system"],
    )
    return {"context": block} if block else None


def _on_transform_llm_output(*, response_text: str = "", platform: str = "", session_id: str = "", **kwargs):
    del kwargs
    return hooks.after_model(response_text, platform=platform, session_id=session_id)


def _on_pre_tool_call(**kwargs):
    # Hermes treats pre_tool_call as a policy hook: if it raises or hangs, the tool is blocked.
    # So: return at once for every tool that isn't a memory write, and never raise.
    try:
        name = str(kwargs.get("tool_name") or kwargs.get("name") or "")
        if not name or name == "shl" or name not in settings.memory_tools():
            return None
        args = kwargs.get("args")
        if not isinstance(args, dict):
            return None
        new = hooks.before_tool(name, args)
        if new is None:
            return None
        return {"action": "modify", "args": new}
    except Exception:
        logger.warning("shl: pre_tool_call failed; memory write left unscreened", exc_info=True)
        return None


def _setup_cli(parser):
    parser.add_argument("words", nargs=argparse.REMAINDER, help="same subcommands as /shl (try: hermes shl help)")


def _print(text: str) -> None:
    """print(), but the SHL frame survives a console or pipe that can't encode it
    (for example cp1252 on Windows when the output is redirected)."""
    try:
        print(text)
    except UnicodeEncodeError:
        import sys

        sys.stdout.flush()
        sys.stdout.buffer.write((text + "\n").encode("utf-8"))
        sys.stdout.buffer.flush()


def _cli(args):
    words = list(getattr(args, "words", None) or [])
    if words and words[0] == "screen" and len(words) > 1:
        import json

        _print(json.dumps(screen.screen(Path(words[1]).read_text(encoding="utf-8")), indent=2))
        return 0
    _print(tools.slash(" ".join(words)))
    return 0


def _llm_rule_generator(ctx):
    def generate(guess_text: str):
        # Only called when rule_source includes "llm". ctx.llm is read here, not at load time.
        prompt = rules.LLM_PROMPT.format(guess=guess_text.replace('"', "'"), languages="English")
        resp = ctx.llm.complete(messages=[{"role": "user", "content": prompt}], purpose="shl evidence rule")
        text = resp if isinstance(resp, str) else (getattr(resp, "text", None) or str(resp))
        return rules.parse_llm_rule(text)

    return generate


def register(ctx):
    if hasattr(ctx, "get_config"):
        settings.use_host(lambda key, default=None: ctx.get_config(key, default))
    # Used only when rule_source includes "llm"; any failure falls back to an automatic rule.
    ledger.set_rule_generator(_llm_rule_generator(ctx))

    ctx.register_tool(
        name="shl",
        toolset="shl",
        schema=tools.SCHEMA,
        handler=tools.handle,
        description=tools.SCHEMA["description"],
    )

    # The fixed rules go into a stable system-prompt section when the host has one,
    # so the per-turn block stays short and the prompt cache stays warm.
    if hasattr(ctx, "register_system_prompt_section"):
        try:
            ctx.register_system_prompt_section("shl-rules", inject.RULES_TEXT, position="after_memory", max_chars=1200)
            _state["rules_in_system"] = True
        except Exception:
            logger.debug("shl: system prompt section not registered; rules stay in the gate block", exc_info=True)

    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
    ctx.register_hook("transform_llm_output", _on_transform_llm_output)
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)

    # args_hint must not start with "<", or Telegram hides the command from its menu.
    ctx.register_command("shl", tools.slash, description="Shadow-Hypothesis Ledger", args_hint="subcommand")
    ctx.register_cli_command(
        name="shl",
        help="Shadow-Hypothesis Ledger",
        setup_fn=_setup_cli,
        handler_fn=_cli,
        description="Only ACTIVE assumptions reach the prompt; rejected ones return only when the person says so.",
    )
    skill = Path(__file__).resolve().parent / "skills" / "shl-context" / "SKILL.md"
    if skill.is_file() and hasattr(ctx, "register_skill"):
        try:
            ctx.register_skill("shl-context", skill, description="How SHL treats assumptions about the person")
        except Exception:
            logger.debug("shl-context skill registration skipped", exc_info=True)
