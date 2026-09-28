"""The Hermes plugin wrapper, called the way Hermes calls it.

Hook arguments follow what Hermes 0.21.5 passes (checked in its source by the
author's Hermes agent, 27 Sep 2026):
  pre_llm_call:          session_id, task_id, turn_id, user_message, conversation_history,
                         is_first_turn, model, platform, parent_session_id, sender_id
  transform_llm_output:  response_text, session_id, model, platform, turn_id
The manifest must declare every tool and hook that register() registers, or
`hermes plugins validate` fails.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import shl_ledger  # noqa: E402
from shl_ledger import hooks, ledger, notices, settings  # noqa: E402


class FakeLLM:
    def __init__(self):
        self.calls = 0

    def complete(self, messages, purpose=""):
        self.calls += 1
        return '{"for": ["vegetarian", "veggie"], "against": ["steak"]}'


class FakeCtx:
    def __init__(self, config=None, with_section=True):
        self.tools, self.hooks, self.commands, self.cli, self.skills, self.sections = {}, {}, {}, {}, {}, {}
        self.config = config or {}
        self.llm = FakeLLM()
        if not with_section:
            self.register_system_prompt_section = None

    def register_tool(self, name, toolset, schema, handler, check_fn=None, requires_env=None, is_async=False,
                      description="", emoji="", override=False):
        self.tools[name] = handler

    def register_hook(self, name, fn):
        self.hooks.setdefault(name, []).append(fn)

    def register_command(self, name, handler, description="", args_hint="", argument_mode=None):
        self.commands[name] = (handler, args_hint)

    def register_cli_command(self, name, help, setup_fn, handler_fn, description=""):
        self.cli[name] = (setup_fn, handler_fn)

    def register_skill(self, name, path, description="", frontmatter=None):
        assert Path(path).is_file(), "Hermes wants the SKILL.md file itself"
        self.skills[name] = path

    def register_system_prompt_section(self, id, content, *, position="after_memory", max_chars=4000):
        assert position == "after_memory" and 0 < max_chars <= 4000 and len(content) <= max_chars
        self.sections[id] = content

    def get_config(self, key, default=None):
        return self.config.get(key, default)


def pre_llm(ctx, msg, session="S1", platform="cli"):
    return ctx.hooks["pre_llm_call"][0](
        session_id=session, task_id="T", turn_id="U1", user_message=msg, conversation_history=[],
        is_first_turn=False, model="m", platform=platform, parent_session_id="", sender_id="",
    )


def transform(ctx, text, platform="cli", session="S1"):
    return ctx.hooks["transform_llm_output"][0](
        response_text=text, session_id=session, model="m", platform=platform, turn_id="U1"
    )


class Contract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        for k in list(os.environ):
            if k.startswith("SHL_"):
                os.environ.pop(k)
        os.environ["SHL_LEDGER_PATH"] = str(Path(self.tmp.name) / "shl.sqlite")
        os.environ["SHL_QUIET"] = "1"
        os.environ["SHL_NEUTRAL_IDS"] = "0"
        self.ctx = FakeCtx()
        shl_ledger.register(self.ctx)

    def tearDown(self):
        hooks._confirmations.clear()
        hooks._parked.clear()
        settings.use_host(None)
        ledger.set_rule_generator(None)
        shl_ledger._state["rules_in_system"] = False
        for k in list(os.environ):
            if k.startswith("SHL_"):
                os.environ.pop(k)
        self.tmp.cleanup()

    def test_manifest_matches_registrations(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("pyyaml not installed")

        man = yaml.safe_load((ROOT / "shl_ledger" / "plugin.yaml").read_text(encoding="utf-8"))
        self.assertEqual(set(man["provides_tools"]), set(self.ctx.tools))
        self.assertEqual(set(man["provides_hooks"]), set(self.ctx.hooks))
        self.assertEqual(man["version"], shl_ledger.__version__)
        self.assertEqual(set(man["config_schema"]), set(settings.SPEC))
        types = {"bool": "bool", "choice": "str", "str": "str", "int": "int", "float": "float"}
        for key, spec in man["config_schema"].items():
            default, kind, rule = settings.SPEC[key]
            self.assertEqual(spec["type"], types[kind], key)
            self.assertEqual(spec["default"], default, key)
            if kind == "choice":
                self.assertEqual(tuple(spec["choices"]), rule, key)
        for field in ("name", "version", "description"):
            self.assertTrue(man.get(field))

    def test_telegram_safe_args_hint(self):
        self.assertFalse(self.ctx.commands["shl"][1].startswith("<"))

    def test_rules_go_to_system_section(self):
        self.assertIn("shl-rules", self.ctx.sections)
        out = pre_llm(self.ctx, "hello")
        self.assertIn("rules in the system prompt", out["context"])

    def test_without_system_section_rules_stay_in_block(self):
        shl_ledger._state["rules_in_system"] = False
        ctx = FakeCtx(with_section=False)
        shl_ledger.register(ctx)
        self.assertIn("Only the person can restore one", pre_llm(ctx, "hello")["context"])

    def test_full_turn_cycle(self):
        tool = self.ctx.tools["shl"]
        tool({"action": "stage", "hypothesis_id": "HYP_VEG", "text": "Is vegetarian."})
        pre_llm(self.ctx, "No, I'm not vegetarian.")
        out = json.loads(tool({"action": "reject", "hypothesis_id": "HYP_VEG", "why": "not true",
                               "evidence_for": ["vegetarian", "plant-based"], "evidence_against": ["steak"]},
                              session_id="S1"))
        self.assertEqual(out["status"], "SHADOW")
        first = transform(self.ctx, "Noted.")
        self.assertTrue(first.startswith(notices.FRAME_TOP))  # the session note
        self.assertIn("Parked 1 assumption you rejected (#1)", first)  # the confirmation at the end
        self.assertTrue(first.endswith(notices.FRAME_BOTTOM))
        for s in ("a", "b", "c"):
            ctx_block = pre_llm(self.ctx, "I have been vegetarian lately.", session=s)["context"]
            self.assertNotIn("Is vegetarian", ctx_block)
        reply = transform(self.ctx, "Answer.", session="d")
        self.assertTrue(reply.startswith(notices.FRAME_TOP))
        self.assertIn("crossed the confidence threshold", reply)
        self.assertNotIn("vegetarian", reply.lower())
        self.assertIsNone(transform(self.ctx, "Answer 2.", session="d"))
        self.assertIsNone(transform(self.ctx, "Answer 3.", platform="cron", session="e"))
        self.assertIn("WAITING FOR YOU", self.ctx.commands["shl"][0](""))

    def test_settings_come_from_hermes_config(self):
        shl_ledger._state["rules_in_system"] = False
        ctx = FakeCtx(config={"ghost_mode": "inert", "evidence_mode": False})
        shl_ledger.register(ctx)
        self.assertEqual(settings.get("ghost_mode"), "inert")
        self.assertEqual(settings.get("evidence_mode"), "off")

    def test_llm_rule_when_enabled(self):
        os.environ["SHL_RULE_SOURCE"] = "llm,auto"
        tool = self.ctx.tools["shl"]
        tool({"action": "stage", "hypothesis_id": "H", "text": "Is vegetarian."})
        out = json.loads(tool({"action": "reject", "hypothesis_id": "H", "why": "no"}))
        self.assertEqual(out["rule_source"], "llm")
        self.assertEqual(self.ctx.llm.calls, 1)

    def test_pre_tool_call_screens_memory(self):
        tool = self.ctx.tools["shl"]
        tool({"action": "stage", "hypothesis_id": "H", "text": "prefers to work late at night and hates early meetings"})
        tool({"action": "reject", "hypothesis_id": "H", "why": "no"})
        hook = self.ctx.hooks["pre_tool_call"][0]
        res = hook(tool_name="memory", args={"content": "He prefers to work late at night and hates early meetings."})
        self.assertEqual(res["action"], "modify")
        self.assertIn("[parked: H]", res["args"]["content"])
        self.assertIsNone(hook(tool_name="memory", args={"content": "Likes tea."}))
        self.assertIsNone(hook(tool_name="shl", args={"text": "anything"}))
        self.assertIsNone(hook(tool_name="terminal", args={"cmd": "ls"}))
        # old_text must stay byte-identical: Hermes uses it to find the entry to replace
        res = hook(tool_name="memory", args={"action": "replace", "old_text": "He prefers to work late at night and hates early meetings.",
                                             "content": "Likes tea."})
        self.assertIsNone(res)
        res = hook(tool_name="memory", args={"action": "batch", "operations": [
            {"action": "add", "content": "He prefers to work late at night and hates early meetings."}]})
        self.assertIn("[parked: H]", res["args"]["operations"][0]["content"])
        self.assertIsNone(hook(tool_name="memory", args="not a dict"))

    def test_slash_and_cli(self):
        handler, _ = self.ctx.commands["shl"]
        self.assertIn("SHL", handler(""))
        import argparse

        setup, run = self.ctx.cli["shl"]
        p = argparse.ArgumentParser()
        setup(p)
        self.assertEqual(run(p.parse_args(["report"])), 0)

    def test_cli_survives_a_console_that_cannot_encode_the_frame(self):
        # Windows with output redirected: stdout is cp1252 and can't encode the frame.
        import argparse
        import contextlib
        import io

        raw = io.BytesIO()
        narrow = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
        setup, run = self.ctx.cli["shl"]
        p = argparse.ArgumentParser()
        setup(p)
        with contextlib.redirect_stdout(narrow):
            self.assertEqual(run(p.parse_args(["verify"])), 0)
        narrow.flush()
        self.assertIn("░▒▓█ ᯽ SHL ᯽ █▓▒░", raw.getvalue().decode("utf-8"))

    def test_multimodal_user_message(self):
        tool = self.ctx.tools["shl"]
        tool({"action": "stage", "hypothesis_id": "H", "text": "Is vegetarian."})
        tool({"action": "reject", "hypothesis_id": "H", "why": "no", "evidence_for": ["vegetarian"]})
        pre_llm(self.ctx, [{"type": "text", "text": "I am vegetarian."}, {"type": "image_url", "image_url": {}}])
        self.assertEqual(ledger.detail("H")["support"], 1)

    def test_skill_registered(self):
        self.assertIn("shl-context", self.ctx.skills)


if __name__ == "__main__":
    unittest.main(verbosity=2)
