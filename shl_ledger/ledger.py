"""Shadow-Hypothesis Ledger (SHL): the store and the lifecycle.

One rule: only ACTIVE assumptions are prompt fuel. Rejected assumptions are kept, inert,
and come back only when the person re-consents.

Storage: one SQLite file per agent profile.
  $SHL_LEDGER_PATH, else $HERMES_HOME/ledger/shl.sqlite, else ./ledger/shl.sqlite

Statuses:
  ACTIVE      may enter the prompt
  SHADOW      rejected; stored, never prompt fuel
  REINFORCED  rejected, and the person's later messages seemed to support it; still inert
  EXPIRED     (v0.2 rows only) rejected and marked expired; still inert
  ERASED      the text is gone; only an id, a one-way hash and the change log remain

Lifecycle of a rejected assumption (v0.3):
  * On rejection it gets an evidence rule (rules.py) and keeps the confidence it had.
  * Each message from the person is checked by plain code (observe). Support moves
    the confidence up by an odds update; contrary evidence moves it down by a fixed
    step. Below expire_at the assumption is erased without telling anyone.
  * After enough support, or after a set number of days, a notice asks the person to
    REVIEW, DEFER or DELETE. Nothing ever becomes ACTIVE without the person.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Optional

from . import rules, scoring, settings, similarity

SCHEMA_VERSION = 3
STATUSES = ("ACTIVE", "SHADOW", "REINFORCED", "EXPIRED", "ERASED")
PARKED = ("SHADOW", "REINFORCED", "EXPIRED")
SCORED = ("SHADOW", "REINFORCED")
DEFAULT_CONFIDENCE = 0.7
BUSY_TIMEOUT = 5.0  # seconds to wait for a locked database; well under Hermes's 30 s hook limit
CONSENT_WINDOW = 30 * 60  # a typed consent code counts for 30 minutes after it was typed
CODE_LIFETIME = 7 * 86400  # and only within 7 days of the notice being shown
_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

# Kept for code that imported the v0.2 constants.
DECAY = 0.15
BOOST = 0.05
EXPIRE_AT = 0.30
REVIEW_EVIDENCE = 3
REVIEW_DAYS = 90

_clock: Callable[[], float] = time.time
_rule_generator: Optional[Callable[[str], Optional[dict]]] = None
_recent_user_messages: list[tuple[float, str, str]] = []


def now() -> float:
    return _clock()


def db_path() -> Path:
    override = os.environ.get("SHL_LEDGER_PATH")
    if override:
        return Path(override)
    home = os.environ.get("HERMES_HOME")
    if home:
        return Path(home) / "ledger" / "shl.sqlite"
    try:
        from hermes_constants import get_hermes_home  # type: ignore

        return Path(get_hermes_home()) / "ledger" / "shl.sqlite"
    except Exception:
        return Path.cwd() / "ledger" / "shl.sqlite"


def evidence_enabled() -> bool:
    """v0.2 name. True unless evidence_mode is off."""
    return settings.get("evidence_mode") != "off"


def set_rule_generator(fn: Optional[Callable[[str], Optional[dict]]]) -> None:
    """A host can supply a one-off model call that writes an evidence rule for an assumption."""
    global _rule_generator
    _rule_generator = fn


def note_user_message(text: str, session_id: str = "") -> None:
    """Remember the person's recent messages (in memory only), to check typed consent codes."""
    t = now()
    _recent_user_messages.append((t, str(session_id or ""), str(text or "")[:2000]))
    del _recent_user_messages[:-20]


# --------------------------------------------------------------------------- schema

_TABLE = """CREATE TABLE {name} (
    hypothesis_id TEXT PRIMARY KEY,
    insight_text TEXT NOT NULL DEFAULT '',
    text_hash TEXT,
    posterior_confidence REAL,
    evidence_counter INTEGER DEFAULT 0,
    last_update_ts REAL,
    status TEXT CHECK(status IN ('ACTIVE','SHADOW','REINFORCED','EXPIRED','ERASED')),
    rejection_metadata TEXT
)"""

_V3_COLUMNS = [
    ("counter_evidence", "INTEGER DEFAULT 0"),
    ("conf_at_reject", "REAL"),
    ("rejected_ts", "REAL"),
    ("rule_json", "TEXT"),
    ("rule_source", "TEXT"),
    ("last_hit_session", "TEXT"),
    ("next_review_ts", "REAL"),
    ("sim_confidence", "REAL"),
    ("sim_counter", "INTEGER DEFAULT 0"),
    ("sim_last_hit_session", "TEXT"),
    ("num", "INTEGER"),
    ("created_ts", "REAL"),
]

_EVENTS = """CREATE TABLE shl_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL, hypothesis_id TEXT, action TEXT,
    from_status TEXT, to_status TEXT, from_conf REAL, to_conf REAL,
    note TEXT DEFAULT '', prev_hash TEXT, hash TEXT)"""

_EVIDENCE = """CREATE TABLE IF NOT EXISTS shl_evidence (
    ts REAL, hypothesis_id TEXT, direction TEXT, phrase TEXT, excerpt TEXT,
    session_id TEXT, conf_before REAL, conf_after REAL, applied INTEGER, note TEXT)"""

_NOTICES = """CREATE TABLE IF NOT EXISTS shl_notices (
    hypothesis_id TEXT PRIMARY KEY, reason TEXT, created_ts REAL,
    shown_ts REAL, code TEXT, state TEXT, announced_ts REAL)"""

_SESSIONS = """CREATE TABLE IF NOT EXISTS shl_sessions (session_id TEXT PRIMARY KEY, ts REAL)"""


def _schema_version(c) -> int:
    try:
        row = c.execute("SELECT value FROM shl_meta WHERE key='schema_version'").fetchone()
        if row:
            return int(row[0])
    except sqlite3.Error:
        pass
    has_shl = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='shl'").fetchone()
    return 1 if has_shl else 0


