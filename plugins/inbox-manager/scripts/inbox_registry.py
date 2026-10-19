#!/usr/bin/env python3
"""Private mailbox registry and observed scheduler lifecycle for Inbox Manager.

This stdlib-only rail records identities and receipts.  It never calls a mail
provider, scheduler, or notification service.
"""

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys


DB = ".inbox-manager-registry.sqlite3"
EMAIL = re.compile(r"^[^\s@<>;,]+@[^\s@<>;,]+\.[^\s@<>;,]+$")
RESULTS = {"success", "failed", "incomplete", "skipped"}
SCHEDULER_KINDS = {"heartbeat", "cron"}
READBACK_MAX_AGE_SECONDS = 300
SECRET_WORDS = {"password", "secret", "token", "credential", "access_token", "refresh_token", "api_key"}


class RegistryError(ValueError):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details

    def as_dict(self):
        return {"code": self.code, "message": self.message, "details": self.details}


def utcnow():
    return datetime.now(timezone.utc)


def stamp():
    return utcnow().isoformat()


def parse_time(value, field):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed
    except (AttributeError, TypeError, ValueError):
        raise RegistryError("invalid_timestamp", field + " must include a timezone", field=field) from None


def current_readback(value, field):
    parsed = parse_time(value, field)
    age = (utcnow() - parsed).total_seconds()
    if age < -5 or age > READBACK_MAX_AGE_SECONDS:
        raise RegistryError("stale_readback", field + " is not a current observation", field=field,
                            max_age_seconds=READBACK_MAX_AGE_SECONDS)
    return parsed


