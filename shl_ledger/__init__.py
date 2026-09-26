"""Shadow-Hypothesis Ledger (SHL) as a Hermes Agent plugin.

Before every model call, the pre_llm_call hook adds a short block: the guesses
about the person that are ACTIVE, and the ids (never the text) of guesses the
person rejected. Isolation therefore doesn't depend on the model remembering to
look anything up.

The ledger itself (ledger.py, inject.py, screen.py) has no Hermes dependency and
can be used from any agent framework.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from . import inject, ledger, screen, tools

__version__ = "0.2.0"
logger = logging.getLogger(__name__)


def _on_pre_llm_call(*, user_message: str = "", **kwargs):
    del kwargs
    try:
        block = inject.build_context(user_message)
        return {"context": block} if block else None
    except Exception:
        logger.warning("shl-ledger pre_llm_call failed", exc_info=True)
        return None


def _setup_cli(parser):
    sub = parser.add_subparsers(dest="shl_cmd")
    sub.add_parser("compile", help="print ACTIVE guesses and parked ids")
    p = sub.add_parser("stage", help="add an ACTIVE guess")
    p.add_argument("hypothesis_id")
    p.add_argument("text")
    p.add_argument("confidence", nargs="?", type=float, default=0.7)
    p = sub.add_parser("reject", help="park a guess (SHADOW)")
    p.add_argument("hypothesis_id")
    p.add_argument("why", nargs="*")
    p = sub.add_parser("reconsent", help="REVIEW | DEFER | DELETE")
    p.add_argument("hypothesis_id")
    p.add_argument("action")
    p = sub.add_parser("forget", help="erase a guess's text for good")
    p.add_argument("hypothesis_id")
    sub.add_parser("review", help="list parked guesses due for the person's review")
    p = sub.add_parser("history", help="show the change history")
    p.add_argument("hypothesis_id", nargs="?")
    p = sub.add_parser("screen", help="screen a text file before saving it as memory")
    p.add_argument("path")


def _cli(args):
    os.environ["SHL_QUIET"] = "1"
    cmd = getattr(args, "shl_cmd", None) or "compile"
    if cmd == "compile":
        out = ledger.compile_active_context()
    elif cmd == "stage":
        out = ledger.stage(args.hypothesis_id, args.text, args.confidence)
    elif cmd == "reject":
        out = ledger.reject(args.hypothesis_id, " ".join(args.why or []) or "rejected by the person")
    elif cmd == "reconsent":
        out = ledger.reconsent(args.hypothesis_id, args.action)
    elif cmd == "forget":
        out = ledger.forget(args.hypothesis_id)
    elif cmd == "review":
        out = {"due": ledger.review_due()}
    elif cmd == "history":
        out = {"events": ledger.history(args.hypothesis_id)}
    elif cmd == "screen":
        out = screen.screen(Path(args.path).read_text(encoding="utf-8"))
    else:
        print(tools.USAGE)
        return 2
    print(json.dumps(out, indent=2, default=str))
    return 0


def register(ctx):
    ctx.register_tool(
        name="shl",
        toolset="shl",
        schema=tools.SCHEMA,
        handler=tools.handle,
        description=tools.SCHEMA["description"],
    )
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
    ctx.register_command("shl", tools.slash, description="Shadow-Hypothesis Ledger", args_hint=tools.USAGE[7:])
    ctx.register_cli_command(
        name="shl",
        help="Shadow-Hypothesis Ledger",
        setup_fn=_setup_cli,
        handler_fn=_cli,
        description="Only ACTIVE guesses reach the prompt; rejected ones return only by reconsent.",
    )
    skill = Path(__file__).resolve().parent / "skills" / "shl-context"
    if skill.is_dir():
        try:
            ctx.register_skill("shl-context", str(skill))
        except Exception:
            logger.debug("shl-context skill registration skipped", exc_info=True)