def _backup(c, path: Path, version: int) -> Optional[Path]:
    """Copy the database before an upgrade. Uses SQLite's backup API (safe with WAL)."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = path.parent / "backups" / f"shl-before-v{SCHEMA_VERSION}-from-v{version}-{stamp}.sqlite"
    dest.parent.mkdir(parents=True, exist_ok=True)
    out = sqlite3.connect(str(dest))
    try:
        c.backup(out)
    finally:
        out.close()
    return dest


def _chain(prev: str, fields: list) -> str:
    payload = (prev or "") + json.dumps(fields, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _ensure(c) -> None:
    """Columns and tables added during v0.3 development: make sure they exist, whatever the
    version marker says (cheap; runs on every open)."""
    have = {r[1] for r in c.execute("PRAGMA table_info(shl)")}
    for col, decl in _V3_COLUMNS:
        if col not in have:
            c.execute(f"ALTER TABLE shl ADD COLUMN {col} {decl}")
    c.execute(_EVIDENCE)
    c.execute(_NOTICES)
    c.execute(_SESSIONS)
    have_n = {r[1] for r in c.execute("PRAGMA table_info(shl_notices)")}
    if "announced_ts" not in have_n:
        c.execute("ALTER TABLE shl_notices ADD COLUMN announced_ts REAL")


def _already_v3(c) -> bool:
    """True when the v3 migration has already run (only it creates the hash-chained log)."""
    row = c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='shl_events'").fetchone()
    return bool(row and "prev_hash" in row[0])


def _migrate(c: sqlite3.Connection, path: Optional[Path] = None) -> None:
    """Create or upgrade the schema. Safe to run on every connection."""
    version = _schema_version(c)
    if version == SCHEMA_VERSION:
        _ensure(c)
        return
    if 0 < version < SCHEMA_VERSION and _already_v3(c):
        # The data is already v3 and only the marker is old: an older SHL still running in
        # another process (for example a gateway not yet restarted) rewrote it. Don't
        # migrate twice; restore the marker.
        _ensure(c)
        c.execute("INSERT OR REPLACE INTO shl_meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
        return
    if version and path is not None and path.exists():
        _backup(c, path, version)
    c.execute("CREATE TABLE IF NOT EXISTS shl_meta (key TEXT PRIMARY KEY, value TEXT)")
    row = c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='shl'").fetchone()
    if row is None:
        c.execute(_TABLE.format(name="shl"))
    elif "ERASED" not in row[0] or "text_hash" not in row[0]:
        # v1 (technical preview): rebuild with the v2 columns and CHECK.
        c.execute(_TABLE.format(name="shl_v2"))
        c.execute(
            """INSERT INTO shl_v2 (hypothesis_id, insight_text, posterior_confidence,
               evidence_counter, last_update_ts, status, rejection_metadata)
               SELECT hypothesis_id, insight_text, posterior_confidence, evidence_counter,
                      last_update_ts, status, rejection_metadata FROM shl"""
        )
        c.execute("DROP TABLE shl")
        c.execute("ALTER TABLE shl_v2 RENAME TO shl")
        for hid, text in list(c.execute("SELECT hypothesis_id, insight_text FROM shl")):
            c.execute("UPDATE shl SET text_hash=? WHERE hypothesis_id=?", (similarity.text_hash(text), hid))
    have = {r[1] for r in c.execute("PRAGMA table_info(shl)")}
    for col, decl in _V3_COLUMNS:
        if col not in have:
            c.execute(f"ALTER TABLE shl ADD COLUMN {col} {decl}")

    # Change log: v3 is append-only and hash-chained, and never holds reasons or text.
    old_events = []
    ev = c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='shl_events'").fetchone()
    if ev is not None and "prev_hash" not in ev[0]:
        old_events = list(c.execute("SELECT ts, hypothesis_id, action, from_status, to_status FROM shl_events"))
        c.execute("DROP TABLE shl_events")
        ev = None
    if ev is None:
        c.execute(_EVENTS)
    c.execute(_EVIDENCE)
    c.execute(_NOTICES)
    c.execute(_SESSIONS)

    if version and version < 3:
        days = settings.get("timed_review_days")
        for hid, st, conf, ts in list(
            c.execute("SELECT hypothesis_id, status, posterior_confidence, last_update_ts FROM shl")
        ):
            upd = {"created_ts": ts}
            if st in PARKED:
                upd["conf_at_reject"] = conf if conf is not None else DEFAULT_CONFIDENCE
                upd["rejected_ts"] = ts
                if days:
                    upd["next_review_ts"] = (ts or now()) + days * 86400
            sets = ", ".join(f"{k}=?" for k in upd)
            c.execute(f"UPDATE shl SET {sets} WHERE hypothesis_id=?", (*upd.values(), hid))
        # v0.2 kept rejection reasons in the log; they now live on the row and are erasable.
        for hid, meta in list(c.execute("SELECT hypothesis_id, rejection_metadata FROM shl")):
            if meta and not meta.startswith("{"):
                c.execute(
                    "UPDATE shl SET rejection_metadata=? WHERE hypothesis_id=?", (json.dumps({"why": meta}), hid)
                )
        for ts, hid, action, fs, tsx in sorted(old_events, key=lambda r: r[0] or 0):
            _event(c, hid, action, fs, tsx, ts=ts)
        for (hid,) in list(
            c.execute(
                "SELECT hypothesis_id FROM shl WHERE status IN ('SHADOW','REINFORCED','EXPIRED') ORDER BY last_update_ts"
            )
        ):
            _assign_num(c, hid)
        c.execute("INSERT OR REPLACE INTO shl_meta VALUES ('upgraded_from', ?)", (f"v{version}",))
        _event(c, "*", "migrate", f"v{version}", f"v{SCHEMA_VERSION}")
    c.execute("INSERT OR REPLACE INTO shl_meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))


@contextmanager
def conn(write: bool = True):
    """A connection with the schema up to date. write=True takes the write lock at once
    (one writer at a time, so the log's hash chain can't fork); readers don't block anyone."""
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(path), timeout=BUSY_TIMEOUT)
    try:
        try:
            c.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            pass
        _migrate(c, path)
        c.commit()
        if write:
            c.execute("BEGIN IMMEDIATE")
        yield c
        c.commit()
    finally:
        try:
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass
        c.close()


def _say(*args) -> None:
    if os.environ.get("SHL_VERBOSE"):
        print(*args)


