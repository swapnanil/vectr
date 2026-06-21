"""
WorkingContextStore — persists working notes the LLM saves to Vectr.

This is the core of the bidirectional protocol. The LLM calls vectr_remember()
to store what it has learned. Vectr stores it persistently for fast recall.
vectr_recall() brings it back on demand — later in this session or in a future
one. Recall is instant (<50ms) and lossless.

Storage: SQLite in the vectr DB dir — available immediately within the same
session and persists across IDE restarts and reboots.

Package layout:
  _audit.py      — rotating audit logger (_get_audit_logger, audit)
  _types.py      — dataclasses (WorkingNote, SnapshotEntry), constants (VALID_KINDS, DEFAULT_KIND)
  _encryption.py — field-level encryption (_NoteEncryptor, _build_encryptor,
                   _FILE_PATH_RE, _extract_file_paths)
  _store.py      — WorkingContextStore class (full store API)

All names that existed on the flat agent/working_context_store.py module are
re-exported here so every existing import site keeps working unchanged:
  from agent.working_context_store import WorkingContextStore
  from agent.working_context_store import WorkingNote
  from agent.working_context_store import SnapshotEntry
  from agent.working_context_store import VALID_KINDS
  from agent.working_context_store import DEFAULT_KIND
  from agent.working_context_store import audit
  from agent.working_context_store import _get_audit_logger
  from agent.working_context_store import _NoteEncryptor
  from agent.working_context_store import _build_encryptor
  from agent.working_context_store import _extract_file_paths
  from agent.working_context_store import _FILE_PATH_RE
"""
from __future__ import annotations

# Audit logger
from agent.working_context_store._audit import (
    _get_audit_logger,
    audit,
)

# Types and constants
from agent.working_context_store._types import (
    DEFAULT_KIND,
    VALID_KINDS,
    SnapshotEntry,
    WorkingNote,
)

# Encryption helpers
from agent.working_context_store._encryption import (
    _FILE_PATH_RE,
    _NoteEncryptor,
    _build_encryptor,
    _extract_file_paths,
)

# Store class
from agent.working_context_store._store import WorkingContextStore

__all__ = [
    # Audit
    "_get_audit_logger",
    "audit",
    # Types and constants
    "DEFAULT_KIND",
    "VALID_KINDS",
    "SnapshotEntry",
    "WorkingNote",
    # Encryption
    "_FILE_PATH_RE",
    "_NoteEncryptor",
    "_build_encryptor",
    "_extract_file_paths",
    # Store
    "WorkingContextStore",
]