def clean(value, field, maximum=2000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise RegistryError("invalid_field", "invalid " + field, field=field)
    return value.strip()


def mailbox(value, field="mailbox"):
    value = clean(value, field, 320).lower()
    if not EMAIL.fullmatch(value):
        raise RegistryError("invalid_mailbox", "use a bare mailbox address", field=field)
    return value


def integer(value, low, high, field):
    if type(value) is not int or not low <= value <= high:
        raise RegistryError("invalid_field", f"{field} must be between {low} and {high}", field=field)
    return value


def approval(payload):
    if payload.get("owner_confirmed") is not True:
        raise RegistryError("owner_approval_required", "authenticated owner approval required")
    return clean(payload.get("approval_ref"), "approval_ref")


def reject_secrets(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in SECRET_WORDS:
                raise RegistryError("credentials_forbidden", "credentials must not be stored", field=str(key))
            reject_secrets(child)
    elif isinstance(value, list):
        for child in value:
            reject_secrets(child)


def digest(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()


@contextmanager
def transaction(root):
    root = Path(root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    path = root / DB
    if path.is_symlink():
        raise RegistryError("unsafe_registry", "registry database must not be a symlink")
    db = sqlite3.connect(path, timeout=15, isolation_level=None)
    db.row_factory = sqlite3.Row
    try:
        path.chmod(0o600)
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        db.executescript("""
        CREATE TABLE IF NOT EXISTS registrations (
          id TEXT PRIMARY KEY, mailbox TEXT NOT NULL UNIQUE COLLATE NOCASE,
          home TEXT NOT NULL UNIQUE, provider TEXT NOT NULL, account_id TEXT NOT NULL,
          connector_ref TEXT NOT NULL, card_ref TEXT NOT NULL, identity_hash TEXT NOT NULL,
          engine_version INTEGER NOT NULL, provider_verified_at TEXT NOT NULL,
          provider_evidence_ref TEXT NOT NULL, approval_ref TEXT NOT NULL,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS aliases (
          alias TEXT PRIMARY KEY COLLATE NOCASE,
          registration_id TEXT NOT NULL REFERENCES registrations(id)
        );
        CREATE TABLE IF NOT EXISTS schedules (
          registration_id TEXT PRIMARY KEY REFERENCES registrations(id),
          binding_id TEXT NOT NULL UNIQUE, binding_hash TEXT NOT NULL,
          task_id TEXT NOT NULL, cadence TEXT NOT NULL, timezone TEXT NOT NULL,
          execution_location TEXT NOT NULL, host_required INTEGER NOT NULL,
          host_enabled INTEGER NOT NULL, locally_paused INTEGER NOT NULL DEFAULT 0,
          verified_at TEXT NOT NULL, evidence_ref TEXT NOT NULL,
          next_run_at TEXT, next_run_verified INTEGER NOT NULL,
          grace_seconds INTEGER NOT NULL, engine_version INTEGER NOT NULL,
          engine_card_ref TEXT NOT NULL, identity_hash TEXT NOT NULL,
          approval_ref TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          schedule_kind TEXT, target_thread_id TEXT, target_archived INTEGER,
          target_verified_at TEXT, target_evidence_ref TEXT
        );
        CREATE TABLE IF NOT EXISTS runs (
          run_id TEXT PRIMARY KEY, binding_id TEXT NOT NULL, registration_id TEXT NOT NULL,
          task_id TEXT NOT NULL, started_at TEXT NOT NULL, expires_at TEXT NOT NULL,
          result TEXT, completed_at TEXT, evidence_ref TEXT
        );
        CREATE TABLE IF NOT EXISTS alert_receipts (
          alert_id TEXT PRIMARY KEY, registration_id TEXT NOT NULL,
          host_notification_id TEXT NOT NULL, observed_at TEXT NOT NULL,
          evidence_ref TEXT NOT NULL, recorded_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS pause_receipts (
          receipt_id TEXT PRIMARY KEY, binding_id TEXT NOT NULL, registration_id TEXT NOT NULL,
          observed_at TEXT NOT NULL,
          approval_ref TEXT NOT NULL, recorded_at TEXT NOT NULL
        );
        """)
        columns = {row[1] for row in db.execute("PRAGMA table_info(schedules)")}
        for name, definition in (
                ("schedule_kind", "TEXT"),
                ("target_thread_id", "TEXT"),
                ("target_archived", "INTEGER"),
                ("target_verified_at", "TEXT"),
                ("target_evidence_ref", "TEXT")):
            if name not in columns:
                db.execute(f"ALTER TABLE schedules ADD COLUMN {name} {definition}")
        db.execute("BEGIN IMMEDIATE")
        yield root, db
        db.execute("COMMIT")
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    finally:
        db.close()


def engine_state(home):
    path = home / "inbox-engine.sqlite3"
    if path.is_symlink() or not path.is_file():
        raise RegistryError("engine_not_initialized", "registered home has no safe initialized engine", home=str(home))
    try:
        uri = "file:" + str(path) + "?mode=ro"
        with sqlite3.connect(uri, uri=True) as db:
            row = db.execute("SELECT data FROM engine WHERE id=1").fetchone()
        state = json.loads(row[0]) if row else None
    except (sqlite3.Error, json.JSONDecodeError, TypeError) as exc:
        raise RegistryError("engine_unreadable", "could not read initialized engine", home=str(home)) from exc
    config = state.get("config") if isinstance(state, dict) else None
    transport = config.get("transport") if isinstance(config, dict) else None
    if not isinstance(transport, dict):
        raise RegistryError("engine_not_configured", "engine must have an approved transport configuration")
    if type(state.get("version")) is not int or state["version"] < 1:
        raise RegistryError("engine_unreadable", "engine has no valid configured version", home=str(home))
    if config.get("chat_output", "full") == "profile":
        import inbox_engine
        try:
            inbox_engine.validate_profile(home.resolve(), config.get("profile_files"))
        except ValueError as exc:
            raise RegistryError("profile_changed", "approved chat profile changed; configure before resuming") from exc
    return {"mailbox": mailbox(state.get("mailbox")), "version": state.get("version"),
            "active": state.get("active") is True, "paused": state.get("paused") is True,
            "card_ref": clean(transport.get("card_ref"), "engine transport card_ref"),
            "transport_mailbox": mailbox(transport.get("mailbox"), "engine transport mailbox"),
            "chat_output": config.get("chat_output", "full")}


def provider_observation(value, expected):
    if not isinstance(value, dict):
        raise RegistryError("provider_readback_required", "observed provider identity required")
    observed = {
        "provider": clean(value.get("provider"), "provider_observation.provider"),
        "mailbox": mailbox(value.get("mailbox"), "provider_observation.mailbox"),
        "account_id": clean(value.get("account_id"), "provider_observation.account_id"),
        "connector_ref": clean(value.get("connector_ref"), "provider_observation.connector_ref"),
        "card_ref": clean(value.get("card_ref"), "provider_observation.card_ref"),
    }
    current_readback(value.get("observed_at"), "provider_observation.observed_at")
    clean(value.get("evidence_ref"), "provider_observation.evidence_ref")
    mismatched = [key for key, wanted in expected.items() if observed.get(key) != wanted]
    if mismatched:
        raise RegistryError("provider_identity_mismatch", "observed provider identity does not match binding",
                            fields=mismatched)
    return observed


def resolve_row(db, value):
    key = mailbox(value) if isinstance(value, str) and "@" in value else clean(value, "mailbox").lower()
    rows = db.execute("""SELECT r.* FROM registrations r LEFT JOIN aliases a ON a.registration_id=r.id
                         WHERE lower(r.mailbox)=? OR lower(a.alias)=?""", (key, key)).fetchall()
    unique = {row["id"]: row for row in rows}
    if not unique:
        raise RegistryError("mailbox_not_registered", "mailbox or alias is not registered", mailbox=key)
    if len(unique) != 1:
        raise RegistryError("ambiguous_alias", "alias resolves to more than one mailbox", alias=key)
    return next(iter(unique.values()))


def row_public(db, row):
    return {"registry_id": row["id"], "mailbox": row["mailbox"], "home": row["home"],
            "provider": row["provider"], "account_id": row["account_id"],
            "connector_ref": row["connector_ref"], "card_ref": row["card_ref"],
            "provider_verified_at": row["provider_verified_at"],
            "provider_evidence_ref": row["provider_evidence_ref"],
            "aliases": [r[0] for r in db.execute(
                "SELECT alias FROM aliases WHERE registration_id=? ORDER BY alias", (row["id"],))],
            "identity_hash": row["identity_hash"], "engine_version": row["engine_version"]}


def register(root, db, p):
    approval_ref = approval(p)
    box = mailbox(p.get("mailbox"))
    raw_home = Path(clean(p.get("home"), "home"))
    if not raw_home.is_absolute():
        raise RegistryError("home_not_absolute", "registered inbox home must be absolute")
    home = raw_home.resolve()
    if home == root or not home.is_relative_to(root):
        raise RegistryError("home_outside_root", "registered inbox home must be inside root", home=str(home))
    if not home.is_dir() or home.is_symlink():
        raise RegistryError("unsafe_home", "registered inbox home must be an existing non-symlink directory")
    provider = clean(p.get("provider"), "provider")
    account_id = clean(p.get("account_id"), "account_id")
    connector_ref = clean(p.get("connector_ref"), "connector_ref")
    card_ref = clean(p.get("card_ref"), "card_ref")
    expected = {"provider": provider, "mailbox": box, "account_id": account_id,
                "connector_ref": connector_ref, "card_ref": card_ref}
    observation = p.get("provider_observation")
    provider_observation(observation, expected)
    engine = engine_state(home)
    if engine["mailbox"] != box or engine["transport_mailbox"] != box or engine["card_ref"] != card_ref:
        raise RegistryError("engine_identity_mismatch", "engine mailbox or configured card does not match registration")
    aliases = p.get("aliases", [])
    if not isinstance(aliases, list) or len(aliases) > 50:
        raise RegistryError("invalid_aliases", "aliases must be a bounded list")
    aliases = sorted({clean(a, "alias", 320).lower() for a in aliases})
    if box in aliases:
        aliases.remove(box)
    identity = {**expected, "connector_ref": connector_ref, "card_ref": card_ref, "home": str(home)}
    identity_hash = digest(identity)
    existing = db.execute("SELECT * FROM registrations WHERE lower(mailbox)=? OR home=?",
                          (box, str(home))).fetchall()
    for old in existing:
        if old["mailbox"].lower() != box or old["identity_hash"] != identity_hash:
            raise RegistryError("identity_rebind_rejected", "mailbox, account, card, or home cannot be silently rebound")
        old_aliases = [r[0] for r in db.execute("SELECT alias FROM aliases WHERE registration_id=? ORDER BY alias", (old["id"],))]
        if aliases != old_aliases:
            raise RegistryError("identity_rebind_rejected", "registered aliases cannot be silently changed")
        db.execute("""UPDATE registrations SET engine_version=?, provider_verified_at=?,
                      provider_evidence_ref=?, updated_at=?, approval_ref=? WHERE id=?""",
                   (engine["version"], observation["observed_at"], observation["evidence_ref"],
                    stamp(), approval_ref, old["id"]))
        return row_public(db, db.execute("SELECT * FROM registrations WHERE id=?", (old["id"],)).fetchone())
    claimed_alias = db.execute("""SELECT r.mailbox FROM aliases a JOIN registrations r
                                  ON r.id=a.registration_id WHERE lower(a.alias)=?""", (box,)).fetchone()
    if claimed_alias:
        raise RegistryError("ambiguous_alias", "mailbox is already an alias for another registration",
                            mailbox=claimed_alias["mailbox"])
    account = db.execute("SELECT mailbox FROM registrations WHERE provider=? AND account_id=?",
                         (provider, account_id)).fetchone()
    if account:
        raise RegistryError("identity_rebind_rejected", "provider account is already bound to another mailbox",
                            mailbox=account["mailbox"])
    for alias in aliases:
        if EMAIL.fullmatch(alias) and db.execute("SELECT 1 FROM registrations WHERE lower(mailbox)=?", (alias,)).fetchone():
            raise RegistryError("ambiguous_alias", "alias is another registered mailbox", alias=alias)
        if db.execute("SELECT 1 FROM aliases WHERE lower(alias)=?", (alias,)).fetchone():
            raise RegistryError("ambiguous_alias", "alias is already registered", alias=alias)
    reg_id = "inbox_" + digest(identity)[:20]
    now = stamp()
    db.execute("INSERT INTO registrations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (reg_id, box, str(home), provider, account_id, connector_ref, card_ref,
                identity_hash, engine["version"], observation["observed_at"],
                observation["evidence_ref"], approval_ref, now, now))
    db.executemany("INSERT INTO aliases VALUES (?,?)", [(a, reg_id) for a in aliases])
    return row_public(db, db.execute("SELECT * FROM registrations WHERE id=?", (reg_id,)).fetchone())


def invocation_prompt(root, mailbox_value, task_id, binding_id, chat_output="full"):
    """The exact human-readable prompt; preserve legacy full-mode bytes."""
    if chat_output not in {"full", "profile"}:
        raise RegistryError("invalid_chat_output", "chat_output must be full or profile")
    if chat_output == "profile":
        return ("Run Inbox Manager for mailbox " + json.dumps(mailbox_value) + " through registry root " +
                json.dumps(str(root)) + ". Use task_id " + json.dumps(task_id) + " and binding_id " +
                json.dumps(binding_id) + "; pass that exact binding and fresh provider/task readbacks to inbox_cycle begin. "
                "Use inbox_cycle for processing and finish the same run with its actual result and fresh task readback. "
                "Read the selected home's approved, hash-bound chat-preferences.md. Follow owner-report.md and "
                "chat-output.md for presentation: focused mode shows only the current email or one decision; "
                "full mode shows the complete dashboard. Retain the complete rendered report and immutable question "
                "mapping in private durable evidence for every run. Preserve actual completion and coverage; "
                "disclose processing failures. Presentation grants no mail authority. Respect host notification requirements.")
    return ("Run Inbox Manager for mailbox " + json.dumps(mailbox_value) + " through registry root " +
            json.dumps(str(root)) + ". Use task_id " + json.dumps(task_id) + " and binding_id " +
            json.dumps(binding_id) + "; pass that exact binding and fresh provider/task readbacks to inbox_cycle begin. "
            "Use inbox_cycle for processing and finish the same run with its actual result "
            "and fresh task readback. Finish every run with the complete rendered inbox dashboard, "
            "including unchanged, partial, and failed runs. Repeat every unresolved action, "
            "waiting item, draft status, numbered question, and email link. Do not replace it with a summary. "
            "The owner explicitly requests a periodic full report, not a changes-only monitor. "
            "Repeat the complete report even when nothing changed: the owner acts from the latest response alone. "
            "Include all six sections: Run result, Needs your attention, Waiting on others, Completed this run, "
            "Drafts, and Questions. Preserve every row and link; counts, top items, or a file link alone are insufficient. "
            "Retrieve the full formatter output if truncated. When the host requires a heartbeat notification block, "
            "include the complete rendered report before that block wherever the host permits; the short notification "
            "supplements the report rather than replacing it. Respect host response requirements and state any "
            "delivery limitation accurately.")


def target_readback(value, expected_thread_id, require_active):
    if not isinstance(value, dict):
        raise RegistryError("scheduler_target_readback_required",
                            "observed heartbeat target task readback required")
    thread_id = clean(value.get("thread_id"), "scheduler_readback.target_readback.thread_id")
    if thread_id != expected_thread_id:
        raise RegistryError("scheduler_target_mismatch",
                            "observed heartbeat target does not match the bound task",
                            expected_thread_id=expected_thread_id, observed_thread_id=thread_id)
    if type(value.get("archived")) is not bool:
        raise RegistryError("scheduler_target_readback_required",
                            "heartbeat target archived state must be observed exactly")
    current_readback(value.get("observed_at"), "scheduler_readback.target_readback.observed_at")
    evidence_ref = clean(value.get("evidence_ref"), "scheduler_readback.target_readback.evidence_ref")
    if require_active and value["archived"]:
        raise RegistryError("scheduler_target_archived",
                            "an archived task cannot be activated or dispatch a heartbeat",
                            thread_id=thread_id)
    return {"thread_id": thread_id, "archived": value["archived"],
            "observed_at": value["observed_at"], "evidence_ref": evidence_ref}


def schedule_readback(value, schedule, enabled=None, require_next=False, expected_prompt=None,
                      allow_setup_prompt=False, require_target_active=False):
    if not isinstance(value, dict):
        raise RegistryError("scheduler_readback_required", "observed scheduler task readback required")
    if type(value.get("host_required")) is not bool or type(value.get("enabled")) is not bool:
        raise RegistryError("scheduler_readback_mismatch", "scheduler boolean state must be observed exactly")
    static = ("task_id", "cadence", "timezone", "execution_location", "host_required")
    bad = [field for field in static if value.get(field) != schedule[field]]
    if enabled is not None and value.get("enabled") is not enabled:
        bad.append("enabled")
    if bad:
        raise RegistryError("scheduler_readback_mismatch", "scheduler readback does not match task binding", fields=bad)
    expected_kind = schedule.get("schedule_kind", schedule.get("kind"))
    if expected_kind not in SCHEDULER_KINDS or value.get("kind") != expected_kind:
        raise RegistryError("scheduler_readback_mismatch", "scheduler kind does not match task binding",
                            fields=["kind"])
    target = None
    expected_target = schedule.get("target_thread_id")
    if expected_kind == "heartbeat":
        if value.get("target_thread_id") != expected_target:
            raise RegistryError("scheduler_target_mismatch",
                                "scheduler heartbeat target does not match the bound task",
                                expected_thread_id=expected_target,
                                observed_thread_id=value.get("target_thread_id"))
        target = target_readback(value.get("target_readback"), expected_target,
                                 require_target_active)
    elif value.get("target_thread_id") is not None or value.get("target_readback") is not None:
        raise RegistryError("scheduler_target_mismatch", "cron scheduler readback must not claim a task target")
    if expected_prompt is not None:
        if allow_setup_prompt:
            clean(value.get("prompt"), "scheduler_readback.prompt", 10000)
        elif value.get("prompt") != expected_prompt:
            raise RegistryError("scheduler_prompt_mismatch", "scheduler prompt does not match exact invocation")
    current_readback(value.get("observed_at"), "scheduler_readback.observed_at")
    clean(value.get("evidence_ref"), "scheduler_readback.evidence_ref")
    if value.get("next_run_at") is not None:
        parse_time(value["next_run_at"], "scheduler_readback.next_run_at")
    elif require_next:
        raise RegistryError("next_run_unverified", "scheduler readback has no next run")
    return value, target


def schedule(root, db, p):
    approval_ref = approval(p)
    reg = resolve_row(db, p.get("mailbox"))
    engine = engine_state(Path(reg["home"]))
    if (engine["mailbox"] != reg["mailbox"] or engine["transport_mailbox"] != reg["mailbox"] or
            engine["card_ref"] != reg["card_ref"]):
        raise RegistryError("stale_identity", "live engine identity differs from registration")
    kind = clean(p.get("kind"), "kind", 40)
    if kind not in SCHEDULER_KINDS:
        raise RegistryError("unsupported_scheduler_kind", "kind must be heartbeat or cron", kind=kind)
    target_thread_id = p.get("target_thread_id")
    if kind == "heartbeat":
        target_thread_id = clean(target_thread_id, "target_thread_id")
    elif target_thread_id is not None:
        raise RegistryError("invalid_scheduler_target", "cron schedules do not bind a task target")
    fields = {"task_id": clean(p.get("task_id"), "task_id"),
              "cadence": clean(p.get("cadence"), "cadence"),
              "timezone": clean(p.get("timezone"), "timezone"),
              "execution_location": clean(p.get("execution_location"), "execution_location"),
              "kind": kind, "target_thread_id": target_thread_id}
    if type(p.get("host_required")) is not bool or type(p.get("enabled")) is not bool:
        raise RegistryError("invalid_field", "host_required and enabled must be booleans")
    fields["host_required"] = p["host_required"]
    fields["enabled"] = p["enabled"]
    verified_at = p.get("verified_at")
    parse_time(verified_at, "verified_at")
    evidence_ref = clean(p.get("evidence_ref"), "evidence_ref")
    grace = integer(p.get("grace_seconds"), 30, 86400, "grace_seconds")
    next_run = p.get("next_run_at")
    if next_run is not None:
        parse_time(next_run, "next_run_at")
    immutable = {"registry_id": reg["id"], "identity_hash": reg["identity_hash"],
                 "engine_version": engine["version"], "engine_card_ref": engine["card_ref"],
                 **{k: fields[k] for k in ("task_id", "cadence", "timezone", "execution_location",
                                           "host_required", "kind", "target_thread_id")}}
    binding_hash = digest(immutable)
    binding_id = "binding_" + binding_hash[:24]
    prompt = invocation_prompt(root, reg["mailbox"], fields["task_id"], binding_id,
                               engine.get("chat_output", "full"))
    readback, target = schedule_readback(
        p.get("scheduler_readback"), fields, p["enabled"], expected_prompt=prompt,
        allow_setup_prompt=not p["enabled"], require_target_active=p["enabled"])
    if readback["observed_at"] != verified_at or readback["evidence_ref"] != evidence_ref or readback.get("next_run_at") != next_run:
        raise RegistryError("scheduler_readback_mismatch", "verified fields must come from the exact scheduler readback")
    prior = db.execute("SELECT * FROM schedules WHERE registration_id=?", (reg["id"],)).fetchone()
    if prior and prior["binding_id"] != binding_id and not prior["locally_paused"]:
        raise RegistryError("schedule_rebind_rejected", "pause the existing binding before replacing it")
    now = stamp()
    if prior:
        db.execute("DELETE FROM schedules WHERE registration_id=?", (reg["id"],))
    db.execute("""INSERT INTO schedules
                  (registration_id,binding_id,binding_hash,task_id,cadence,timezone,execution_location,
                   host_required,host_enabled,locally_paused,verified_at,evidence_ref,next_run_at,
                   next_run_verified,grace_seconds,engine_version,engine_card_ref,identity_hash,
                   approval_ref,created_at,updated_at,schedule_kind,target_thread_id,target_archived,
                   target_verified_at,target_evidence_ref)
                  VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
               (reg["id"], binding_id, binding_hash, fields["task_id"], fields["cadence"], fields["timezone"],
                fields["execution_location"], int(fields["host_required"]), int(fields["enabled"]), 0,
                verified_at, evidence_ref, next_run, int(next_run is not None), grace, engine["version"],
                engine["card_ref"], reg["identity_hash"], approval_ref, now, now, kind, target_thread_id,
                int(target["archived"]) if target else None, target["observed_at"] if target else None,
                target["evidence_ref"] if target else None))
    return {"binding_id": binding_id, "binding_hash": binding_hash, "invocation_prompt": prompt,
            "kind": kind, "target_thread_id": target_thread_id,
            "invocation": {"root": str(root), "mailbox": reg["mailbox"],
                           "task_id": fields["task_id"], "binding_id": binding_id,
                           "prompt": prompt}}


def exact_binding(db, p):
    reg = resolve_row(db, p.get("mailbox"))
    binding_id = clean(p.get("binding_id"), "binding_id")
    task_id = clean(p.get("task_id"), "task_id")
    sched = db.execute("SELECT * FROM schedules WHERE registration_id=?", (reg["id"],)).fetchone()
    if not sched or sched["binding_id"] != binding_id or sched["task_id"] != task_id:
        raise RegistryError("binding_mismatch", "mailbox, task, and binding must match exactly")
    return reg, sched


def dispatch(root, db, p):
    reg, sched = exact_binding(db, p)
    if sched["schedule_kind"] not in SCHEDULER_KINDS:
        raise RegistryError("scheduler_target_unverified",
                            "legacy schedule must be rebound with an observed scheduler kind and target")
    if sched["locally_paused"] or not sched["host_enabled"]:
        raise RegistryError("schedule_disabled", "disabled or paused schedule cannot dispatch")
    engine = engine_state(Path(reg["home"]))
    if not engine["active"] or engine["paused"]:
        raise RegistryError("engine_paused", "inactive or paused inbox engine cannot dispatch")
    if (engine["version"] != sched["engine_version"] or engine["card_ref"] != sched["engine_card_ref"] or
            reg["identity_hash"] != sched["identity_hash"] or engine["mailbox"] != reg["mailbox"] or
            engine["transport_mailbox"] != reg["mailbox"]):
        raise RegistryError("stale_binding", "engine config or identity changed after schedule binding")
    expected = {k: reg[k] for k in ("provider", "mailbox", "account_id", "connector_ref", "card_ref")}
    provider_observation(p.get("provider_observation"), expected)
    prompt = invocation_prompt(root, reg["mailbox"], sched["task_id"], sched["binding_id"],
                                   engine_state(Path(reg["home"])).get("chat_output", "full"))
    readback, target = schedule_readback(p.get("scheduler_readback"), dict(sched), True,
                                         expected_prompt=prompt, require_target_active=True)
    run_id = clean(p.get("run_id"), "run_id")
    if db.execute("SELECT 1 FROM runs WHERE run_id=?", (run_id,)).fetchone():
        raise RegistryError("run_id_reused", "run_id must be globally unique", run_id=run_id)
    seconds = integer(p.get("lease_seconds", 3600), 60, 14400, "lease_seconds")
    started = stamp()
    expires = (utcnow() + timedelta(seconds=seconds)).isoformat()
    db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?)",
               (run_id, sched["binding_id"], reg["id"], sched["task_id"], started, expires, None, None, None))
    db.execute("""UPDATE schedules SET verified_at=?, evidence_ref=?, target_archived=?,
                  target_verified_at=?, target_evidence_ref=?, updated_at=? WHERE registration_id=?""",
               (readback["observed_at"], readback["evidence_ref"],
                int(target["archived"]) if target else None,
                target["observed_at"] if target else None,
                target["evidence_ref"] if target else None, stamp(), reg["id"]))
    return {"run_id": run_id, "started_at": started, "expires_at": expires,
            "invocation": {"root": str(root), "mailbox": reg["mailbox"], "task_id": sched["task_id"],
                           "binding_id": sched["binding_id"], "prompt": prompt}}


def complete(root, db, p):
    reg, sched = exact_binding(db, p)
    run_id = clean(p.get("run_id"), "run_id")
    result = p.get("result")
    if result not in RESULTS:
        raise RegistryError("invalid_result", "result must be success, failed, incomplete, or skipped")
    evidence_ref = clean(p.get("evidence_ref"), "evidence_ref")
    run = db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
    if not run or run["binding_id"] != sched["binding_id"] or run["registration_id"] != reg["id"]:
        raise RegistryError("run_mismatch", "run does not belong to exact binding")
    if run["result"] is not None:
        if run["result"] == result and run["evidence_ref"] == evidence_ref:
            return {"run_id": run_id, "result": result, "completed_at": run["completed_at"],
                    "next_run_verified": bool(sched["next_run_verified"]), "idempotent": True}
        raise RegistryError("run_already_completed", "run already has a different recorded result",
                            result=run["result"])
    readback = p.get("scheduler_readback")
    if readback is None:
        db.execute("UPDATE schedules SET next_run_verified=0, updated_at=? WHERE registration_id=?",
                   (stamp(), reg["id"]))
    else:
        prompt = invocation_prompt(root, reg["mailbox"], sched["task_id"], sched["binding_id"],
                                   engine_state(Path(reg["home"])).get("chat_output", "full"))
        readback, target = schedule_readback(readback, dict(sched), expected_prompt=prompt,
                                             require_target_active=False)
        enabled = readback.get("enabled")
        if type(enabled) is not bool:
            raise RegistryError("scheduler_readback_mismatch", "scheduler enabled state must be observed")
        db.execute("""UPDATE schedules SET host_enabled=?, verified_at=?, evidence_ref=?, next_run_at=?,
                      next_run_verified=?, target_archived=?, target_verified_at=?,
                      target_evidence_ref=?, updated_at=? WHERE registration_id=?""",
                   (int(enabled), readback["observed_at"], readback["evidence_ref"], readback.get("next_run_at"),
                    int(readback.get("next_run_at") is not None),
                    int(target["archived"]) if target else None,
                    target["observed_at"] if target else None,
                    target["evidence_ref"] if target else None, stamp(), reg["id"]))
    completed = stamp()
    db.execute("UPDATE runs SET result=?, completed_at=?, evidence_ref=? WHERE run_id=?",
               (result, completed, evidence_ref, run_id))
    return {"run_id": run_id, "result": result, "completed_at": completed,
            "next_run_verified": bool(readback and readback.get("next_run_at") is not None)}


def pause(root, db, p):
    approval_ref = approval(p)
    reg, sched = exact_binding(db, p)
    receipt = p.get("owner_receipt")
    if not isinstance(receipt, dict) or receipt.get("authenticated") is not True:
        raise RegistryError("owner_receipt_required", "authenticated owner pause receipt required")
    receipt_id = clean(receipt.get("receipt_id"), "owner_receipt.receipt_id")
    current_readback(receipt.get("observed_at"), "owner_receipt.observed_at")
    host_enabled = sched["host_enabled"]
    verified_at, evidence_ref = sched["verified_at"], sched["evidence_ref"]
    readback = p.get("scheduler_readback")
    if readback is not None:
        prompt = invocation_prompt(root, reg["mailbox"], sched["task_id"], sched["binding_id"],
                                   engine_state(Path(reg["home"])).get("chat_output", "full"))
        readback, target = schedule_readback(readback, dict(sched), False, expected_prompt=prompt,
                                             require_target_active=False)
        host_enabled = 0
        verified_at, evidence_ref = readback["observed_at"], readback["evidence_ref"]
    else:
        target = None
    db.execute("""UPDATE schedules SET locally_paused=1, host_enabled=?, verified_at=?, evidence_ref=?,
                  target_archived=COALESCE(?,target_archived),
                  target_verified_at=COALESCE(?,target_verified_at),
                  target_evidence_ref=COALESCE(?,target_evidence_ref),
                  approval_ref=?, updated_at=? WHERE registration_id=?""",
               (host_enabled, verified_at, evidence_ref,
                int(target["archived"]) if target else None,
                target["observed_at"] if target else None,
                target["evidence_ref"] if target else None,
                approval_ref, stamp(), reg["id"]))
    prior = db.execute("SELECT * FROM pause_receipts WHERE receipt_id=?", (receipt_id,)).fetchone()
    if prior and (prior["binding_id"], prior["registration_id"], prior["observed_at"], prior["approval_ref"]) != (
            sched["binding_id"], reg["id"], receipt["observed_at"], approval_ref):
        raise RegistryError("owner_receipt_conflict", "owner pause receipt was already used differently")
    if not prior:
        db.execute("INSERT INTO pause_receipts VALUES (?,?,?,?,?,?)", (receipt_id, sched["binding_id"],
                   reg["id"], receipt["observed_at"], approval_ref, stamp()))
    return {"binding_id": sched["binding_id"], "locally_paused": True,
            "host_task_disabled_observed": host_enabled == 0}


def observation_is_current(value, field):
    if value is None:
        return False
    try:
        current_readback(value, field)
        return True
    except RegistryError:
        return False


def alerts_for(db, reg, sched, engine, now, target_verified, target_archived):
    alerts = []
    def add(kind, ref, message):
        alert_id = "alert_" + digest({"binding_id": sched["binding_id"], "kind": kind, "ref": ref})[:24]
        receipt = db.execute("SELECT * FROM alert_receipts WHERE alert_id=?", (alert_id,)).fetchone()
        alerts.append({"alert_id": alert_id, "kind": kind, "message": message,
                       "host_notification_receipt": dict(receipt) if receipt else None})
    if sched["locally_paused"]:
        add("locally_paused", sched["updated_at"], "dispatch is locally paused")
    if not sched["host_enabled"]:
        add("host_disabled", sched["verified_at"], "scheduler task was observed disabled")
    if not sched["next_run_verified"]:
        add("next_run_unverified", sched["updated_at"], "next scheduler run is unverified")
    elif sched["host_enabled"] and not sched["locally_paused"]:
        due = parse_time(sched["next_run_at"], "next_run_at")
        if now > due + timedelta(seconds=sched["grace_seconds"]):
            add("missed_task", sched["next_run_at"], "scheduler task missed its grace window")
        elif now > due:
            add("overdue_task", sched["next_run_at"], "scheduler task is overdue within its grace window")
    if sched["schedule_kind"] not in SCHEDULER_KINDS or (
            sched["schedule_kind"] == "heartbeat" and not target_verified):
        add("scheduler_target_unverified", sched["updated_at"],
            "scheduler kind or heartbeat target is unverified; rebind before activation")
    elif sched["schedule_kind"] == "heartbeat" and target_archived:
        add("scheduler_target_archived", sched["target_thread_id"],
            "heartbeat target task was observed archived")
    if engine.get("error"):
        add("engine_unverified", engine["error"], "live engine state could not be verified")
    elif (engine["version"] != sched["engine_version"] or engine["card_ref"] != sched["engine_card_ref"] or
          engine["mailbox"] != reg["mailbox"] or engine["transport_mailbox"] != reg["mailbox"]):
        add("stale_binding", str(engine.get("version")), "live engine identity or config changed")
    elif not engine["active"] or engine["paused"]:
        add("engine_paused", str(engine["paused"]), "live engine is inactive or paused")
    expired = db.execute("""SELECT * FROM runs WHERE registration_id=? AND result IS NULL
                             AND expires_at<? ORDER BY started_at""", (reg["id"], now.isoformat())).fetchall()
    for run in expired:
        add("unfinished_expired_run", run["run_id"], "scheduled run expired without a recorded result")
    return alerts


def health(root, db, p):
    if p.get("scheduler_readback") is not None and p.get("mailbox") is None:
        raise RegistryError("mailbox_required", "a scheduler health readback requires one mailbox")
    rows = [resolve_row(db, p["mailbox"])] if p.get("mailbox") is not None else db.execute(
        "SELECT * FROM registrations ORDER BY mailbox").fetchall()
    now = utcnow()
    output = []
    for reg in rows:
        try:
            engine = engine_state(Path(reg["home"]))
        except RegistryError as exc:
            engine = {"error": exc.code}
        sched = db.execute("SELECT * FROM schedules WHERE registration_id=?", (reg["id"],)).fetchone()
        latest = db.execute("""SELECT run_id,result,started_at,completed_at,evidence_ref FROM runs
                               WHERE registration_id=? AND binding_id=? AND result IS NOT NULL
                               ORDER BY completed_at DESC LIMIT 1""",
                            (reg["id"], sched["binding_id"] if sched else "")).fetchone()
        item = {**row_public(db, reg), "engine": engine, "schedule": None,
                "latest_result": dict(latest) if latest else None, "alerts": []}
        if sched:
            observed_enabled = None
            observed_target = None
            if p.get("scheduler_readback") is not None:
                if sched["schedule_kind"] not in SCHEDULER_KINDS:
                    raise RegistryError("scheduler_target_unverified",
                                        "legacy schedule must be rebound before health can verify its target")
                prompt = invocation_prompt(root, reg["mailbox"], sched["task_id"], sched["binding_id"],
                                   engine_state(Path(reg["home"])).get("chat_output", "full"))
                observed, observed_target = schedule_readback(
                    p["scheduler_readback"], dict(sched), expected_prompt=prompt,
                    require_target_active=False)
                observed_enabled = observed["enabled"]
                db.execute("""UPDATE schedules SET host_enabled=?, verified_at=?, evidence_ref=?,
                              next_run_at=?, next_run_verified=?, target_archived=?,
                              target_verified_at=?, target_evidence_ref=?, updated_at=?
                              WHERE registration_id=?""",
                           (int(observed_enabled), observed["observed_at"], observed["evidence_ref"],
                            observed.get("next_run_at"), int(observed.get("next_run_at") is not None),
                            int(observed_target["archived"]) if observed_target else None,
                            observed_target["observed_at"] if observed_target else None,
                            observed_target["evidence_ref"] if observed_target else None,
                            stamp(), reg["id"]))
                sched = db.execute("SELECT * FROM schedules WHERE registration_id=?",
                                   (reg["id"],)).fetchone()
            kind = sched["schedule_kind"]
            if kind == "heartbeat":
                target_archived = (observed_target["archived"] if observed_target is not None
                                   else bool(sched["target_archived"])
                                   if sched["target_archived"] is not None else None)
                target_verified = (observed_target is not None or
                                   (sched["target_thread_id"] is not None and
                                    sched["target_archived"] is not None and
                                    observation_is_current(sched["target_verified_at"],
                                                           "target_verified_at")))
            elif kind == "cron":
                target_archived, target_verified = None, None
            else:
                target_archived, target_verified = None, False
            configured_active = bool(sched["host_enabled"] if observed_enabled is None else observed_enabled)
            configured_active = configured_active and not bool(sched["locally_paused"])
            latest_wake_verified = bool(latest and latest["result"] == "success")
            target_ready = kind == "cron" or (target_verified and target_archived is False)
            engine_ready = (not engine.get("error") and engine["active"] and not engine["paused"] and
                            engine["version"] == sched["engine_version"] and
                            engine["card_ref"] == sched["engine_card_ref"] and
                            engine["mailbox"] == reg["mailbox"] and
                            engine["transport_mailbox"] == reg["mailbox"])
            schedule_status = dict(sched)
            if observed_enabled is not None:
                schedule_status["host_enabled"] = int(observed_enabled)
            item["schedule"] = {k: schedule_status[k] for k in (
                "binding_id", "binding_hash", "task_id", "cadence", "timezone", "execution_location",
                "host_required", "host_enabled", "locally_paused", "verified_at", "evidence_ref",
                "next_run_at", "next_run_verified", "grace_seconds")}
            for key in ("host_required", "host_enabled", "locally_paused", "next_run_verified"):
                item["schedule"][key] = bool(item["schedule"][key])
            item["schedule"].update({
                "kind": kind, "target_thread_id": sched["target_thread_id"],
                "target_archived": target_archived, "target_verified": target_verified,
                "target_verified_at": (observed_target["observed_at"] if observed_target
                                       else sched["target_verified_at"]),
                "target_evidence_ref": (observed_target["evidence_ref"] if observed_target
                                        else sched["target_evidence_ref"]),
                "configured_active": configured_active,
                "latest_wake_verified": latest_wake_verified,
                "operating": bool(configured_active and sched["next_run_verified"] and
                                  target_ready and engine_ready and latest_wake_verified),
            })
            item["alerts"] = alerts_for(db, reg, schedule_status, engine, now,
                                         target_verified, target_archived)
        output.append(item)
    return {"inboxes": output, "checked_at": now.isoformat()}


def alert_receipt(root, db, p):
    reg = resolve_row(db, p.get("mailbox"))
    alert_id = clean(p.get("alert_id"), "alert_id")
    valid = {a["alert_id"] for item in health(root, db, {"mailbox": reg["mailbox"]})["inboxes"]
             for a in item["alerts"]}
    if alert_id not in valid:
        raise RegistryError("unknown_alert", "alert is not currently active for this mailbox")
    notification = clean(p.get("host_notification_id"), "host_notification_id")
    observed = p.get("observed_at")
    parse_time(observed, "observed_at")
    evidence = clean(p.get("evidence_ref"), "evidence_ref")
    prior = db.execute("SELECT * FROM alert_receipts WHERE alert_id=?", (alert_id,)).fetchone()
    if prior:
        if (prior["host_notification_id"], prior["observed_at"], prior["evidence_ref"]) != (notification, observed, evidence):
            raise RegistryError("alert_receipt_conflict", "alert already has a different host receipt")
        return {"alert_id": alert_id, "host_notification_received": True, "recipient_delivery": "unverified"}
    db.execute("INSERT INTO alert_receipts VALUES (?,?,?,?,?,?)",
               (alert_id, reg["id"], notification, observed, evidence, stamp()))
    return {"alert_id": alert_id, "host_notification_received": True, "recipient_delivery": "unverified"}


def execute(root, command, payload=None):
    p = {} if payload is None else payload
    if not isinstance(p, dict):
        raise RegistryError("invalid_payload", "JSON object required")
    reject_secrets(p)
    with transaction(root) as (resolved_root, db):
        if command == "register":
            return register(resolved_root, db, p)
        if command == "resolve":
            return row_public(db, resolve_row(db, p.get("mailbox")))
        if command == "list":
            return health(resolved_root, db, {})
        if command == "health":
            return health(resolved_root, db, p)
        if command == "schedule":
            return schedule(resolved_root, db, p)
        if command == "dispatch":
            return dispatch(resolved_root, db, p)
        if command == "complete":
            return complete(resolved_root, db, p)
        if command == "pause":
            return pause(resolved_root, db, p)
        if command == "alert-receipt":
            return alert_receipt(resolved_root, db, p)
        raise RegistryError("unknown_command", "unknown command", command=command)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("register", "resolve", "list", "schedule", "dispatch",
                                            "complete", "health", "pause", "alert-receipt"))
    parser.add_argument("--root", required=True)
    parser.add_argument("--input", help="JSON object file; stdin when omitted")
    args = parser.parse_args()
    try:
        if args.command == "list" and not args.input:
            payload = {}
        else:
            with open(args.input, encoding="utf-8") if args.input else sys.stdin as stream:
                payload = json.load(stream)
        print(json.dumps({"ok": True, "result": execute(args.root, args.command, payload)}, indent=2))
        return 0
    except RegistryError as exc:
        print(json.dumps({"ok": False, "error": exc.as_dict()}), file=sys.stderr)
        return 2
    except (OSError, sqlite3.Error, json.JSONDecodeError) as exc:
        error = RegistryError("registry_io_error", str(exc))
        print(json.dumps({"ok": False, "error": error.as_dict()}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