def _event(c, hid, action, before, after, fconf=None, tconf=None, note="", ts=None) -> None:
    """Append to the change log. Stores ids, statuses and scores, never assumption text or reasons.

    Each entry carries the hash of the previous one, so a changed or deleted entry
    shows up in verify_log().
    """
    row = c.execute("SELECT hash FROM shl_events ORDER BY seq DESC LIMIT 1").fetchone()
    prev = row[0] if row else ""
    ts = now() if ts is None else ts
    fconf = None if fconf is None else round(float(fconf), 4)
    tconf = None if tconf is None else round(float(tconf), 4)
    h = _chain(prev, [ts, hid, action, before, after, fconf, tconf, note])
    c.execute(
        """INSERT INTO shl_events (ts, hypothesis_id, action, from_status, to_status, from_conf, to_conf,
           note, prev_hash, hash) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (ts, hid, action, before, after, fconf, tconf, note, prev, h),
    )


def verify_log() -> dict:
    """Recompute the change log's hash chain."""
    with conn(write=False) as c:
        prev, n = "", 0
        for seq, ts, hid, action, fs, tsx, fc, tc, note, p, h in c.execute(
            """SELECT seq, ts, hypothesis_id, action, from_status, to_status, from_conf, to_conf,
               note, prev_hash, hash FROM shl_events ORDER BY seq"""
        ):
            if p != prev or _chain(prev, [ts, hid, action, fs, tsx, fc, tc, note]) != h:
                return {"ok": False, "entries": n, "broken_at": seq}
            prev, n = h, n + 1
    return {"ok": True, "entries": n}


def _row(c, hid):
    cur = c.execute("SELECT * FROM shl WHERE hypothesis_id=?", (hid,))
    r = cur.fetchone()
    if not r:
        return None
    return dict(zip([d[0] for d in cur.description], r))


def _status(c, hid):
    row = c.execute("SELECT status FROM shl WHERE hypothesis_id=?", (hid,)).fetchone()
    return row[0] if row else None


def current_ids() -> set:
    with conn(write=False) as c:
        return {r[0] for r in c.execute("SELECT hypothesis_id FROM shl")}


def status(hid) -> Optional[str]:
    with conn(write=False) as c:
        return _status(c, str(hid).strip())


def _neutral_id(c) -> str:
    while True:
        hid = "SHL_" + "".join(secrets.choice(_CODE_ALPHABET) for _ in range(6))
        if _status(c, hid) is None:
            return hid


def _is_neutral(hid: str) -> bool:
    return bool(re.match(r"^SHL_[A-Z2-9]{6}$", hid or ""))


def _rename(c, old: str, new: str) -> None:
    for table in ("shl", "shl_evidence", "shl_notices"):
        c.execute(f"UPDATE {table} SET hypothesis_id=? WHERE hypothesis_id=?", (new, old))


def _matching_parked(c, text):
    """Ids of non-ACTIVE rows (including erased ones) that are the same assumption as `text`."""
    h = similarity.text_hash(text)
    hits = []
    for hid, st, ptext, phash in c.execute(
        "SELECT hypothesis_id, status, insight_text, text_hash FROM shl WHERE status!='ACTIVE'"
    ):
        if phash == h or (ptext and similarity.same_guess(ptext, text)):
            hits.append((hid, st))
    return hits


def _erase(c, hid, action, before, conf) -> str:
    """Erase an assumption. purge_mode=hash keeps a fingerprint; full deletes the row."""
    c.execute("DELETE FROM shl_evidence WHERE hypothesis_id=?", (hid,))
    c.execute("DELETE FROM shl_notices WHERE hypothesis_id=?", (hid,))
    if settings.get("purge_mode") == "full" and action != "forget":
        c.execute("DELETE FROM shl WHERE hypothesis_id=?", (hid,))
        _event(c, hid, action, before, "PURGED", conf, None)
        return "PURGED"
    c.execute(
        """UPDATE shl SET insight_text='', status='ERASED', posterior_confidence=NULL, evidence_counter=0,
           counter_evidence=0, rejection_metadata=NULL, rule_json=NULL, rule_source=NULL,
           last_hit_session=NULL, next_review_ts=NULL, sim_confidence=NULL, sim_counter=0,
           conf_at_reject=NULL, sim_last_hit_session=NULL, last_update_ts=? WHERE hypothesis_id=?""",
        (now(), hid),
    )
    _event(c, hid, action, before, "ERASED", conf, None)
    return "ERASED"


# --------------------------------------------------------------------------- basic operations


def stage(hid, text, conf=DEFAULT_CONFIDENCE, by="agent"):
    """Add an assumption as ACTIVE, or update an ACTIVE one.

    Refused if the id is parked or erased, or if the text is the same assumption as a
    parked or erased row under another id (so a rejected assumption can't come back renamed).

    by="person" (the /shl command, the CLI, an app acting for the person) may re-add
    an assumption that matches an erased one: people change, and the fingerprint exists to
    stop the agent, not the person. It still can't bypass a parked (not erased) assumption;
    for that the person uses reconsent REVIEW.
    """
    hid, text = str(hid).strip(), str(text)
    if not _ID.match(hid) or hid.isdigit():
        return {"ok": False, "id": hid[:64], "error": "ids are 1-64 characters (letters, digits, _ . : -), not only digits"}
    if not text.strip():
        return {"ok": False, "id": hid, "error": "empty assumption"}
    try:
        conf = min(0.99, max(0.01, float(conf)))
    except (TypeError, ValueError):
        conf = DEFAULT_CONFIDENCE
    with conn() as c:
        st = _status(c, hid)
        if st is not None and st != "ACTIVE":
            _say("parked", hid, st, "use reconsent REVIEW")
            return {"ok": False, "id": hid, "status": st, "error": "parked; only the person can restore it"}
        clash = [(h, s) for h, s in _matching_parked(c, text) if h != hid]
        if by == "person":
            clash = [(h, s) for h, s in clash if s != "ERASED"]
        if clash:
            _event(c, hid, "stage_refused", st, st)
            _say("refused", hid, "matches parked", clash)
            return {
                "ok": False,
                "id": hid,
                "error": "same assumption as a rejected one; only the person can restore it",
                "matches": [h for h, _ in clash],
            }
        if st is None:
            c.execute(
                """INSERT INTO shl (hypothesis_id, insight_text, text_hash, posterior_confidence, evidence_counter,
                   last_update_ts, status, created_ts) VALUES (?,?,?,?,?,?,?,?)""",
                (hid, text, similarity.text_hash(text), conf, 0, now(), "ACTIVE", now()),
            )
            _event(c, hid, "stage" if by != "person" else "stage_by_person", None, "ACTIVE", None, conf)
        else:
            c.execute(
                "UPDATE shl SET insight_text=?, text_hash=?, posterior_confidence=?, last_update_ts=? WHERE hypothesis_id=?",
                (text, similarity.text_hash(text), conf, now(), hid),
            )
            _event(c, hid, "update", "ACTIVE", "ACTIVE", None, conf)
    _say("ACTIVE", hid)
    return {"ok": True, "id": hid, "status": "ACTIVE"}


def _build_rule(text: str, evidence_for=None, evidence_against=None) -> dict:
    """The first usable rule from the configured sources (agent, llm, auto)."""
    for source in settings.rule_sources():
        if source == "agent" and (evidence_for or evidence_against):
            r = rules.make_rule(evidence_for, evidence_against, True, "agent")
            if rules.is_valid(r):
                return r
        elif source == "llm" and _rule_generator is not None:
            try:
                r = _rule_generator(text)
            except Exception:
                r = None
            if r and rules.is_valid(r):
                r["source"] = "llm"
                return r
        elif source == "auto":
            break
    return rules.auto_rule(text)


def reject(hid, why, evidence_for=None, evidence_against=None, source_note="", context=None):
    """The person said an assumption is wrong. Park it (SHADOW) with an evidence rule.

    An assumption whose confidence was below shadow_threshold is erased at once instead;
    its fingerprint still blocks it from being added again.
    """
    hid = str(hid).strip()
    context = context or {}
    with conn(write=False) as c:
        first = _row(c, hid)
    # The rule may need a model call; never hold the database lock while it runs.
    rule = (
        _build_rule(first["insight_text"], evidence_for, evidence_against)
        if first is not None and first["status"] == "ACTIVE"
        else None
    )
    with conn() as c:
        row = _row(c, hid)
        if row is None:
            _say("missing", hid)
            return {"ok": False, "id": hid, "error": "missing"}
        st = row["status"]
        if st == "ERASED":
            return {"ok": False, "id": hid, "status": st, "error": "erased"}
        if st in PARKED:
            # Already rejected. Re-rejecting must not reset its evidence or cancel a pending review.
            return {"ok": True, "id": hid, "status": st, "note": "already rejected; nothing changed"}
        conf = row["posterior_confidence"]
        conf = DEFAULT_CONFIDENCE if conf is None else float(conf)
        was = None
        if settings.get("neutral_ids") and not _is_neutral(hid):
            was, hid = hid, _neutral_id(c)
            _rename(c, was, hid)
        if conf < settings.get("shadow_threshold"):
            new = _erase(c, hid, "reject_erase", st, conf)
            _say(new, hid)
            return {
                "ok": True,
                "id": hid,
                **({"was": was} if was else {}),
                "status": new,
                "note": "confidence was below shadow_threshold; erased, fingerprint kept"
                if new == "ERASED"
                else "confidence was below shadow_threshold; deleted",
            }
        if rule is None or row["insight_text"] != first["insight_text"]:
            rule = rules.auto_rule(row["insight_text"])
        after_no = scoring.weigh_rejection(conf, settings.get("rejection_weight"))
        meta = {
            "why": str(why or "")[:500],
            "source_note": str(source_note or "")[:300],
            "session_id": str(context.get("session_id") or ""),
            "turn_id": str(context.get("turn_id") or ""),
            "conf_after_no": round(after_no, 4),
        }
        days = settings.get("timed_review_days")
        c.execute(
            """UPDATE shl SET status='SHADOW', evidence_counter=0, counter_evidence=0, last_update_ts=?,
               rejection_metadata=?, conf_at_reject=?, posterior_confidence=?, rejected_ts=?, rule_json=?,
               rule_source=?, last_hit_session=NULL, next_review_ts=?, sim_confidence=?, sim_counter=0
               WHERE hypothesis_id=?""",
            (
                now(),
                json.dumps(meta),
                conf,
                after_no,
                now(),
                json.dumps(rule),
                rule.get("source"),
                now() + days * 86400 if days else None,
                after_no,
                hid,
            ),
        )
        c.execute("DELETE FROM shl_notices WHERE hypothesis_id=?", (hid,))
        num = _assign_num(c, hid)
        _event(c, hid, "reject", st, "SHADOW", conf, after_no, note="rule:" + str(rule.get("source")))
    _say("SHADOW", hid)
    out = {"ok": True, "id": hid, "num": num, "status": "SHADOW", "rule_source": rule.get("source")}
    if was:
        out["was"] = was
        out["note"] = f"renamed to {hid} so the id says nothing about the assumption; use {hid} from now on"
    return out


def set_rule(hid, evidence_for=None, evidence_against=None, first_person=True, auto=False):
    """The person replaces a parked assumption's evidence rule. Person-facing only."""
    hid = resolve(hid) or str(hid).strip()
    with conn() as c:
        row = _row(c, hid)
        if row is None or row["status"] not in PARKED:
            return {"ok": False, "id": hid, "error": "only parked assumptions have rules"}
        rule = (
            rules.auto_rule(row["insight_text"])
            if auto
            else rules.make_rule(evidence_for, evidence_against, first_person, "person")
        )
        if not rules.is_valid(rule):
            return {"ok": False, "id": hid, "error": "a rule needs at least one phrase"}
        c.execute(
            "UPDATE shl SET rule_json=?, rule_source=? WHERE hypothesis_id=?",
            (json.dumps(rule), rule["source"], hid),
        )
        _event(c, hid, "rule_" + rule["source"], row["status"], row["status"])
    return {"ok": True, "id": hid, "rule": rule}


def neutralize_ids() -> dict:
    """Give every rejected or erased assumption a neutral id (for ledgers from before v0.3)."""
    renamed = []
    with conn() as c:
        for (hid,) in list(
            c.execute(
                "SELECT hypothesis_id FROM shl WHERE status!='ACTIVE' "
                "AND hypothesis_id NOT IN (SELECT hypothesis_id FROM shl_notices)"
            )
        ):
            if not _is_neutral(hid):
                new = _neutral_id(c)
                _rename(c, hid, new)
                _event(c, new, "rename", None, None)
                renamed.append(new)
    return {"ok": True, "renamed": len(renamed), "note": "assumptions with an open notice keep their id until you answer"}


def auto_rules_for_missing() -> dict:
    """Give every parked assumption that has no evidence rule (e.g. after an upgrade) one built
    from its own words. Nobody reads the assumptions to do it."""
    done = []
    with conn() as c:
        for hid, text, st in list(
            c.execute(
                "SELECT hypothesis_id, insight_text, status FROM shl WHERE status IN ('SHADOW','REINFORCED') "
                "AND (rule_json IS NULL OR rule_json='') AND insight_text!=''"
            )
        ):
            rule = rules.auto_rule(text)
            if rules.is_valid(rule):
                c.execute(
                    "UPDATE shl SET rule_json=?, rule_source='auto' WHERE hypothesis_id=?", (json.dumps(rule), hid)
                )
                _event(c, hid, "rule_auto", st, st)
                done.append(hid)
    return {"ok": True, "rules_added": len(done), "ids": done}


def get_rule(hid) -> Optional[dict]:
    """Person-facing only: the rule describes the assumption, so never hand it to the model."""
    hid = resolve(hid) or str(hid).strip()
    with conn(write=False) as c:
        row = _row(c, hid)
    return rules.loads(row["rule_json"]) if row else None


# --------------------------------------------------------------------------- evidence


def _apply(c, row, direction, phrase="", excerpt="", session_id="", manual=False) -> dict:
    """One piece of evidence for or against a parked assumption."""
    hid, st = row["hypothesis_id"], row["status"]
    mode = settings.get("evidence_mode")
    log_only = mode == "log" and not manual
    col_conf = "sim_confidence" if log_only else "posterior_confidence"
    base = row[col_conf]
    if base is None:
        base = row["posterior_confidence"] if row["posterior_confidence"] is not None else DEFAULT_CONFIDENCE
    base = float(base)
    out = {"id": hid, "direction": direction, "applied": not log_only, "before": round(base, 4)}
    if direction == "for":
        after = scoring.support(base, settings.get("likelihood_ratio"))
    else:
        after = scoring.contradict(base, settings.get("decay"))
    out["after"] = round(after, 4)
    note = ""
    expire = after < settings.get("expire_at")
    if log_only:
        c.execute(
            "UPDATE shl SET sim_confidence=?, sim_counter=sim_counter+?, sim_last_hit_session=? WHERE hypothesis_id=?",
            (after, 1 if direction == "for" else 0, session_id or None, hid),
        )
        n_for = (row["sim_counter"] or 0) + (1 if direction == "for" else 0)
        if expire:
            note = "would_erase"
        elif direction == "for" and scoring.evidence_due(
            n_for, after, settings.get("review_evidence"), settings.get("notice_threshold")
        ):
            note = "would_notice"
    else:
        if direction == "for":
            new = "REINFORCED"
            c.execute(
                """UPDATE shl SET posterior_confidence=?, evidence_counter=evidence_counter+1, status=?,
                   last_update_ts=?, last_hit_session=? WHERE hypothesis_id=?""",
                (after, new, now(), session_id or None, hid),
            )
        else:
            new = st
            c.execute(
                """UPDATE shl SET posterior_confidence=?, counter_evidence=counter_evidence+1,
                   last_update_ts=?, last_hit_session=? WHERE hypothesis_id=?""",
                (after, now(), session_id or None, hid),
            )
        _event(c, hid, ("manual_" if manual else "evidence_") + direction, st, new, base, after)
    c.execute(
        "INSERT INTO shl_evidence VALUES (?,?,?,?,?,?,?,?,?,?)",
        (now(), hid, direction, phrase, excerpt, session_id, base, after, 0 if log_only else 1, note),
    )
    if expire and not log_only:
        out["erased"] = _erase(c, hid, "expire", "REINFORCED" if direction == "for" else st, after)
    if note:
        out["note"] = note
    return out


def observe(text: str, session_id: str = "", turn_id: str = "", platform: str = "") -> dict:
    """Check one message from the person against every parked assumption's rule.

    Plain code, no model call. Skips cron and subagent turns. At most one piece of
    evidence per assumption per session. Returns ids and directions only.
    """
    result = {"mode": settings.get("evidence_mode"), "hits": [], "ghosts": [], "erased": [], "queued": []}
    if result["mode"] == "off" or not (text or "").strip():
        return result
    if (platform or "").lower() in ("cron", "subagent"):
        result["skipped"] = platform
        return result
    session_key = session_id or time.strftime("day-%Y-%m-%d", time.localtime(now()))
    with conn() as c:
        cur = c.execute("SELECT * FROM shl WHERE status IN ('SHADOW','REINFORCED') AND rule_json IS NOT NULL")
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        for row in rows:
            rule = rules.loads(row["rule_json"])
            if rule is None:
                continue
            hit = rules.check(rule, text)
            if hit is None:
                continue
            if hit["direction"] == "for":
                result["ghosts"].append(row["hypothesis_id"])
            seen_col = "sim_last_hit_session" if result["mode"] == "log" else "last_hit_session"
            if row[seen_col] == session_key:
                continue
            out = _apply(c, row, hit["direction"], hit["phrase"], hit["excerpt"], session_key)
            result["hits"].append(out)
            if out.get("erased"):
                result["erased"].append(row["hypothesis_id"])
        if result["mode"] == "on":
            result["queued"] = _queue_due(c)
    return result


def corroborate(hid, session_id="manual"):
    """The person (or an app on their behalf) records support for a parked assumption by hand."""
    return _manual(hid, "for", session_id)


def counter(hid, session_id="manual"):
    """The person records evidence against a parked assumption by hand."""
    return _manual(hid, "against", session_id)


def _manual(hid, direction, session_id):
    hid = str(hid).strip()
    if not evidence_enabled():
        return {"ok": False, "id": hid, "error": "evidence is off (evidence_mode=off)"}
    with conn() as c:
        row = _row(c, hid)
        if not row:
            return {"ok": False, "id": hid, "error": "missing"}
        if row["status"] not in SCORED:
            return {"ok": False, "id": hid, "status": row["status"], "error": "parked assumptions only; evidence never promotes"}
        out = _apply(c, row, direction, "(manual)", "", session_id, manual=True)
        if settings.get("evidence_mode") == "on":
            _queue_due(c)
        after = _row(c, hid)
    status = after["status"] if after else out.get("erased", "PURGED")
    res = {"ok": True, "id": hid, "status": status, "posterior_confidence": out["after"]}
    if after:
        res["evidence_counter"] = after["evidence_counter"]
    return res


# --------------------------------------------------------------------------- reviews (what waits for the person)

# The person's words for a decision, and the older words that still work.
VERBS = {"USE": "REVIEW", "REVIEW": "REVIEW", "LATER": "DEFER", "DEFER": "DEFER", "DROP": "DELETE", "DELETE": "DELETE"}
_TYPED = re.compile(r"\b(USE|REVIEW|LATER|DEFER|DROP|DELETE)\s+(#?[A-Z0-9_.:-]+)\s+([A-Z2-9]{4})\b")


def _code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(4))


