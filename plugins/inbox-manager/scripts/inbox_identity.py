"""Responsibility identity shared by the engine and read-only projections."""

from __future__ import annotations

from email.utils import parseaddr
import re
import unicodedata


RESPONSIBILITY_TYPES = frozenset({"owner", "bot", "correspondent", "unknown"})
BOT_ALIASES = frozenset({"inbox-manager", "inbox manager", "bot", "assistant"})
LEGACY_OWNER_ALIASES = frozenset({"owner", "member", "inbox-owner", "inbox owner", "mailbox owner"})
OWNER_DISPLAY_FIELDS = (
    "owner_display",
    "owner_display_name",
    "owner_name",
)


def _identity(value):
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).strip()).casefold()


def owner_aliases(state):
    """Return exact normalized legacy labels that identify this mailbox owner."""
    config = state.get("config") or {}
    values = [state.get("mailbox"), (config.get("transport") or {}).get("mailbox")]
    configured = config.get("owner_aliases", [])
    if isinstance(configured, str):
        values.append(configured)
    elif isinstance(configured, (list, tuple, set)):
        values.extend(configured)
    for source in (state, config):
        values.extend(source.get(field) for field in OWNER_DISPLAY_FIELDS)
    aliases = {_identity(value) for value in values if isinstance(value, str)} | LEGACY_OWNER_ALIASES
    # A legacy responsible value may contain the configured mailbox as an RFC 5322
    # display address. Keep matching exact: arbitrary sender text never becomes owner.
    aliases.update(_identity(parseaddr(value)[1]) for value in values if isinstance(value, str))
    return frozenset(alias for alias in aliases if alias)


def normalize_responsibility_type(
    state, *, value=None, responsible=None, item_state=None
):
    """Validate an explicit type or safely classify a legacy responsibility label."""
    if value is not None:
        explicit = _identity(value)
        if explicit not in RESPONSIBILITY_TYPES:
            raise ValueError("responsibility_type must be owner, bot, correspondent, or unknown")
    else:
        explicit = None

    # These durable workflow states already bind the current responsible party.
    if item_state == "waiting_owner":
        return "owner"
    if item_state == "waiting_correspondent":
        return "correspondent"
    if explicit is not None:
        return explicit

    label = _identity(responsible)
    address = _identity(parseaddr(responsible)[1]) if isinstance(responsible, str) else ""
    if label in owner_aliases(state) or (address and address in owner_aliases(state)):
        return "owner"
    if label in BOT_ALIASES:
        return "bot"
    return "unknown"


def responsibility_type(state, item):
    """Read one case/obligation type, including the bounded legacy migration path."""
    return normalize_responsibility_type(
        state,
        value=item.get("responsibility_type"),
        responsible=item.get("responsible"),
        item_state=item.get("state"),
    )
