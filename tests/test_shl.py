"""SHL tests. Each test uses its own temporary SQLite file and a controllable clock;
nothing touches a live ledger.

Run:  python -m pytest -q      or      python tests/test_shl.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shl_ledger import hooks, inject, ledger, notices, rules, scoring, screen, settings, similarity, tools  # noqa: E402

SECRET = "prefers to work late at night and hates early meetings"
DAY = 86400.0


class Clock:
    def __init__(self, t=1_790_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        for k in list(os.environ):
            if k.startswith("SHL_"):
                os.environ.pop(k)
        os.environ["SHL_LEDGER_PATH"] = str(Path(self.tmp.name) / "shl.sqlite")
        os.environ["SHL_QUIET"] = "1"
        os.environ["SHL_NEUTRAL_IDS"] = "0"  # keep readable ids in most tests; NeutralIds tests the default
        self.clock = Clock()
        ledger._clock = self.clock
        ledger._recent_user_messages.clear()
        hooks._confirmations.clear()
        hooks._parked.clear()
        hooks._failed_sessions.clear()
        hooks._current.update(session_id="", turn_id="", platform="")
        ledger.set_rule_generator(None)
        settings.use_host(None)

    def tearDown(self):
        for k in list(os.environ):
            if k.startswith("SHL_"):
                os.environ.pop(k)
        import time

        ledger._clock = time.time
        self.tmp.cleanup()

    def active_ids(self):
        return {h for h, _ in ledger.compile_active_context()["active"]}

    def parked(self):
        return dict(ledger.compile_active_context()["quarantined"])

    def db(self):
        return sqlite3.connect(os.environ["SHL_LEDGER_PATH"])

    def park_veg(self, conf=0.7, **kw):
        ledger.stage("HYP_VEG", "Is vegetarian.", conf)
        return ledger.reject(
            "HYP_VEG", "no", evidence_for=["vegetarian", "plant-based", "no meat"], evidence_against=["steak", "chicken"], **kw
        )

    def say(self, text, session="s1", platform="cli"):
        return hooks.before_model(text, session_id=session, platform=platform)


class CoreGuarantees(Base):
    """The guarantees of the technical preview and v0.2, unchanged."""

    def test_uses_temp_db(self):
        self.assertTrue(str(ledger.db_path()).startswith(self.tmp.name))

    def test_rejected_is_stored_not_active(self):
        ledger.stage("H1", SECRET)
        ledger.reject("H1", "wrong")
        self.assertNotIn("H1", self.active_ids())
        self.assertEqual(self.parked()["H1"], "SHADOW")

    def test_stage_cannot_revive_same_id(self):
        ledger.stage("H1", SECRET)
        ledger.reject("H1", "wrong")
        out = ledger.stage("H1", "sneak back")
        self.assertFalse(out["ok"])
        self.assertNotIn("H1", self.active_ids())

    def test_only_review_promotes(self):
        ledger.stage("H1", SECRET)
        ledger.reject("H1", "wrong")
        ledger.reconsent("H1", "REVIEW")
        self.assertIn("H1", self.active_ids())
        ledger.reconsent("H1", "DEFER")
        self.assertEqual(self.parked()["H1"], "SHADOW")

    def test_inject_has_ids_not_text_or_scores(self):
        ledger.stage("KEEP", "works in Bratislava")
        ledger.stage("DEAD", SECRET, 0.99)
        ledger.reject("DEAD", "wrong", evidence_for=["night owl"])
        block = inject.build_context("")
        self.assertIn("works in Bratislava", block)
        self.assertIn("DEAD", block)
        self.assertNotIn(SECRET, block)
        self.assertNotIn("0.99", block)
        self.assertNotIn("night owl", block)  # rules never reach the prompt
        self.assertNotIn("posterior", block.lower())

    def test_ghost_flags_without_echo(self):
        ledger.stage("DEAD", SECRET)
        ledger.reject("DEAD", "wrong")
        block = inject.build_context("my friend says I " + SECRET)
        self.assertIn("GHOST", block)
        self.assertIn("DEAD", block)
        self.assertNotIn(SECRET, block)

    def test_evidence_never_promotes(self):
        self.park_veg()
        for i in range(10):
            self.say("I am vegetarian, fully plant-based.", session=f"s{i}")
        self.assertNotIn("HYP_VEG", self.active_ids())
        self.assertEqual(self.parked()["HYP_VEG"], "REINFORCED")

    def test_renamed_guess_is_refused(self):
        ledger.stage("H1", SECRET)
        ledger.reject("H1", "wrong")
        out = ledger.stage("H2", "Prefers to work late at night, and hates early meetings!")
        self.assertFalse(out["ok"])
        self.assertIn("H1", out["matches"])
        self.assertNotIn("H2", self.active_ids())

    def test_unrelated_guess_is_allowed(self):
        ledger.stage("H1", SECRET)
        ledger.reject("H1", "wrong")
        self.assertTrue(ledger.stage("H2", "is learning Portuguese for a trip to Lisbon")["ok"])

    def test_ghost_catches_reworded_turn(self):
        ledger.stage("DEAD", SECRET)
        ledger.reject("DEAD", "wrong")
        block = inject.build_context("Honestly I hate early meetings and prefer to work late at night.")
        self.assertIn("GHOST", block)
        self.assertNotIn(SECRET, block)

    def test_ghost_ignores_unrelated_turn(self):
        ledger.stage("DEAD", SECRET)
        ledger.reject("DEAD", "wrong")
        self.assertNotIn("GHOST", inject.build_context("Can you help me plan a trip to Vienna?"))

    def test_forget_erases_text_and_blocks_return(self):
        ledger.stage("H1", SECRET)
        ledger.reject("H1", "I never " + SECRET, evidence_for=["late at night"])
        ledger.forget("H1")
        with self.db() as c:
            text, rule, meta = c.execute(
                "SELECT insight_text, rule_json, rejection_metadata FROM shl WHERE hypothesis_id='H1'"
            ).fetchone()
            events = " ".join(str(r) for r in c.execute("SELECT * FROM shl_events"))
        self.assertEqual(text, "")
        self.assertIsNone(rule)
        self.assertIsNone(meta)
        self.assertNotIn("late at night", events)
        self.assertNotIn("H1", self.parked())
        self.assertFalse(ledger.stage("H9", SECRET)["ok"])  # the hash still recognises it
        self.assertFalse(ledger.reconsent("H1", "REVIEW")["ok"])

    def test_history_is_recorded_without_text(self):
        ledger.stage("H1", SECRET)
        ledger.reject("H1", "because " + SECRET)
        ledger.reconsent("H1", "REVIEW")
        actions = [e["action"] for e in ledger.history("H1")]
        self.assertEqual(actions, ["reconsent_review", "reject", "stage"])
        self.assertNotIn("late at night", str(ledger.history()))

    def test_screen_removes_parked_sentence_and_secrets(self):
        ledger.stage("DEAD", SECRET)
        ledger.reject("DEAD", "wrong")
        out = screen.screen(
            "Alex met a funder today. Alex prefers to work late at night and hates early meetings. api_key=sk-123"
        )
        self.assertIn("DEAD", out["parked_hits"])
        self.assertNotIn("late at night", out["text"])
        self.assertIn("met a funder", out["text"])
        self.assertNotIn("sk-123", out["text"])

    def test_screen_leaves_clean_text_untouched(self):
        text = "Line one.\nLine two!"
        self.assertEqual(screen.screen(text)["text"], text)

    def test_parked_id_list_is_capped(self):
        for i in range(inject.MAX_PARKED_IDS + 5):
            ledger.stage(f"P{i}", f"guess {i}: the person owns exactly {i} bicycles and {i + 100} books")
            ledger.reject(f"P{i}", "no")
        self.assertIn("5 more", inject.build_context(""))

    def test_tool_handler_never_raises(self):
        self.assertIn("error", tools.handle({"action": "reject"}))
        self.assertIn("error", tools.handle({"action": "nonsense"}))
        self.assertIn("error", tools.handle(None))

    def test_ghost_mode_inert_never_offers_restore(self):
        os.environ["SHL_GHOST_MODE"] = "inert"
        ledger.stage("DEAD", SECRET)
        ledger.reject("DEAD", "wrong")
        block = inject.build_context("I " + SECRET)
        self.assertIn("GHOST", block)
        self.assertNotIn("restore it;", block)
        self.assertIn("do not offer to restore", block)

    def test_gate_note_is_optional(self):
        self.assertNotIn("SHL note", inject.build_context(""))
        os.environ["SHL_GATE_NOTE"] = "1"
        self.assertIn("not proof", inject.build_context(""))

    def test_short_guesses_match_exactly_only(self):
        self.assertFalse(similarity.overlaps("likes tea", "she likes green tea a lot, not coffee"))
        self.assertTrue(similarity.overlaps("likes tea", "he likes tea"))


class Rules(Base):
    R = rules.make_rule(["vegetarian", "plant-based", "no meat"], ["steak", "chicken"])

    def d(self, text, rule=None):
        hit = rules.check(rule or self.R, text)
        return hit["direction"] if hit else None

    def test_support(self):
        self.assertEqual(self.d("I'm vegetarian now."), "for")
        self.assertEqual(self.d("Mostly plant based food for me."), "for")

    def test_negation_counts_against(self):
        self.assertEqual(self.d("I'm not vegetarian."), "against")
        self.assertEqual(self.d("I am no longer vegetarian"), "against")
        self.assertEqual(self.d("I don't eat plant-based stuff"), "against")
        self.assertEqual(self.d("Nie som vegetarián."), "against")

    def test_contrary_phrase(self):
        self.assertEqual(self.d("I had a great steak last night."), "against")

    def test_negated_contrary_phrase_is_nothing(self):
        self.assertIsNone(self.d("I don't eat chicken."))

    def test_someone_else_does_not_count(self):
        self.assertIsNone(self.d("My friend is vegetarian."))
        self.assertIsNone(self.d("She ordered a steak."))

    def test_first_person_with_others_counts(self):
        self.assertEqual(self.d("My wife and I are vegetarian."), "for")

    def test_slovak_first_person(self):
        r = rules.make_rule(["vegetarian"], [])
        self.assertEqual(self.d("Som vegetarián už rok.", r), "for")

    def test_first_person_can_be_turned_off(self):
        r = rules.make_rule(["vegetarian"], [], first_person=False)
        self.assertEqual(self.d("My friend is vegetarian.", r), "for")

    def test_unrelated(self):
        self.assertIsNone(self.d("Plan a trip to Vienna for me."))

    def test_contrary_wins_in_mixed_message(self):
        self.assertEqual(self.d("I'm vegetarian. Well, I had steak yesterday."), "against")

    def test_auto_rule(self):
        r = rules.auto_rule("Wants to move to Lisbon.")
        self.assertEqual(self.d("I really want to move to Lisbon next year.", r), "for")
        self.assertEqual(self.d("I don't want to move to Lisbon anymore.", r), "against")

    def test_phrases_are_cleaned_and_capped(self):
        r = rules.make_rule(["  a  ", "the", "Vegetarian", "vegetarian"] + [f"word{i}" for i in range(40)], [])
        self.assertNotIn("the", r["for"])
        self.assertEqual(sum(1 for p in r["for"] if p.lower() == "vegetarian"), 1)
        self.assertLessEqual(len(r["for"]), rules.MAX_PHRASES)

    def test_llm_reply_parsing(self):
        r = rules.parse_llm_rule('Sure: {"for": ["vegetarian"], "against": ["steak"]}')
        self.assertEqual(r["source"], "llm")
        self.assertIsNone(rules.parse_llm_rule("no json here"))


class Scoring(unittest.TestCase):
    def test_odds_update(self):
        self.assertAlmostEqual(scoring.support(0.5, 2.0), 2 / 3, places=6)
        self.assertEqual(scoring.trajectory(0.7, ["for", "for", "for"]), [0.8235, 0.9032, 0.9492])

    def test_linear_decay(self):
        self.assertEqual(scoring.trajectory(0.7, ["against"] * 3), [0.55, 0.4, 0.25])
        self.assertEqual(scoring.contradict(0.1, 0.15), 0.0)

    def test_cap(self):
        p = 0.5
        for _ in range(50):
            p = scoring.support(p, 20)
        self.assertLessEqual(p, scoring.CAP)


class Lifecycle(Base):
    def test_reject_stores_full_record(self):
        hooks.before_model("hello", session_id="S9", turn_id="T3", platform="cli")
        out = tools.handle(
            {
                "action": "reject",
                "hypothesis_id": "HYP_VEG",
                "why": "I eat meat",
                "evidence_for": ["vegetarian"],
                "evidence_against": "steak, chicken",
                "source_note": "dinner chat",
            }
        )
        self.assertIn("missing", out)  # not staged yet
        ledger.stage("HYP_VEG", "Is vegetarian.", 0.8)
        out = json.loads(tools.handle({"action": "reject", "hypothesis_id": "HYP_VEG", "why": "I eat meat",
                                        "evidence_for": ["vegetarian"], "evidence_against": "steak, chicken"}))
        self.assertEqual(out["rule_source"], "agent")
        d = ledger.detail("HYP_VEG")
        self.assertEqual(d["conf_at_reject"], 0.8)
        self.assertEqual(d["rejection"]["session_id"], "S9")
        self.assertEqual(d["rejection"]["turn_id"], "T3")
        self.assertEqual(d["rule"]["against"], ["steak", "chicken"])
        self.assertIsNotNone(d["next_review_ts"])

    def test_below_shadow_threshold_is_erased(self):
        ledger.stage("H1", "Likes jazz.", 0.5)
        out = ledger.reject("H1", "no")
        self.assertEqual(out["status"], "ERASED")
        self.assertFalse(ledger.stage("H2", "Likes jazz.")["ok"])

    def test_support_raises_and_reinforces(self):
        self.park_veg()
        self.say("I am vegetarian.")
        d = ledger.detail("HYP_VEG")
        self.assertEqual(d["status"], "REINFORCED")
        self.assertAlmostEqual(d["confidence"], 0.5385, places=3)  # 0.70, then 0.37 after the no, then support

    def test_one_hit_per_session(self):
        self.park_veg()
        for _ in range(3):
            self.say("I am vegetarian.", session="same")
        self.assertEqual(ledger.detail("HYP_VEG")["support"], 1)

    def test_cron_and_subagent_turns_are_ignored(self):
        self.park_veg()
        self.say("I am vegetarian.", platform="cron")
        self.say("I am vegetarian.", session="s2", platform="subagent")
        self.assertEqual(ledger.detail("HYP_VEG")["support"], 0)

    def test_assistant_text_is_never_scored(self):
        # Only before_model looks at text, and it is given the person's message only.
        self.park_veg()
        hooks.after_model("As a vegetarian you might like plant-based recipes.", platform="cli")
        self.assertEqual(ledger.detail("HYP_VEG")["support"], 0)

    def test_contrary_evidence_erases_silently(self):
        self.park_veg()
        for i in range(3):
            self.say("I had a steak.", session=f"s{i}")
        with self.db() as c:
            row = c.execute("SELECT status, insight_text, rule_json FROM shl WHERE hypothesis_id='HYP_VEG'").fetchone()
            ev = c.execute("SELECT COUNT(*) FROM shl_evidence WHERE hypothesis_id='HYP_VEG'").fetchone()[0]
        self.assertEqual(row, ("ERASED", "", None))
        self.assertEqual(ev, 0)
        self.assertEqual(ledger.waiting(), [])
        self.assertFalse(ledger.stage("HYP_V2", "Is vegetarian.")["ok"])

    def test_full_purge_mode(self):
        os.environ["SHL_PURGE_MODE"] = "full"
        self.park_veg()
        for i in range(3):
            self.say("I had a steak.", session=f"s{i}")
        with self.db() as c:
            self.assertIsNone(c.execute("SELECT 1 FROM shl WHERE hypothesis_id='HYP_VEG'").fetchone())
        self.assertIn("PURGED", [e["to"] for e in ledger.history("HYP_VEG")])

    def test_review_restores_the_old_confidence_and_drops_reasons(self):
        self.park_veg(conf=0.8)
        self.say("I had a burger.", session="z")  # 0.8 -> 0.5 after the no -> 0.35, above 0.30
        ledger.reconsent("HYP_VEG", "REVIEW")
        d = ledger.detail("HYP_VEG")
        self.assertEqual((d["status"], d["confidence"], d["rejection"], d["num"]), ("ACTIVE", 0.8, {}, None))

    def test_person_can_readd_an_erased_guess(self):
        self.park_veg()
        self.say("I had a steak.")  # erased
        self.assertFalse(ledger.stage("HYP_V2", "Is vegetarian.")["ok"])  # the agent can't
        self.assertIn("Added HYP_V2", tools.slash("add HYP_V2 Is vegetarian."))  # the person can
        self.assertIn("HYP_V2", self.active_ids())

    def test_person_cannot_bypass_a_parked_guess(self):
        self.park_veg()
        self.assertIn("Not added", tools.slash("add HYP_V2 Is vegetarian."))

    def test_review_restores_and_clears_rule(self):
        self.park_veg()
        ledger.reconsent("HYP_VEG", "REVIEW")
        self.assertIn("HYP_VEG", self.active_ids())
        self.assertIsNone(ledger.get_rule("HYP_VEG"))

    def test_delete_erases(self):
        self.park_veg()
        ledger.reconsent("HYP_VEG", "DELETE")
        self.assertEqual(ledger.detail("HYP_VEG")["status"], "ERASED")


class Numbers(Base):
    def test_parked_assumptions_get_stable_numbers(self):
        self.assertEqual(self.park_veg()["num"], 1)
        ledger.stage("H2", "Likes jazz.")
        self.assertEqual(ledger.reject("H2", "no")["num"], 2)
        self.assertEqual(ledger.resolve("1"), "HYP_VEG")
        self.assertEqual(ledger.resolve("#2"), "H2")
        self.assertIsNone(ledger.resolve("99"))

    def test_numbers_are_never_reused(self):
        self.park_veg()
        ledger.reconsent("1", "DELETE")
        ledger.stage("H2", "Likes jazz.")
        self.assertEqual(ledger.reject("H2", "no")["num"], 2)

    def test_a_number_never_hits_the_wrong_assumption(self):
        self.park_veg()
        ledger.reconsent("1", "use")  # #1 is ACTIVE again, its number cleared
        ledger.stage("HYP_TEA", "Likes tea.")
        self.assertIn("not found", tools.slash("drop 1"))
        self.assertIn("HYP_TEA", self.active_ids())
        self.assertFalse(ledger.forget("1")["ok"])
        self.assertIn("HYP_VEG", self.active_ids())

    def test_use_and_drop_only_act_on_parked_assumptions(self):
        ledger.stage("HYP_TEA", "Likes tea.")
        self.assertFalse(ledger.reconsent("HYP_TEA", "drop")["ok"])
        self.assertIn("HYP_TEA", self.active_ids())

    def test_restored_then_rejected_again_gets_a_new_number(self):
        self.park_veg()
        ledger.reconsent("1", "use")
        self.assertIsNone(ledger.number("HYP_VEG"))
        self.assertEqual(ledger.reject("HYP_VEG", "no again")["num"], 2)


class Reviews(Base):
    def reply(self, session, platform="cli"):
        return hooks.after_model("Answer.", platform=platform, session_id=session)

    def supported(self):
        self.park_veg()
        for s in "abc":
            self.say("I am vegetarian.", session=s)

    def test_waits_after_three_supporting_sessions(self):
        self.park_veg()
        self.say("I am vegetarian.", session="a")
        self.say("I eat plant-based.", session="b")
        self.assertEqual(ledger.waiting(), [])
        self.say("I cooked no meat this week.", session="c")
        w = ledger.waiting()
        self.assertEqual((len(w), w[0]["num"], w[0]["reason"]), (1, 1, "evidence"))
        self.assertAlmostEqual(w[0]["confidence"], 0.8235, places=3)

    def test_session_note_once_per_session_at_the_start(self):
        self.supported()
        out = self.reply("new")
        self.assertTrue(out.startswith(notices.FRAME_TOP + "\n\n"))
        self.assertIn("1 assumption you once rejected has crossed the confidence threshold (0.65)", out)
        self.assertIn('Type "/shl" to see them', out)
        self.assertIn(notices.FRAME_BOTTOM + "\n\nAnswer.", out)
        self.assertNotIn("vegetarian", out.lower())  # never the text
        self.assertIsNone(self.reply("new"))

    def test_note_when_nothing_waits(self):
        self.park_veg()
        out = self.reply("x")
        self.assertIn("1 assumption you rejected is parked in your Shadow-Hypothesis Ledger. No actions necessary.", out)
        self.assertIn('(type "/shl all" to see all SHL assumptions)', out)

    def test_note_on_an_empty_ledger(self):
        self.assertIn("You haven't rejected any assumptions yet", self.reply("x"))

    def test_note_counts_purges_since_last_session(self):
        self.park_veg()
        self.reply("s0")
        self.clock.advance(60)
        self.say("I had a steak.", session="s1")
        out = self.reply("s1")
        self.assertIn("Since your last session: 1 assumption quietly decayed and was purged.", out)
        self.assertIn("No rejected assumptions are parked right now", out)

    def test_still_waiting_reminder(self):
        self.supported()
        self.reply("n1")
        self.clock.advance(3 * DAY)
        out = self.reply("n2")
        self.assertIn("Still waiting from before: 1 assumption (first mentioned 3 days ago).", out)

    def test_note_can_be_turned_off(self):
        os.environ["SHL_SESSION_NOTE"] = "0"
        self.supported()
        self.assertIsNone(self.reply("x"))

    def test_no_note_on_cron(self):
        self.supported()
        self.assertIsNone(self.reply("x", platform="cron"))
        self.assertIsNotNone(self.reply("x", platform="telegram"))

    def test_timed_review(self):
        self.park_veg()
        self.clock.advance(89 * DAY)
        self.assertEqual(ledger.waiting(), [])
        self.clock.advance(2 * DAY)
        self.assertIn("is due for its 90-day check", self.reply("later"))

    def test_timed_review_off(self):
        os.environ["SHL_TIMED_REVIEW_DAYS"] = "0"
        self.park_veg()
        self.clock.advance(400 * DAY)
        self.assertEqual(ledger.waiting(), [])

    def test_later_resets_and_reschedules(self):
        self.supported()
        self.assertIn("Kept #1 parked at your request", tools.slash("later 1"))
        d = ledger.detail("HYP_VEG")
        self.assertEqual((d["status"], d["support"], d["notice"]), ("SHADOW", 0, None))
        self.assertGreater(d["next_review_ts"], self.clock() + 89 * DAY)

    def test_original_spec_numbers(self):
        os.environ["SHL_REJECTION_WEIGHT"] = "1"
        self.supported()
        self.assertIn("0.70 → 0.95", tools.slash(""))

    def test_trial_mode_note(self):
        os.environ["SHL_EVIDENCE_MODE"] = "log"
        self.park_veg()
        self.reply("s0")
        self.clock.advance(60)
        for s in "abcd":
            self.say("I am vegetarian.", session=s)
        out = self.reply("s1")
        self.assertIn("Trial mode: SHL is watching but changing nothing", out)
        self.assertIn("would have brought back 1 assumption", out)

    def test_evidence_off_note(self):
        os.environ["SHL_EVIDENCE_MODE"] = "off"
        self.park_veg()
        self.assertIn("evidence is off", self.reply("x"))

    def test_confirmation_after_a_rejection(self):
        ledger.stage("HYP_VEG", "Is vegetarian.")
        tools.handle({"action": "reject", "hypothesis_id": "HYP_VEG", "why": "no"}, session_id="t")
        out = self.reply("t")
        self.assertTrue(out.endswith(notices.FRAME_BOTTOM))
        self.assertIn('Parked 1 assumption you rejected (#1). It won\'t be used.\nChanged your mind? "/shl use 1"', out)

    def test_several_rejections_one_line(self):
        for hid, t in (("A", "Is vegetarian."), ("B", "Likes jazz.")):
            ledger.stage(hid, t)
            tools.handle({"action": "reject", "hypothesis_id": hid, "why": "no"}, session_id="t")
        self.assertIn("Parked 2 assumptions you rejected (#1, #2)", self.reply("t"))

    def test_weak_rejection_and_blocked_readd(self):
        ledger.stage("H1", "Likes jazz.", 0.5)
        tools.handle({"action": "reject", "hypothesis_id": "H1", "why": "no"}, session_id="t")
        tools.handle({"action": "stage", "hypothesis_id": "H2", "text": "Likes jazz."}, session_id="t")
        out = self.reply("t")
        self.assertIn("was weak", out)
        self.assertIn("tried to add back an assumption you rejected", out)

    def test_confirmations_can_be_turned_off(self):
        os.environ["SHL_CONFIRMATIONS"] = "0"
        os.environ["SHL_SESSION_NOTE"] = "0"
        ledger.stage("H", "Is vegetarian.")
        tools.handle({"action": "reject", "hypothesis_id": "H", "why": "no"}, session_id="t")
        self.assertIsNone(self.reply("t"))


class Consent(Base):
    def waiting_code(self):
        self.park_veg()
        for s in "abc":
            self.say("I am vegetarian.", session=s)
        out = tools.slash("1")
        self.assertIn("Or tell the assistant: use 1 ", out)
        return out.split("Or tell the assistant: use 1 ")[1].split()[0]

    def tool(self, action, ref="1", session="d"):
        return json.loads(
            tools.handle({"action": "reconsent", "hypothesis_id": ref, "reconsent_action": action}, session_id=session)
        )

    def test_agent_cannot_restore_on_its_own(self):
        self.park_veg()
        out = self.tool("REVIEW")
        self.assertFalse(out["ok"])
        self.assertIn("/shl use 1", out["error"])
        self.assertNotIn("HYP_VEG", self.active_ids())
        self.assertIn("The assistant tried to use #1 without you", hooks.after_model("x", session_id="d") or "")

    def test_agent_cannot_delete_or_defer_on_its_own(self):
        self.park_veg()
        self.assertFalse(self.tool("DELETE")["ok"])
        self.assertFalse(self.tool("DEFER")["ok"])

    def test_typed_line_lets_the_tool_act(self):
        code = self.waiting_code()
        self.say(f"ok, use 1 {code.lower()} please", session="d")
        self.assertTrue(self.tool("REVIEW")["ok"])
        self.assertIn("HYP_VEG", self.active_ids())
        self.assertIn("Restored #1 at your request", hooks.after_model("x", session_id="d"))

    def test_typed_later_and_old_words(self):
        code = self.waiting_code()
        self.say(f"LATER #1 {code}", session="d")
        self.assertTrue(self.tool("DEFER")["ok"])

    def test_typed_drop_with_id(self):
        code = self.waiting_code()
        self.say(f"drop HYP_VEG {code}", session="d")
        self.assertTrue(self.tool("DELETE", ref="HYP_VEG")["ok"])

    def test_wrong_code_other_session_or_old_code_fail(self):
        code = self.waiting_code()
        self.say("use 1 ZZZZ", session="d")
        self.assertFalse(self.tool("REVIEW")["ok"])
        self.say(f"use 1 {code}", session="someone-else")
        self.assertFalse(self.tool("REVIEW", session="mine")["ok"])
        self.say(f"use 1 {code}", session="e")
        self.clock.advance(31 * 60)
        self.assertFalse(self.tool("REVIEW", session="e")["ok"])

    def test_code_expires_a_week_after_it_was_last_shown(self):
        code = self.waiting_code()
        self.clock.advance(8 * DAY)
        self.say(f"use 1 {code}", session="late")
        self.assertFalse(self.tool("REVIEW", session="late")["ok"])

    def test_codes_never_reach_the_conversation(self):
        code = self.waiting_code()
        out = hooks.after_model("x", session_id="fresh")
        self.assertNotIn(code, out)

    def test_agent_history_hides_old_ids(self):
        os.environ["SHL_NEUTRAL_IDS"] = "1"
        ledger.stage("HYP_VEGETARIAN", "Is vegetarian.")
        new = ledger.reject("HYP_VEGETARIAN", "no")["id"]
        out = tools.handle({"action": "history"})
        self.assertNotIn("VEGETARIAN", out)
        self.assertIn(new, out)
        self.assertEqual(json.loads(tools.handle({"action": "history", "hypothesis_id": "HYP_VEGETARIAN"}))["events"], [])

    def test_rename_all_keeps_ids_that_are_waiting(self):
        self.waiting_code()
        os.environ["SHL_NEUTRAL_IDS"] = "1"
        self.assertIn("Gave 0 assumptions", tools.slash("rename ALL"))
        self.assertEqual(ledger.status("HYP_VEG"), "REINFORCED")

    def test_agent_cannot_forget_a_rejected_guess(self):
        self.park_veg()
        out = json.loads(tools.handle({"action": "forget", "hypothesis_id": "1"}))
        self.assertFalse(out["ok"])
        self.assertIn("/shl drop 1", out["error"])
        ledger.stage("HYP_TEA", "Likes tea.")
        self.assertTrue(json.loads(tools.handle({"action": "forget", "hypothesis_id": "HYP_TEA"}))["ok"])

    def test_re_reject_changes_nothing(self):
        code = self.waiting_code()
        before = ledger.detail("HYP_VEG")
        out = json.loads(tools.handle({"action": "reject", "hypothesis_id": "HYP_VEG", "why": "again"}))
        self.assertIn("already", out["note"])
        after = ledger.detail("HYP_VEG")
        self.assertEqual((after["support"], after["confidence"], after["notice"]["code"]),
                         (before["support"], before["confidence"], code))

    def test_slash_is_the_person(self):
        self.park_veg()
        self.assertIn("Restored #1 at your request", tools.slash("use 1"))
        self.assertIn("HYP_VEG", self.active_ids())

    def test_old_reconsent_words_still_work(self):
        self.park_veg()
        self.assertIn("Erased #1", tools.slash("reconsent 1 DELETE"))


class LogMode(Base):
    def test_log_mode_changes_nothing(self):
        os.environ["SHL_EVIDENCE_MODE"] = "log"
        self.park_veg()
        for s in "abcd":
            self.say("I am vegetarian.", session=s)
        d = ledger.detail("HYP_VEG")
        self.assertEqual((d["status"], round(d["confidence"], 4), d["support"]), ("SHADOW", 0.3684, 0))
        self.assertEqual(d["log_mode"]["support"], 4)
        self.assertEqual(ledger.waiting(), [])
        self.assertIn("would_notice", [h["note"] for h in ledger.stats()["hits"]])

    def test_log_mode_records_would_erase(self):
        os.environ["SHL_EVIDENCE_MODE"] = "log"
        self.park_veg()
        for s in "abc":
            self.say("I had a steak.", session=s)
        self.assertEqual(ledger.detail("HYP_VEG")["status"], "SHADOW")
        self.assertIn("would_erase", [h["note"] for h in ledger.stats()["hits"]])

    def test_log_mode_does_not_block_real_evidence_later(self):
        os.environ["SHL_EVIDENCE_MODE"] = "log"
        self.park_veg()
        self.say("I am vegetarian.", session="same")
        os.environ["SHL_EVIDENCE_MODE"] = "on"
        self.say("I am vegetarian.", session="same")
        self.assertEqual(ledger.detail("HYP_VEG")["support"], 1)

    def test_off_mode_ignores_messages(self):
        os.environ["SHL_EVIDENCE_MODE"] = "off"
        self.park_veg()
        self.say("I am vegetarian.")
        self.assertEqual(ledger.detail("HYP_VEG")["support"], 0)
        self.assertFalse(ledger.corroborate("HYP_VEG")["ok"])

    def test_legacy_env_still_works(self):
        os.environ["SHL_EVIDENCE"] = "0"
        self.assertEqual(settings.get("evidence_mode"), "off")


class Manual(Base):
    def test_corroborate_never_promotes(self):
        ledger.stage("H1", SECRET, 0.9)
        ledger.reject("H1", "wrong")
        out = ledger.corroborate("H1")
        self.assertEqual(out["status"], "REINFORCED")
        self.assertNotIn("H1", self.active_ids())

    def test_counter_counts_and_can_erase(self):
        os.environ["SHL_REJECTION_WEIGHT"] = "1"  # the original spec's numbers: 0.70, 0.55, 0.40, 0.25
        ledger.stage("H1", SECRET, 0.7)
        ledger.reject("H1", "wrong")
        ledger.counter("H1")
        self.assertEqual(ledger.detail("H1")["against"], 1)
        ledger.counter("H1")
        self.assertEqual(ledger.counter("H1")["status"], "ERASED")

    def test_agent_tool_has_no_manual_evidence(self):
        self.assertNotIn("corroborate", tools.ACTIONS)
        self.assertNotIn("counter", tools.ACTIONS)


class Settings(Base):
    def test_defaults_and_validation(self):
        self.assertEqual(settings.get("evidence_mode"), "on")
        os.environ["SHL_DECAY"] = "banana"
        self.assertEqual(settings.get("decay"), 0.15)
        os.environ["SHL_DECAY"] = "5"
        self.assertEqual(settings.get("decay"), 0.15)
        os.environ["SHL_DECAY"] = "0.2"
        self.assertEqual(settings.get("decay"), 0.2)

    def test_host_settings_and_env_precedence(self):
        settings.use_host(lambda k, d=None: {"ghost_mode": "inert", "session_note": False}.get(k, d))
        self.assertEqual(settings.get("ghost_mode"), "inert")
        self.assertFalse(settings.get("session_note"))
        os.environ["SHL_GHOST_MODE"] = "ask"
        self.assertEqual(settings.get("ghost_mode"), "ask")

    def test_empty_env_means_unset(self):
        os.environ["SHL_SESSION_NOTE"] = ""
        self.assertTrue(settings.get("session_note"))
        os.environ["SHL_EVIDENCE"] = "log"
        self.assertEqual(settings.get("evidence_mode"), "log")

    def test_host_errors_fall_back(self):
        def boom(k, d=None):
            raise RuntimeError("x")

        settings.use_host(boom)
        self.assertEqual(settings.get("ghost_mode"), "ask")


class RuleSources(Base):
    def test_llm_generator_used_when_enabled(self):
        os.environ["SHL_RULE_SOURCE"] = "llm,auto"
        ledger.set_rule_generator(lambda text: rules.make_rule(["veggie"], ["meat"], True, "llm"))
        ledger.stage("H", "Is vegetarian.")
        self.assertEqual(ledger.reject("H", "no")["rule_source"], "llm")

    def test_llm_failure_falls_back_to_auto(self):
        os.environ["SHL_RULE_SOURCE"] = "llm,auto"

        def bad(text):
            raise RuntimeError("no model")

        ledger.set_rule_generator(bad)
        ledger.stage("H", "Is vegetarian.")
        self.assertEqual(ledger.reject("H", "no")["rule_source"], "auto")

    def test_slow_rule_call_does_not_lock_the_ledger(self):
        import threading
        import time as _t

        self.park_veg()  # a parked guess to score while the other rejection is running
        os.environ["SHL_RULE_SOURCE"] = "llm,auto"
        ledger.set_rule_generator(lambda text: (_t.sleep(1.0), rules.make_rule(["veggie"], [], True, "llm"))[1])
        old = ledger.BUSY_TIMEOUT
        ledger.BUSY_TIMEOUT = 0.2
        try:
            ledger.stage("H2", "Likes jazz.", 0.9)
            t = threading.Thread(target=lambda: ledger.reject("H2", "no"))
            t.start()
            _t.sleep(0.2)
            out = ledger.observe("I am vegetarian.", session_id="x", platform="cli")  # must not raise "locked"
            t.join()
            self.assertEqual(len(out["hits"]), 1)
            self.assertEqual(ledger.detail("H2")["rule"]["source"], "llm")
        finally:
            ledger.BUSY_TIMEOUT = old

    def test_agent_phrases_ignored_when_not_allowed(self):
        os.environ["SHL_RULE_SOURCE"] = "auto"
        ledger.stage("H", "Is vegetarian.")
        self.assertEqual(ledger.reject("H", "no", evidence_for=["veggie"])["rule_source"], "auto")

    def test_person_edits_rule(self):
        self.park_veg()
        out = tools.slash("rule 1 for veggie, meat-free against burger")
        self.assertIn("for: veggie, meat-free", out)
        self.assertIn("against: burger", out)
        self.assertEqual(ledger.get_rule("HYP_VEG")["source"], "person")
        self.say("I ate a burger.")
        self.assertEqual(ledger.detail("HYP_VEG")["status"], "ERASED")  # 0.37 - 0.15 < 0.30


class NeutralIds(Base):
    def setUp(self):
        super().setUp()
        os.environ.pop("SHL_NEUTRAL_IDS")  # the default: on

    def test_reject_renames_to_a_neutral_id(self):
        ledger.stage("HYP_VEGETARIAN", "Is vegetarian.")
        out = json.loads(tools.handle({"action": "reject", "hypothesis_id": "HYP_VEGETARIAN", "why": "no",
                                        "evidence_for": ["vegetarian"]}))
        new = out["id"]
        self.assertRegex(new, r"^SHL_[A-Z2-9]{6}$")
        self.assertEqual(out["was"], "HYP_VEGETARIAN")
        block = inject.build_context("")
        self.assertIn(new, block)
        self.assertNotIn("VEGETARIAN", block)
        self.assertIsNone(ledger.status("HYP_VEGETARIAN"))
        self.assertFalse(ledger.stage("HYP_VEGETARIAN", "Is vegetarian.")["ok"])  # the text is still recognised
        self.say("I am vegetarian.")
        self.assertEqual(ledger.detail(new)["support"], 1)

    def test_rename_all_for_older_ledgers(self):
        os.environ["SHL_NEUTRAL_IDS"] = "0"
        ledger.stage("HYP_NIGHT", SECRET)
        ledger.reject("HYP_NIGHT", "no")
        os.environ.pop("SHL_NEUTRAL_IDS")
        self.assertIn("Gave 1 assumption a neutral id", tools.slash("rename ALL"))
        self.assertNotIn("HYP_NIGHT", inject.build_context(""))
        self.assertIn("Gave 0 assumptions", tools.slash("rename ALL"))

    def test_ids_are_validated(self):
        self.assertFalse(ledger.stage("has spaces in it", "x")["ok"])
        self.assertFalse(ledger.stage("X" * 65, "x")["ok"])
        self.assertFalse(ledger.stage("12", "x")["ok"])  # digits only would clash with numbers

    def test_later_on_an_active_assumption_gets_a_neutral_id(self):
        ledger.stage("HYP_NIGHTOWL", SECRET)
        out = ledger.reconsent("HYP_NIGHTOWL", "later")
        self.assertRegex(out["id"], r"^SHL_")
        self.assertNotIn("NIGHTOWL", inject.build_context(""))


class OtherScripts(Base):
    def test_cyrillic_guesses_are_distinct(self):
        ledger.stage("H_RU1", "Живёт в Берлине.")
        ledger.reject("H_RU1", "нет")
        self.assertTrue(ledger.stage("H_RU2", "Любит кошек.")["ok"])
        self.assertTrue(ledger.stage("H_ZH", "喜欢猫")["ok"])
        self.assertFalse(ledger.stage("H_RU3", "живёт в берлине")["ok"])

    def test_cyrillic_rule(self):
        r = rules.make_rule(["вегетарианец"], ["стейк"])
        self.assertEqual(rules.check(r, "Я вегетарианец.")["direction"], "for")
        self.assertEqual(rules.check(r, "Я не вегетарианец.")["direction"], "against")
        self.assertEqual(rules.check(r, "Вчера я ел стейк.")["direction"], "against")

    def test_negation_in_the_authors_languages(self):
        r = rules.make_rule(["vegetarian", "vegetariano", "vegetariana", "vegetarián", "vegetarianec"], [])
        for text in ("No soy vegetariano.", "Não sou vegetariano.", "Nejsem vegetarián.", "Nie som vegetarián."):
            self.assertEqual(rules.check(r, text)["direction"], "against", text)
        for text in ("Soy vegetariano.", "Eu sou vegetariano.", "Jsem vegetarián."):
            self.assertEqual(rules.check(r, text)["direction"], "for", text)
        self.assertIsNone(rules.check(r, "Mi amiga es vegetariana y ella cocina."))

    def test_symbols_only_do_not_collide(self):
        self.assertNotEqual(similarity.text_hash("!!!"), similarity.text_hash("???"))
        self.assertFalse(similarity.same_guess("!!!", "???"))

    def test_rule_parser_needs_whole_words(self):
        f, a = tools._parse_rule_args("for forest walks against indoor")
        self.assertEqual((f, a), (["forest walks"], ["indoor"]))


class AuditLog(Base):
    def test_chain_verifies_and_detects_tampering(self):
        self.park_veg()
        self.say("I am vegetarian.")
        self.assertTrue(ledger.verify_log()["ok"])
        with self.db() as c:
            c.execute("UPDATE shl_events SET to_status='ACTIVE' WHERE action='reject'")
        v = ledger.verify_log()
        self.assertFalse(v["ok"])

    def test_log_has_confidence_values(self):
        self.park_veg()
        self.say("I am vegetarian.")
        ev = [e for e in ledger.history("HYP_VEG") if e["action"] == "evidence_for"][0]
        self.assertEqual((ev["from_conf"], ev["to_conf"]), (0.3684, 0.5385))
        rej = [e for e in ledger.history("HYP_VEG") if e["action"] == "reject"][0]
        self.assertEqual((rej["from_conf"], rej["to_conf"]), (0.7, 0.3684))


class Memory(Base):
    def test_memory_write_is_screened(self):
        ledger.stage("DEAD", SECRET)
        ledger.reject("DEAD", "wrong")
        new = hooks.before_tool("memory", {"action": "add", "content": "User " + SECRET + ". Likes Vienna."})
        self.assertNotIn("late at night", json.dumps(new))
        self.assertIn("Vienna", json.dumps(new))

    def test_clean_write_and_other_tools_untouched(self):
        ledger.stage("DEAD", SECRET)
        ledger.reject("DEAD", "wrong")
        self.assertIsNone(hooks.before_tool("memory", {"content": "Likes Vienna."}))
        self.assertIsNone(hooks.before_tool("web_search", {"q": SECRET}))

    def test_screening_can_be_turned_off(self):
        os.environ["SHL_SCREEN_MEMORY"] = "0"
        ledger.stage("DEAD", SECRET)
        ledger.reject("DEAD", "wrong")
        self.assertIsNone(hooks.before_tool("memory", {"content": SECRET}))


class Migration(Base):
    def _v1(self):
        with self.db() as c:
            c.execute(
                """CREATE TABLE shl (hypothesis_id TEXT PRIMARY KEY, insight_text TEXT NOT NULL,
                posterior_confidence REAL, evidence_counter INTEGER DEFAULT 0, last_update_ts REAL,
                status TEXT CHECK(status IN ('ACTIVE','SHADOW','REINFORCED','EXPIRED')), rejection_metadata TEXT)"""
            )
            c.execute("INSERT INTO shl VALUES ('OLD', ?, 0.7, 0, ?, 'SHADOW', NULL)", (SECRET, self.clock()))

    def _v2(self):
        with self.db() as c:
            c.execute(
                """CREATE TABLE shl (hypothesis_id TEXT PRIMARY KEY, insight_text TEXT NOT NULL DEFAULT '',
                text_hash TEXT, posterior_confidence REAL, evidence_counter INTEGER DEFAULT 0, last_update_ts REAL,
                status TEXT CHECK(status IN ('ACTIVE','SHADOW','REINFORCED','EXPIRED','ERASED')), rejection_metadata TEXT)"""
            )
            c.execute("CREATE TABLE shl_meta (key TEXT PRIMARY KEY, value TEXT)")
            c.execute("INSERT INTO shl_meta VALUES ('schema_version','2')")
            c.execute("CREATE TABLE shl_events (ts REAL, hypothesis_id TEXT, action TEXT, from_status TEXT, to_status TEXT, note TEXT)")
            c.execute(
                "INSERT INTO shl VALUES ('OLD', ?, ?, 0.7, 0, ?, 'SHADOW', ?)",
                (SECRET, similarity.text_hash(SECRET), self.clock(), json.dumps({"why": "nope"})),
            )
            c.execute("INSERT INTO shl VALUES ('KEEP', 'Lives in Brno.', NULL, 0.7, 0, ?, 'ACTIVE', NULL)", (self.clock(),))
            c.execute("INSERT INTO shl_events VALUES (?, 'OLD', 'reject', 'ACTIVE', 'SHADOW', 'I never work late')", (self.clock(),))

    def test_upgrades_preview_database(self):
        self._v1()
        self.assertEqual(self.parked()["OLD"], "SHADOW")
        self.assertFalse(ledger.stage("NEW", SECRET)["ok"])
        self.assertTrue(ledger.forget("OLD")["ok"])

    def test_upgrades_v2_with_backup(self):
        self._v2()
        ctx = ledger.compile_active_context()
        self.assertEqual(ctx["active_count"], 1)
        self.assertEqual(self.parked(), {"OLD": "SHADOW"})
        backups = list((Path(self.tmp.name) / "backups").glob("shl-before-v3-from-v2-*.sqlite"))
        self.assertEqual(len(backups), 1)
        d = ledger.detail("OLD")
        self.assertEqual(d["conf_at_reject"], 0.7)
        self.assertIsNotNone(d["next_review_ts"])
        self.assertEqual(d["rejection"]["why"], "nope")
        # old reasons are gone from the log, the log is chained, actions kept
        self.assertNotIn("work late", str(ledger.history()))
        self.assertTrue(ledger.verify_log()["ok"])
        self.assertIn("reject", [e["action"] for e in ledger.history("OLD")])
        # migrated rows have no rule until one is set, and are simply not scored
        self.say("I work late at night.")
        self.assertEqual(ledger.detail("OLD")["support"], 0)
        self.assertTrue(ledger.set_rule("OLD", auto=True)["ok"])

    def test_rules_for_all_migrated_guesses(self):
        self._v2()
        self.assertIn("Added evidence rules to 1 assumption", tools.slash("rule ALL auto"))
        self.assertIn("to 0 assumptions", tools.slash("rule ALL auto"))
        self.say("I work late at night these days.")
        self.assertEqual(ledger.detail("OLD")["support"], 1)

    def test_migration_runs_once(self):
        self._v2()
        ledger.compile_active_context()
        ledger.compile_active_context()
        self.assertEqual(len(list((Path(self.tmp.name) / "backups").glob("*.sqlite"))), 1)

    def test_old_process_rewriting_the_marker_does_not_migrate_twice(self):
        # A v0.2 gateway still running after the upgrade rewrites schema_version=2 on every open.
        self._v2()
        ledger.compile_active_context()
        num = ledger.detail("OLD")["num"]
        with self.db() as c:
            c.execute("INSERT OR REPLACE INTO shl_meta VALUES ('schema_version','2')")
        ledger.compile_active_context()
        self.assertEqual(len(list((Path(self.tmp.name) / "backups").glob("*.sqlite"))), 1)
        self.assertEqual(ledger.detail("OLD")["num"], num)
        self.assertEqual([e["action"] for e in ledger.history()].count("migrate"), 1)
        self.assertTrue(ledger.verify_log()["ok"])
        with self.db() as c:
            self.assertEqual(c.execute("SELECT value FROM shl_meta WHERE key='schema_version'").fetchone()[0], "3")


class SlashAndReport(Base):
    def test_everything_is_framed(self):
        self.park_veg()
        for cmd in ("", "1", "all", "active", "help", "help all", "report", "verify", "settings", "history", "nonsense"):
            out = tools.slash(cmd)
            self.assertTrue(out.startswith(notices.FRAME_TOP + "\n\n"), cmd)
            self.assertTrue(out.endswith("\n\n" + notices.FRAME_BOTTOM), cmd)

    def test_frame_is_symmetrical(self):
        top, bottom = notices.FRAME_TOP, notices.FRAME_BOTTOM
        self.assertEqual(len(top), len(bottom))
        self.assertEqual(top[:4], top[-4:][::-1])  # ░▒▓█ ... █▓▒░
        self.assertEqual(bottom, bottom[::-1])  # a palindrome: same number of blocks on each side

    def test_main_view(self):
        self.park_veg()
        out = tools.slash("")
        self.assertIn("1 assumption you rejected is parked", out)
        for s in "abc":
            self.say("I am vegetarian.", session=s)
        out = tools.slash("")
        self.assertIn("WAITING FOR YOU", out)
        self.assertIn(' 1. "Is vegetarian."', out)
        self.assertIn("0.37 → 0.82 · 3 supporting mentions", out)
        self.assertIn("/shl use 1     use it again", out)
        self.assertIn(tools.slash(""), tools.slash("review"))

    def test_help(self):
        self.assertIn("EVERYDAY", tools.slash("help"))
        self.assertNotIn("MORE", tools.slash("help"))
        self.assertIn("MORE", tools.slash("help all"))

    def test_detail_shows_text_to_person(self):
        self.park_veg()
        self.say("I am vegetarian.")
        out = tools.slash("1")
        self.assertIn('#1  "Is vegetarian."', out)
        self.assertIn("0.37 → 0.54", out)
        self.assertIn("0.70 before your no, 0.37 after it, 0.54 now", out)
        self.assertEqual(out, tools.slash("review 1"))

    def test_all_view(self):
        self.park_veg()
        ledger.stage("H2", "Likes jazz.")
        ledger.reject("H2", "no")
        out = tools.slash("all")
        self.assertIn("PARKED (2)", out)
        self.assertIn(' 2. "Likes jazz."', out)

    def test_decision_words(self):
        self.park_veg()
        self.assertIn("Erased #1 at your request", tools.slash("drop 1"))
        self.assertIn("Which one?", tools.slash("use"))

    def test_agent_tool_never_returns_parked_text(self):
        self.park_veg()
        for s in "abc":
            self.say("I am vegetarian.", session=s)
        for action in ("compile", "review", "notices", "history"):
            self.assertNotIn("vegetarian", tools.handle({"action": action}).lower())

    def test_check_is_a_dry_run(self):
        self.park_veg()
        out = tools.slash("check I'm vegetarian these days")
        self.assertIn("#1: for", out)
        self.assertEqual(ledger.detail("HYP_VEG")["support"], 0)

    def test_report_has_no_text_unless_full(self):
        self.park_veg()
        self.say("I am vegetarian.")
        rep = tools.report_text()
        self.assertIn("#1", rep)
        self.assertNotIn("Is vegetarian", rep)
        self.assertIn("Is vegetarian", tools.report_text(full=True))

    def test_slash_never_raises(self):
        for cmd in ("rule", "review NOPE", "reconsent X MAYBE", "use 99", "later", "1 2 3", "#", "rule 99 for x"):
            self.assertIsInstance(tools.slash(cmd), str)


class Upgrades(Base):
    def test_an_early_v3_database_gets_the_new_columns(self):
        with self.db() as c:  # a database from an earlier v0.3 build: marked v3, without num or shl_sessions
            c.execute(ledger._TABLE.format(name="shl"))
            cols = [col for col in ledger._V3_COLUMNS if col[0] != "num"]
            for col, decl in cols:
                c.execute(f"ALTER TABLE shl ADD COLUMN {col} {decl}")
            c.execute("CREATE TABLE shl_meta (key TEXT PRIMARY KEY, value TEXT)")
            c.execute("INSERT INTO shl_meta VALUES ('schema_version','3')")
            c.execute(ledger._EVENTS)
            c.execute("""CREATE TABLE shl_notices (hypothesis_id TEXT PRIMARY KEY, reason TEXT, created_ts REAL,
                         shown_ts REAL, code TEXT, state TEXT)""")
        self.assertEqual(self.park_veg()["status"], "SHADOW")
        self.assertIn("1 assumption you rejected is parked", tools.slash(""))
        self.assertIsNotNone(hooks.after_model("x", session_id="s"))


class FailSafe(Base):
    def test_hooks_fail_safe(self):
        blocker = Path(self.tmp.name) / "a-file"
        blocker.write_text("x")
        os.environ["SHL_LEDGER_PATH"] = str(blocker / "sub" / "shl.sqlite")  # a file can't be a folder, on any OS
        self.assertIsNone(hooks.before_model("I am vegetarian", platform="cli"))
        out = hooks.after_model("x", platform="cli", session_id="broken")
        self.assertIn("couldn't read its ledger", out)
        self.assertIsNone(hooks.after_model("x", platform="cli", session_id="broken"))
        self.assertIsNone(hooks.before_tool("memory", {"content": "x"}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