def _assign_num(c, hid) -> int:
    """A short, stable number for a parked assumption (#55). Numbers are never reused."""
    row = c.execute("SELECT num FROM shl WHERE hypothesis_id=?", (hid,)).fetchone()
    if row and row[0]:
        return int(row[0])
    got = c.execute("SELECT value FROM shl_meta WHERE key='next_num'").fetchone()
    top = c.execute("SELECT COALESCE(MAX(num), 0) FROM shl").fetchone()[0] or 0
    n = max(int(got[0]) if got else 0, int(top)) + 1
    c.execute("UPDATE shl SET num=? WHERE hypothesis_id=?", (n, hid))
    c.execute("INSERT OR REPLACE INTO shl_meta VALUES ('next_num', ?)", (str(n),))
    return n


def resolve(ref) -> Optional[str]:
    """'55', '#55' or an id -> the id, or None. A bare number is only ever a number."""
    ref = str(ref or "").strip()
    m = re.fullmatch(r"#?(\d{1,9})", ref)
    with conn(write=False) as c:
        if m:
            row = c.execute("SELECT hypothesis_id FROM shl WHERE num=?", (int(m.group(1)),)).fetchone()
            return row[0] if row else None
        return ref if ref and _status(c, ref) is not None else None


def number(hid) -> Optional[int]:
    with conn(write=False) as c:
        row = c.execute("SELECT num FROM shl WHERE hypothesis_id=?", (str(hid),)).fetchone()
    return int(row[0]) if row and row[0] else None


