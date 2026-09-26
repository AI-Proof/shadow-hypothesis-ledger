"""SHL tests. Each test uses its own temporary SQLite file; nothing touches a live ledger.

Run:  python -m pytest -q      or      python tests/test_shl.py
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shl_ledger import inject, ledger, screen, similarity, tools  # noqa: E402

SECRET = "prefers to work late at night and hates early meetings"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        os.environ["SHL_LEDGER_PATH"] = str(Path(self.tmp.name) / "shl.sqlite")
        os.environ["SHL_QUIET"] = "1"
        os.environ.pop("SHL_EVIDENCE", None)

    def tearDown(self):
        for k in ("SHL_LEDGER_PATH", "SHL_QUIET", "SHL_EVIDENCE", "SHL_GHOST_MODE", "SHL_GATE_NOTE"):
            os.environ.pop(k, None)
        self.tmp.cleanup()

    def active_ids(self):
        return {h for h, _ in ledger.compile_active_context()["active"]}

    def parked(self):
        return dict(ledger.compile_active_context()["quarantined"])


class CoreGuarantees(Base):
    """The seven guarantees of the technical preview, unchanged."""

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
        ledger.reject("DEAD", "wrong")
        block = inject.build_context("")
        self.assertIn("works in Bratislava", block)
        self.assertIn("DEAD", block)
        self.assertNotIn(SECRET, block)
        self.assertNotIn("0.99", block)
        self.assertNotIn("posterior", block.lower())

    def test_ghost_flags_without_echo(self):
        ledger.stage("DEAD", SECRET)
        ledger.reject("DEAD", "wrong")
        block = inject.build_context("my friend says I " + SECRET)
        self.assertIn("GHOST", block)
        self.assertIn("DEAD", block)
        self.assertNotIn(SECRET, block)

    def test_corroborate_never_promotes(self):
        os.environ["SHL_EVIDENCE"] = "1"
        ledger.stage("H1", SECRET, 0.9)
        ledger.reject("H1", "wrong")
        out = ledger.corroborate("H1")
        self.assertEqual(out["status"], "REINFORCED")
        self.assertNotIn("H1", self.active_ids())


class NewInV02(Base):
    def test_renamed_guess_is_refused(self):
        """A rejected guess can't come back under a new id with the same or reordered words."""
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
        ledger.reject("H1", "wrong")
        ledger.forget("H1")
        with sqlite3.connect(os.environ["SHL_LEDGER_PATH"]) as c:
            text = c.execute("SELECT insight_text FROM shl WHERE hypothesis_id='H1'").fetchone()[0]
            events = " ".join(str(r) for r in c.execute("SELECT * FROM shl_events"))
        self.assertEqual(text, "")
        self.assertNotIn(SECRET, events)
        self.assertNotIn("H1", self.parked())  # erased rows are not listed at all
        self.assertFalse(ledger.stage("H9", SECRET)["ok"])  # the hash still recognises it
        self.assertFalse(ledger.reconsent("H1", "REVIEW")["ok"])

    def test_evidence_is_opt_in(self):
        ledger.stage("H1", SECRET)
        ledger.reject("H1", "wrong")
        self.assertFalse(ledger.corroborate("H1")["ok"])
        self.assertFalse(ledger.counter("H1")["ok"])

    def test_counter_can_expire(self):
        os.environ["SHL_EVIDENCE"] = "1"
        ledger.stage("H1", SECRET, 0.4)
        ledger.reject("H1", "wrong")
        self.assertEqual(ledger.counter("H1")["status"], "EXPIRED")
        self.assertNotIn("H1", self.active_ids())

    def test_history_is_recorded_without_text(self):
        ledger.stage("H1", SECRET)
        ledger.reject("H1", "wrong")
        ledger.reconsent("H1", "REVIEW")
        actions = [e["action"] for e in ledger.history("H1")]
        self.assertEqual(actions, ["reconsent_review", "reject", "stage"])
        self.assertNotIn(SECRET, str(ledger.history()))

    def test_review_due_lists_but_never_promotes(self):
        os.environ["SHL_EVIDENCE"] = "1"
        ledger.stage("H1", SECRET, 0.5)
        ledger.reject("H1", "wrong")
        for _ in range(3):
            ledger.corroborate("H1")
        self.assertEqual([r["id"] for r in ledger.review_due()], ["H1"])
        self.assertNotIn("H1", self.active_ids())

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

    def test_parked_id_list_is_capped(self):
        for i in range(inject.MAX_PARKED_IDS + 5):
            ledger.stage(f"P{i}", f"guess {i}: the person owns exactly {i} bicycles and {i + 100} books")
            ledger.reject(f"P{i}", "no")
        self.assertIn("5 more", inject.build_context(""))

    def test_upgrades_preview_database(self):
        path = os.environ["SHL_LEDGER_PATH"]
        with sqlite3.connect(path) as c:  # the v1 table from the technical preview
            c.execute(
                """CREATE TABLE shl (hypothesis_id TEXT PRIMARY KEY, insight_text TEXT NOT NULL,
                posterior_confidence REAL, evidence_counter INTEGER DEFAULT 0, last_update_ts REAL,
                status TEXT CHECK(status IN ('ACTIVE','SHADOW','REINFORCED','EXPIRED')), rejection_metadata TEXT)"""
            )
            c.execute("INSERT INTO shl VALUES ('OLD', ?, 0.7, 0, 0, 'SHADOW', NULL)", (SECRET,))
        self.assertEqual(self.parked()["OLD"], "SHADOW")
        self.assertFalse(ledger.stage("NEW", SECRET)["ok"])
        self.assertTrue(ledger.forget("OLD")["ok"])

    def test_tool_handler_never_raises(self):
        self.assertIn("error", tools.handle({"action": "reject"}))
        self.assertIn("error", tools.handle({"action": "nonsense"}))

    def test_forget_clears_reject_reasons(self):
        ledger.stage("H1", SECRET)
        ledger.reject("H1", "I never " + SECRET)
        ledger.forget("H1")
        self.assertNotIn("late at night", str(ledger.history("H1")))

    def test_counter_counts_as_evidence(self):
        os.environ["SHL_EVIDENCE"] = "1"
        ledger.stage("H1", SECRET, 0.9)
        ledger.reject("H1", "wrong")
        ledger.counter("H1")
        ledger.counter("H1")
        with sqlite3.connect(os.environ["SHL_LEDGER_PATH"]) as c:
            n = c.execute("SELECT evidence_counter FROM shl WHERE hypothesis_id='H1'").fetchone()[0]
        self.assertEqual(n, 2)

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
