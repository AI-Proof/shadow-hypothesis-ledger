"""Shadow-Hypothesis Ledger (SHL): the store.

One rule: only ACTIVE guesses are prompt fuel. Rejected guesses are kept, but
inert, and come back only when the person re-consents.

Storage: one SQLite file per agent profile.
  $SHL_LEDGER_PATH, else $HERMES_HOME/ledger/shl.sqlite, else ./ledger/shl.sqlite

Statuses:
  ACTIVE      may enter the prompt
  SHADOW      rejected; stored, never prompt fuel
  REINFORCED  rejected, and later evidence seemed to support it; still inert
  EXPIRED     rejected, and evidence ran against it (or the person said DELETE)
  ERASED      the text is gone; only an id, a hash and the history remain

Scores (posterior_confidence, evidence_counter) are labels for the person to read.
They never rank the prompt and can never promote a row. Evidence tracking is
off unless SHL_EVIDENCE=1.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from . import similarity

SCHEMA_VERSION = 2
DECAY = 0.15          # label step down on counter-evidence (placeholder, not fitted)
BOOST = 0.05          # label step up on corroboration (placeholder, not fitted)
EXPIRE_AT = 0.30      # label floor that marks a row EXPIRED
REVIEW_EVIDENCE = 3   # parked rows with this much evidence are listed for review
REVIEW_DAYS = 90      # ... or this old since their last change
STATUSES = ("ACTIVE", "SHADOW", "REINFORCED", "EXPIRED", "ERASED")
PARKED = ("SHADOW", "REINFORCED", "EXPIRED")


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
    return os.environ.get("SHL_EVIDENCE", "").strip().lower() in {"1", "true", "yes", "on"}


def now() -> float:
    return time.time()


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


def _migrate(c: sqlite3.Connection) -> None:
    """Create or upgrade the schema. Safe to run on every connection."""
    c.execute("CREATE TABLE IF NOT EXISTS shl_meta (key TEXT PRIMARY KEY, value TEXT)")
    c.execute(
        """CREATE TABLE IF NOT EXISTS shl_events (
            ts REAL, hypothesis_id TEXT, action TEXT,
            from_status TEXT, to_status TEXT, note TEXT)"""
    )
    row = c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='shl'").fetchone()
    if row is None:
        c.execute(_TABLE.format(name="shl"))
    elif "ERASED" not in row[0] or "text_hash" not in row[0]:
        # Version 1 (technical preview) table: rebuild with the new columns and CHECK.
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
        c.execute(
            "INSERT INTO shl_events VALUES (?,?,?,?,?,?)",
            (now(), "*", "migrate", "v1", "v2", "schema upgraded"),
        )
    c.execute("INSERT OR REPLACE INTO shl_meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))


@contextmanager
def conn():
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(path), timeout=30)
    try:
        try:
            c.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            pass
        _migrate(c)
        c.commit()
        yield c
        c.commit()
    finally:
        try:
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass
        c.close()


def _say(*args) -> None:
    if not os.environ.get("SHL_QUIET"):
        print(*args)


def _event(c, hid, action, before, after, note="") -> None:
    """History of changes. Stores ids, statuses and the person's short reason for a
    rejection, never the guess text itself. `forget` clears the reasons too."""
    c.execute("INSERT INTO shl_events VALUES (?,?,?,?,?,?)", (now(), hid, action, before, after, note))


def _status(c, hid):
    row = c.execute("SELECT status FROM shl WHERE hypothesis_id=?", (hid,)).fetchone()
    return row[0] if row else None


def _matching_parked(c, text):
    """Ids of parked or erased rows that are the same guess as `text`."""
    h = similarity.text_hash(text)
    hits = []
    for hid, st, ptext, phash in c.execute(
        "SELECT hypothesis_id, status, insight_text, text_hash FROM shl WHERE status!='ACTIVE'"
    ):
        if phash == h or (ptext and similarity.same_guess(ptext, text)):
            hits.append((hid, st))
    return hits


def stage(hid, text, conf=0.7):
    """Add a guess as ACTIVE, or update an ACTIVE one.

    Refused if the id is parked, or if the text is the same guess as a parked or
    erased row under another id (so a rejected guess can't come back renamed).
    """
    hid, text = str(hid).strip(), str(text)
    with conn() as c:
        st = _status(c, hid)
        if st is not None and st != "ACTIVE":
            _say("parked", hid, st, "use reconsent REVIEW")
            return {"ok": False, "id": hid, "status": st, "error": "parked; use reconsent REVIEW"}
        clash = [(h, s) for h, s in _matching_parked(c, text) if h != hid]
        if clash:
            _event(c, hid, "stage_refused", st, st, "matches parked " + ",".join(h for h, _ in clash))
            _say("refused", hid, "matches parked", clash)
            return {
                "ok": False,
                "id": hid,
                "error": "same guess as a rejected one; use reconsent REVIEW on it instead",
                "matches": [h for h, _ in clash],
            }
        if st is None:
            c.execute(
                "INSERT INTO shl VALUES (?,?,?,?,?,?,?,?)",
                (hid, text, similarity.text_hash(text), float(conf), 0, now(), "ACTIVE", None),
            )
            _event(c, hid, "stage", None, "ACTIVE")
        else:
            c.execute(
                "UPDATE shl SET insight_text=?, text_hash=?, posterior_confidence=?, last_update_ts=? WHERE hypothesis_id=?",
                (text, similarity.text_hash(text), float(conf), now(), hid),
            )
            _event(c, hid, "update", "ACTIVE", "ACTIVE")
    _say("ACTIVE", hid)
    return {"ok": True, "id": hid, "status": "ACTIVE"}


def reject(hid, why):
    hid = str(hid).strip()
    with conn() as c:
        st = _status(c, hid)
        if st is None:
            _say("missing", hid)
            return {"ok": False, "id": hid, "error": "missing"}
        if st == "ERASED":
            return {"ok": False, "id": hid, "status": st, "error": "erased"}
        c.execute(
            "UPDATE shl SET status='SHADOW', evidence_counter=0, last_update_ts=?, rejection_metadata=? WHERE hypothesis_id=?",
            (now(), json.dumps({"why": str(why)}), hid),
        )
        _event(c, hid, "reject", st, "SHADOW", str(why)[:200])
    _say("SHADOW", hid)
    return {"ok": True, "id": hid, "status": "SHADOW"}


def corroborate(hid):
    """Evidence seemed to support a parked guess. Label only; it stays inert."""
    hid = str(hid).strip()
    if not evidence_enabled():
        return {"ok": False, "id": hid, "error": "evidence tracking is off (set SHL_EVIDENCE=1 to opt in)"}
    with conn() as c:
        row = c.execute(
            "SELECT posterior_confidence, evidence_counter, status FROM shl WHERE hypothesis_id=?", (hid,)
        ).fetchone()
        if not row:
            return {"ok": False, "id": hid, "error": "missing"}
        conf, n, st = row
        if st not in ("SHADOW", "REINFORCED"):
            return {"ok": False, "id": hid, "status": st, "error": "inert rows only; corroborate never promotes"}
        conf = min(0.99, float(conf or 0.5) + BOOST)
        n = int(n or 0) + 1
        c.execute(
            "UPDATE shl SET posterior_confidence=?, evidence_counter=?, status='REINFORCED', last_update_ts=? WHERE hypothesis_id=?",
            (conf, n, now(), hid),
        )
        _event(c, hid, "corroborate", st, "REINFORCED")
    _say("REINFORCED", hid, conf, n)
    return {"ok": True, "id": hid, "status": "REINFORCED", "posterior_confidence": conf, "evidence_counter": n}


def counter(hid):
    """Evidence ran against a parked guess. Label only; may mark it EXPIRED."""
    hid = str(hid).strip()
    if not evidence_enabled():
        return {"ok": False, "id": hid, "error": "evidence tracking is off (set SHL_EVIDENCE=1 to opt in)"}
    with conn() as c:
        row = c.execute("SELECT posterior_confidence, status FROM shl WHERE hypothesis_id=?", (hid,)).fetchone()
        if not row:
            return {"ok": False, "id": hid, "error": "missing"}
        conf, st = row
        if st not in ("SHADOW", "REINFORCED"):
            return {"ok": False, "id": hid, "status": st, "error": "inert rows only"}
        conf = float(conf or 0.5) - DECAY
        new = "EXPIRED" if conf < EXPIRE_AT else "SHADOW"
        c.execute(
            "UPDATE shl SET posterior_confidence=?, evidence_counter=evidence_counter+1, status=?, last_update_ts=? WHERE hypothesis_id=?",
            (conf, new, now(), hid),
        )
        _event(c, hid, "counter", st, new)
    _say(new, hid, conf)
    return {"ok": True, "id": hid, "status": new, "posterior_confidence": conf}


def reconsent(hid, action):
    """The person decides about a parked guess. REVIEW is the only way back to ACTIVE."""
    hid, action = str(hid).strip(), str(action).upper().strip()
    targets = {"REVIEW": "ACTIVE", "DEFER": "SHADOW", "DELETE": "EXPIRED"}
    if action not in targets:
        return {"ok": False, "error": "use REVIEW|DEFER|DELETE"}
    with conn() as c:
        st = _status(c, hid)
        if st is None:
            return {"ok": False, "id": hid, "error": "missing"}
        if st == "ERASED":
            return {"ok": False, "id": hid, "status": st, "error": "erased; the text no longer exists"}
        new = targets[action]
        if action == "DELETE":
            c.execute("UPDATE shl SET status='EXPIRED', last_update_ts=? WHERE hypothesis_id=?", (now(), hid))
        else:
            c.execute(
                "UPDATE shl SET status=?, evidence_counter=0, last_update_ts=? WHERE hypothesis_id=?",
                (new, now(), hid),
            )
        _event(c, hid, "reconsent_" + action.lower(), st, new)
    _say(action, hid)
    return {"ok": True, "id": hid, "action": action, "status": new}


def forget(hid):
    """Erase a guess's text for good. Keeps only the id, a one-way hash and the history.

    The hash lets the ledger refuse the same guess if an agent tries to stage it
    again later, without keeping what the guess said.
    """
    hid = str(hid).strip()
    with conn() as c:
        st = _status(c, hid)
        if st is None:
            return {"ok": False, "id": hid, "error": "missing"}
        c.execute(
            """UPDATE shl SET insight_text='', status='ERASED', posterior_confidence=NULL,
               evidence_counter=0, rejection_metadata=NULL, last_update_ts=? WHERE hypothesis_id=?""",
            (now(), hid),
        )
        # The person's earlier reasons may describe the guess; clear them too.
        c.execute("UPDATE shl_events SET note='' WHERE hypothesis_id=?", (hid,))
        _event(c, hid, "forget", st, "ERASED")
    _say("ERASED", hid)
    return {"ok": True, "id": hid, "status": "ERASED"}


def compile_active_context():
    """Only ACTIVE is prompt fuel. Other statuses are listed by id only."""
    with conn() as c:
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
    with conn() as c:
        return list(
            c.execute(
                "SELECT hypothesis_id, insight_text FROM shl WHERE status IN ('SHADOW','REINFORCED','EXPIRED') AND insight_text!=''"
            )
        )


def review_due():
    """Parked guesses the person may want to look at again. Read-only; never promotes.

    Nothing here reaches the prompt. The person asks for this list and decides.
    """
    cutoff = now() - REVIEW_DAYS * 86400
    with conn() as c:
        rows = list(
            c.execute(
                """SELECT hypothesis_id, status, evidence_counter, last_update_ts FROM shl
                   WHERE status IN ('SHADOW','REINFORCED') AND (evidence_counter>=? OR last_update_ts<?)""",
                (REVIEW_EVIDENCE, cutoff),
            )
        )
    return [
        {"id": h, "status": s, "evidence_counter": n, "days_since_change": round((now() - (t or now())) / 86400)}
        for h, s, n, t in rows
    ]


def history(hid=None, limit=50):
    with conn() as c:
        if hid:
            rows = c.execute(
                "SELECT ts, hypothesis_id, action, from_status, to_status, note FROM shl_events WHERE hypothesis_id=? ORDER BY ts DESC LIMIT ?",
                (str(hid), int(limit)),
            )
        else:
            rows = c.execute(
                "SELECT ts, hypothesis_id, action, from_status, to_status, note FROM shl_events ORDER BY ts DESC LIMIT ?",
                (int(limit),),
            )
        return [dict(zip(("ts", "id", "action", "from", "to", "note"), r)) for r in rows]