def _queue_due(c) -> list[str]:
    """Mark every parked assumption whose review is due as waiting for the person. Returns new ids."""
    need, thr = settings.get("review_evidence"), settings.get("notice_threshold")
    days = settings.get("timed_review_days")
    queued = []
    for hid, n, conf, nxt in list(
        c.execute(
            """SELECT hypothesis_id, evidence_counter, posterior_confidence, next_review_ts FROM shl
               WHERE status IN ('SHADOW','REINFORCED')
               AND hypothesis_id NOT IN (SELECT hypothesis_id FROM shl_notices)"""
        )
    ):
        reason = None
        if scoring.evidence_due(n or 0, conf if conf is not None else 0.0, need, thr):
            reason = "evidence"
        elif days and nxt is not None and nxt <= now():
            reason = "timed"
        if reason:
            c.execute(
                """INSERT INTO shl_notices (hypothesis_id, reason, created_ts, shown_ts, code, state, announced_ts)
                   VALUES (?,?,?,?,?,?,?)""",
                (hid, reason, now(), None, _code(), "waiting", None),
            )
            _event(c, hid, "review_due", None, None, note=reason)
            queued.append(hid)
    return queued


def _meta(row) -> dict:
    try:
        return json.loads(row.get("rejection_metadata") or "{}")
    except ValueError:
        return {}


_WAITING_SQL = """SELECT s.hypothesis_id, s.num, s.insight_text, s.status, s.posterior_confidence, s.conf_at_reject,
       s.rejection_metadata, s.evidence_counter, s.counter_evidence, s.rejected_ts, n.reason, n.code, n.created_ts
       FROM shl_notices n JOIN shl s ON s.hypothesis_id = n.hypothesis_id
       WHERE s.status IN ('SHADOW','REINFORCED') ORDER BY s.num"""


def _item(r) -> dict:
    hid, num, text, st, conf, c0, meta, sup, ag, rts, reason, code, created = r
    try:
        after_no = json.loads(meta or "{}").get("conf_after_no")
    except ValueError:
        after_no = None
    return {
        "id": hid, "num": num, "text": text, "status": st, "confidence": conf, "conf_at_reject": c0,
        "conf_after_no": after_no, "support": sup or 0, "against": ag or 0, "rejected_ts": rts,
        "reason": reason, "code": code, "since": created,
    }


def waiting(viewed: bool = False) -> list[dict]:
    """Parked assumptions waiting for the person's decision. Person-facing: includes the text.

    viewed=True (the person is looking at the list) refreshes the consent codes' lifetime.
    """
    with conn() as c:
        if settings.get("evidence_mode") == "on":
            _queue_due(c)
        rows = [_item(r) for r in c.execute(_WAITING_SQL)]
        if viewed and rows:
            c.execute("UPDATE shl_notices SET shown_ts=?", (now(),))
    return rows


def parked_list() -> list[dict]:
    """Every parked assumption, numbered. Person-facing: includes the text."""
    with conn(write=False) as c:
        wait = {r[0] for r in c.execute("SELECT hypothesis_id FROM shl_notices")}
        rows = list(
            c.execute(
                """SELECT hypothesis_id, num, insight_text, status, posterior_confidence, evidence_counter,
                   counter_evidence FROM shl WHERE status IN ('SHADOW','REINFORCED','EXPIRED') ORDER BY num, hypothesis_id"""
            )
        )
    return [
        {"id": h, "num": n, "text": t, "status": s, "confidence": p, "support": a or 0, "against": b or 0,
         "waiting": h in wait}
        for h, n, t, s, p, a, b in rows
    ]


def pending_notices() -> list[dict]:
    """What waits for the person. Ids, numbers and reasons only (safe for the agent)."""
    with conn(write=False) as c:
        return [
            {"id": h, "num": n, "reason": r}
            for h, n, r in c.execute(
                """SELECT n.hypothesis_id, s.num, n.reason FROM shl_notices n JOIN shl s
                   ON s.hypothesis_id = n.hypothesis_id ORDER BY s.num"""
            )
        ]


def session_summary(session_key: str) -> Optional[dict]:
    """What to tell the person at the start of a session. Once per session: None if already done.

    Counts only, never assumption text: this goes into the conversation.
    """
    with conn() as c:
        if c.execute("SELECT 1 FROM shl_sessions WHERE session_id=?", (session_key,)).fetchone():
            return None
        got = c.execute("SELECT value FROM shl_meta WHERE key='last_note_ts'").fetchone()
        last = float(got[0]) if got else 0.0
        mode = settings.get("evidence_mode")
        if mode == "on":
            _queue_due(c)
        rows = list(
            c.execute(
                """SELECT n.reason, n.announced_ts FROM shl_notices n JOIN shl s ON s.hypothesis_id = n.hypothesis_id
                   WHERE s.status IN ('SHADOW','REINFORCED')"""
            )
        )
        new_evidence = sum(1 for r, a in rows if a is None and r == "evidence")
        new_timed = sum(1 for r, a in rows if a is None and r == "timed")
        old = [a for r, a in rows if a is not None]
        c.execute("UPDATE shl_notices SET announced_ts=? WHERE announced_ts IS NULL", (now(),))
        purged = c.execute(
            "SELECT COUNT(DISTINCT hypothesis_id) FROM shl_events WHERE action='expire' AND ts>?", (last,)
        ).fetchone()[0]
        parked = c.execute(
            "SELECT COUNT(*) FROM shl WHERE status IN ('SHADOW','REINFORCED','EXPIRED')"
        ).fetchone()[0]
        ever = c.execute("SELECT COUNT(*) FROM shl WHERE status!='ACTIVE'").fetchone()[0]
        old_ids = sum(
            1
            for (h,) in c.execute("SELECT hypothesis_id FROM shl WHERE status IN ('SHADOW','REINFORCED','EXPIRED')")
            if not _is_neutral(h)
        )
        no_rule = c.execute(
            "SELECT COUNT(*) FROM shl WHERE status IN ('SHADOW','REINFORCED') AND (rule_json IS NULL OR rule_json='')"
        ).fetchone()[0]
        would = dict(
            c.execute(
                """SELECT note, COUNT(DISTINCT hypothesis_id) FROM shl_evidence
                   WHERE applied=0 AND note!='' AND ts>? GROUP BY note""",
                (last,),
            ).fetchall()
        )
        up = c.execute("SELECT value FROM shl_meta WHERE key='upgraded_from'").fetchone()
        if up:
            c.execute("DELETE FROM shl_meta WHERE key='upgraded_from'")
        c.execute("INSERT OR REPLACE INTO shl_sessions VALUES (?, ?)", (session_key, now()))
        c.execute(
            "DELETE FROM shl_sessions WHERE session_id NOT IN (SELECT session_id FROM shl_sessions ORDER BY ts DESC LIMIT 500)"
        )
        c.execute("INSERT OR REPLACE INTO shl_meta VALUES ('last_note_ts', ?)", (str(now()),))
    return {
        "mode": mode,
        "new_evidence": new_evidence,
        "new_timed": new_timed,
        "still_waiting": len(old),
        "still_waiting_days": round((now() - min(old)) / 86400) if old else 0,
        "purged": purged,
        "parked": parked,
        "ever": ever,
        "no_rule": no_rule,
        "old_ids": old_ids if settings.get("neutral_ids") else 0,
        "would_notice": would.get("would_notice", 0),
        "would_erase": would.get("would_erase", 0),
        "upgraded_from": up[0] if up else None,
        "log_ok": verify_log()["ok"],
        "threshold": settings.get("notice_threshold"),
        "timed_days": settings.get("timed_review_days"),
    }


def _consent_typed(hid: str, action: str, session_id: str = "") -> bool:
    """Did the person type a decision line with this assumption's code, in this session, lately?

    Accepted: "use 55 K7Q2", "later #55 K7Q2", "drop SHL_7Q2KX9 K7Q2" (or REVIEW/DEFER/DELETE).
    The code is shown only in /shl output, which goes to the person and never into the model's
    history; and only the person's own messages are recorded here (by before_model).
    """
    with conn(write=False) as c:
        row = c.execute(
            """SELECT n.code, n.shown_ts, n.created_ts, s.num FROM shl_notices n JOIN shl s
               ON s.hypothesis_id = n.hypothesis_id WHERE n.hypothesis_id=?""",
            (hid,),
        ).fetchone()
    if not row or not row[0]:
        return False
    code, shown, created, num = row
    if max(shown or 0, created or 0) < now() - CODE_LIFETIME:
        return False
    refs = {hid.upper()}
    if num:
        refs |= {str(num), f"#{num}"}
    cutoff = now() - CONSENT_WINDOW
    for ts, sid, msg in _recent_user_messages:
        if ts < cutoff or (session_id and sid and sid != session_id):
            continue
        for m in _TYPED.finditer(" ".join(str(msg).upper().split())):
            if VERBS[m.group(1)] == action and m.group(2) in refs and m.group(3) == code.upper():
                return True
    return False


def reconsent(hid, action, by="person", session_id=""):
    """The person decides about a parked assumption. REVIEW (use) is the only way back to ACTIVE.

    hid may be an id or a number ("55", "#55"). action: REVIEW|DEFER|DELETE or use|later|drop.
    by="person": a slash command, the CLI or an app acting for the person.
    by="agent":  the agent's tool. Any decision then needs the person's typed line with the
                 assumption's code, in the same session, in the last 30 minutes.
    """
    ref = str(hid).strip()
    hid = resolve(ref)
    if hid is None:
        return {"ok": False, "id": ref, "error": "not found"}
    action = VERBS.get(str(action).upper().strip(), "")
    if not action:
        return {"ok": False, "error": "use: use | later | drop (or REVIEW | DEFER | DELETE)"}
    num = number(hid)
    label = f"#{num}" if num else hid
    if by != "person" and not _consent_typed(hid, action, session_id):
        word = {"REVIEW": "use", "DEFER": "later", "DELETE": "drop"}[action]
        return {
            "ok": False,
            "id": hid,
            "num": num,
            "error": f"needs the person: ask them to run /shl {word} {num or hid}, "
            f"or to type the line with the code shown by /shl {num or hid}",
        }
    with conn() as c:
        row = _row(c, hid)
        if row is None:
            return {"ok": False, "id": ref, "error": "not found"}
        st = row["status"]
        if st == "ERASED":
            return {"ok": False, "id": hid, "status": st, "error": "erased; the text no longer exists"}
        if st == "ACTIVE" and action != "DEFER":
            return {"ok": False, "id": hid, "status": st, "error": "that assumption is in use, not parked"}
        conf = row["posterior_confidence"]
        c.execute("DELETE FROM shl_notices WHERE hypothesis_id=?", (hid,))
        if action == "DELETE":
            new = _erase(c, hid, "reconsent_delete", st, conf)
        elif action == "REVIEW":
            new = "ACTIVE"
            restored = row["conf_at_reject"] if row["conf_at_reject"] is not None else conf
            c.execute(
                """UPDATE shl SET status='ACTIVE', posterior_confidence=?, evidence_counter=0, counter_evidence=0,
                   rule_json=NULL, rule_source=NULL, next_review_ts=NULL, last_hit_session=NULL, num=NULL,
                   rejection_metadata=NULL, sim_confidence=NULL, sim_counter=0, sim_last_hit_session=NULL,
                   last_update_ts=? WHERE hypothesis_id=?""",
                (restored, now(), hid),
            )
            c.execute("DELETE FROM shl_evidence WHERE hypothesis_id=?", (hid,))
            _event(c, hid, "reconsent_review", st, new, conf, conf, note="by:" + by)
        else:
            new = "SHADOW"
            days = settings.get("timed_review_days")
            c.execute(
                """UPDATE shl SET status='SHADOW', evidence_counter=0, last_update_ts=?, next_review_ts=?,
                   rule_json=COALESCE(rule_json, ?), rule_source=COALESCE(rule_source, 'auto')
                   WHERE hypothesis_id=?""",
                (
                    now(),
                    now() + days * 86400 if days else None,
                    json.dumps(rules.auto_rule(row["insight_text"])),
                    hid,
                ),
            )
            if st == "ACTIVE":
                c.execute(
                    "UPDATE shl SET conf_at_reject=COALESCE(conf_at_reject, posterior_confidence), rejected_ts=? WHERE hypothesis_id=?",
                    (now(), hid),
                )
                if settings.get("neutral_ids") and not _is_neutral(hid):
                    new_id = _neutral_id(c)
                    _rename(c, hid, new_id)
                    hid = new_id
            num = _assign_num(c, hid)
            label = f"#{num}"
            _event(c, hid, "reconsent_defer", st, new, conf, conf, note="by:" + by)
    _say(action, hid)
    return {"ok": True, "id": hid, "num": num, "label": label, "action": action, "status": new}


def forget(hid):
    """Erase an assumption's text for good. Keeps only the id, a one-way hash and the change log.

    The hash lets the ledger refuse the same assumption if an agent tries to stage it
    again later, without keeping what the assumption said. (Always kept here, whatever
    purge_mode says: refusing the assumption again is the point of forgetting it.)
    """
    ref = str(hid).strip()
    hid = resolve(ref) or ("" if ref.lstrip("#").isdigit() else ref)
    with conn() as c:
        row = _row(c, hid) if hid else None
        if row is None:
            return {"ok": False, "id": ref, "error": "missing"}
        new = _erase(c, hid, "forget", row["status"], row["posterior_confidence"])
    _say(new, hid)
    return {"ok": True, "id": hid, "status": new}


# --------------------------------------------------------------------------- reading


def compile_active_context():
    """Only ACTIVE is prompt fuel. Other statuses are listed by id only."""
    with conn(write=False) as c:
        active = list(
            c.execute("SELECT hypothesis_id, insight_text FROM shl WHERE status='ACTIVE' ORDER BY last_update_ts")
        )
        quarantined = list(
            c.execute(
                "SELECT hypothesis_id, status FROM shl WHERE status IN ('SHADOW','REINFORCED','EXPIRED') ORDER BY last_update_ts"
            )
        )
    return {
        "db": str(db_path()),
        "active": active,
        "quarantined": quarantined,
        "active_count": len(active),
        "quarantined_count": len(quarantined),
        "note": "counts are a SQLite filter check, not an LLM leakage proof",
    }


def quarantine_strips():
    """(id, text) of parked rows, for ghost detection and screening. Never prompt fuel."""
    with conn(write=False) as c:
        return list(
            c.execute(
                "SELECT hypothesis_id, insight_text FROM shl WHERE status IN ('SHADOW','REINFORCED','EXPIRED') AND insight_text!=''"
            )
        )


def review_due():
    """Parked assumptions due for the person's review, with notices pending. Ids and numbers only."""
    need, days = settings.get("review_evidence"), settings.get("timed_review_days")
    with conn(write=False) as c:
        rows = list(
            c.execute(
                """SELECT hypothesis_id, status, evidence_counter, counter_evidence, next_review_ts, last_update_ts
                   FROM shl WHERE status IN ('SHADOW','REINFORCED')"""
            )
        )
        pending = {h for (h,) in c.execute("SELECT hypothesis_id FROM shl_notices")}
    out = []
    for h, s, n, k, nxt, t in rows:
        timed = bool(days and nxt is not None and nxt <= now())
        if (n or 0) >= need or timed or h in pending:
            out.append(
                {
                    "id": h,
                    "status": s,
                    "evidence_counter": n or 0,
                    "counter_evidence": k or 0,
                    "timed_review_due": timed,
                    "notice_pending": h in pending,
                    "days_since_change": round((now() - (t or now())) / 86400),
                }
            )
    return out


def detail(hid) -> Optional[dict]:
    """Everything about one assumption. Person-facing only (slash command, CLI): includes the text."""
    hid = resolve(hid) or str(hid).strip()
    with conn(write=False) as c:
        row = _row(c, hid)
        if not row:
            return None
        ev = [
            dict(zip(("ts", "direction", "phrase", "excerpt", "conf_before", "conf_after", "applied", "note"), r))
            for r in c.execute(
                """SELECT ts, direction, phrase, excerpt, conf_before, conf_after, applied, note
                   FROM shl_evidence WHERE hypothesis_id=? ORDER BY ts""",
                (hid,),
            )
        ]
        notice = c.execute(
            "SELECT reason, state, code FROM shl_notices WHERE hypothesis_id=?", (hid,)
        ).fetchone()
    meta: dict = {}
    if row.get("rejection_metadata"):
        try:
            meta = json.loads(row["rejection_metadata"])
        except ValueError:
            meta = {"why": row["rejection_metadata"]}
    return {
        "id": hid,
        "num": row.get("num"),
        "status": row["status"],
        "text": row["insight_text"],
        "confidence": row["posterior_confidence"],
        "conf_at_reject": row["conf_at_reject"],
        "support": row["evidence_counter"] or 0,
        "against": row["counter_evidence"] or 0,
        "rejected_ts": row["rejected_ts"],
        "next_review_ts": row["next_review_ts"],
        "rule": rules.loads(row["rule_json"]),
        "rejection": meta,
        "evidence": ev,
        "notice": dict(zip(("reason", "state", "code"), notice)) if notice else None,
        "log_mode": {"confidence": row["sim_confidence"], "support": row["sim_counter"] or 0},
    }


def history(hid=None, limit=50):
    with conn(write=False) as c:
        q = "SELECT ts, hypothesis_id, action, from_status, to_status, from_conf, to_conf FROM shl_events"
        if hid:
            rows = c.execute(q + " WHERE hypothesis_id=? ORDER BY seq DESC LIMIT ?", (str(hid), int(limit)))
        else:
            rows = c.execute(q + " ORDER BY seq DESC LIMIT ?", (int(limit),))
        return [dict(zip(("ts", "id", "action", "from", "to", "from_conf", "to_conf"), r)) for r in rows]


def stats(days: int = 14) -> dict:
    """Numbers for reports. Ids and counts only."""
    since = now() - days * 86400
    with conn(write=False) as c:
        by_status = dict(c.execute("SELECT status, COUNT(*) FROM shl GROUP BY status").fetchall())
        hits = list(
            c.execute(
                """SELECT hypothesis_id, direction, applied, note, COUNT(*) FROM shl_evidence WHERE ts>=?
                   GROUP BY hypothesis_id, direction, applied, note ORDER BY hypothesis_id""",
                (since,),
            )
        )
        erased = [
            r[0]
            for r in c.execute(
                "SELECT hypothesis_id FROM shl_events WHERE action IN ('expire','reject_erase') AND ts>=?", (since,)
            )
        ]
        shown = c.execute(
            "SELECT COUNT(*) FROM shl_events WHERE action='review_due' AND ts>=?", (since,)
        ).fetchone()[0]
        timed_waiting = c.execute(
            "SELECT COUNT(*) FROM shl WHERE status IN ('SHADOW','REINFORCED') AND next_review_ts IS NOT NULL AND next_review_ts<=?",
            (now(),),
        ).fetchone()[0]
    return {
        "days": days,
        "by_status": by_status,
        "hits": [
            {"id": h, "direction": d, "applied": bool(a), "note": n or "", "count": k} for h, d, a, n, k in hits
        ],
        "erased": erased,
        "notices_shown": shown,
        "timed_reviews_due": timed_waiting,
    }
