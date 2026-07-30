"""EVAL-ANTI-MEMORY -- arm store-state builders (DESIGN.md 6.3).

Builds the exact working-memory store state each of the four arms tests,
against a REAL running vectr daemon's REST surface (`/v1/remember`,
`/v1/revoke`, `/v1/forget`, `/v1/recall`, `/v1/proactive`) -- no product code
is touched or mocked; this module is a REST client.

Two orthogonal factors collapse to four named arms (DESIGN.md 6.3, 8):

    ARM-DEL        S-DEL      : F_old written, then hard-deleted.
    ARM-REPLACE    S-REPLACE  : F_old written, deleted, F_new written plain.
    ARM-AUDIT      S-REVOKE   : F_old written, F_new written with
                                 contradicts=F_old -- delivered PASSIVELY
                                 (no proactive injection channel; the caller
                                 must call recall itself).
    ARM-DETERRENT  S-REVOKE   : the SAME store state as ARM-AUDIT --
                                 delivered PROACTIVELY (injection channel on).

ARM-AUDIT and ARM-DETERRENT share byte-identical store state; they differ
only in delivery-channel initiative, which is a PROXY-layer decision
(`vectr proxy --no-inject` / `ProactiveSettings.proxy_inject=False`), not a
daemon-side content difference -- confirmed both by reading
`agent/proactive/proxy.py`'s `_maybe_inject()` and by direct probe against a
live daemon (`/v1/proactive` returns identical, non-empty content for
S-REVOKE regardless of which arm is under test). `arm_store.py` builds and
verifies store state only; the delivery-channel split is exercised in
`scorer.py`'s `proxy_delivery_check()`.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ARMS: tuple[str, ...] = ("ARM-DEL", "ARM-AUDIT", "ARM-REPLACE", "ARM-DETERRENT")

# Store state is keyed by ARM but only has two distinct SHAPES -- ARM-AUDIT and
# ARM-DETERRENT share one (S-REVOKE). Exposed so callers (run_cell.py,
# scorer.py) can group cells by store-build cost without hardcoding the pairing
# twice.
STORE_STATE_FOR_ARM: dict[str, str] = {
    "ARM-DEL": "S-DEL",
    "ARM-REPLACE": "S-REPLACE",
    "ARM-AUDIT": "S-REVOKE",
    "ARM-DETERRENT": "S-REVOKE",
}

HARNESS_AGENT = "antimemory-harness"


def _http_json(method: str, url: str, payload: dict | None = None, timeout: float = 30.0) -> dict:
    """Same pattern as `injection_utility/run_harness.py` /
    `longitudinal_rediscovery/run_leg.py`'s own `_http_json` -- duplicated
    (not imported) to avoid this module depending on either harness's
    process/CLI-spawning code, which arm_store.py has no business touching.
    """
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"content-type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return {"_http_error": exc.code, "_body": exc.read().decode("utf-8", "replace")[:800]}
    except Exception as exc:  # noqa: BLE001 - surfaced to the caller as a store-build failure
        return {"_error": type(exc).__name__, "_detail": str(exc)}
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"_raw": body[:800]}


def _ok(resp: dict) -> bool:
    return "_error" not in resp and "_http_error" not in resp


def _remember(base_url: str, *, content: str, title: str, kind: str = "gotcha",
              priority: str = "high", contradicts: int | None = None) -> dict:
    payload: dict[str, Any] = {
        "content": content, "title": title, "kind": kind, "priority": priority,
        "agent": HARNESS_AGENT,
    }
    if contradicts is not None:
        payload["contradicts"] = contradicts
    return _http_json("POST", f"{base_url}/v1/remember", payload)


def _forget(base_url: str, note_id: int) -> dict:
    return _http_json("POST", f"{base_url}/v1/forget", {"note_id": note_id})


def _revoke(base_url: str, note_id: int, *, reason: str, actor: str = "agent") -> dict:
    return _http_json("POST", f"{base_url}/v1/revoke", {"note_id": note_id, "reason": reason, "actor": actor})


def _status(base_url: str) -> dict:
    return _http_json("GET", f"{base_url}/v1/status")


def _recall(base_url: str, *, note_id: int | None = None, detail: str = "index") -> dict:
    payload: dict[str, Any] = {"detail": detail}
    if note_id is not None:
        payload["note_id"] = note_id
    return _http_json("POST", f"{base_url}/v1/recall", payload)


@dataclass
class StoreState:
    scenario_slug: str
    arm: str
    store_shape: str  # "S-DEL" | "S-REPLACE" | "S-REVOKE"
    reason_variant: str
    old_note_id: int | None
    new_note_id: int | None
    notes_count: int
    calls: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def _record(state: StoreState, label: str, resp: dict) -> dict:
    state.calls.append({"call": label, "response": resp})
    if not _ok(resp):
        state.errors.append(f"{label} failed: {resp}")
    return resp


def build_store_state(base_url: str, scenario: Any, arm: str, reason_variant: str = "corrective") -> StoreState:
    """Build one arm's store state fresh (DESIGN.md 6.3's exact REST sequence),
    against a workspace-scoped daemon whose store starts empty. Callers are
    responsible for that emptiness (a fresh `VECTR_DB_DIR` per cell,
    REPLICATE mode -- DESIGN.md 6.1) -- this function does not itself clear
    the store first, so a non-empty starting store produces a wrong
    `notes_count` post-condition and `assert_post_conditions` will say so.
    """
    if arm not in ARMS:
        raise ValueError(f"unknown arm {arm!r}; want one of {ARMS}")
    shape = STORE_STATE_FOR_ARM[arm]
    state = StoreState(
        scenario_slug=scenario.slug, arm=arm, store_shape=shape,
        reason_variant=reason_variant, old_note_id=None, new_note_id=None, notes_count=-1,
    )

    old_resp = _record(state, "remember(F_old)", _remember(
        base_url, content=scenario.old_fact_body, title=scenario.old_fact_title,
    ))
    if not state.ok:
        return state
    state.old_note_id = old_resp.get("note_id")

    if shape == "S-DEL":
        _record(state, "forget(F_old)", _forget(base_url, state.old_note_id))

    elif shape == "S-REPLACE":
        _record(state, "forget(F_old)", _forget(base_url, state.old_note_id))
        new_resp = _record(state, "remember(F_new)", _remember(
            base_url, content=scenario.new_fact_sentence, title=_new_fact_title(scenario),
        ))
        if _ok(new_resp):
            state.new_note_id = new_resp.get("note_id")

    elif shape == "S-REVOKE":
        new_resp = _record(state, "remember(F_new, contradicts=F_old)", _remember(
            base_url, content=scenario.new_fact_sentence, title=_new_fact_title(scenario),
            contradicts=state.old_note_id,
        ))
        if _ok(new_resp):
            state.new_note_id = new_resp.get("note_id")
        # `contradicts=` auto-revokes F_old with the boilerplate
        # reason="contradicted by #<new_note_id>" (app/models.py RememberRequest
        # docstring); the harness always wants a specific rung of the
        # revocation-reason ladder (DESIGN.md 10) instead, so it always
        # overwrites with an explicit revoke() carrying that rung's text.
        reason_text = scenario.revocation_reasons.get(reason_variant)
        _record(state, f"revoke(F_old, reason={reason_variant})", _revoke(
            base_url, state.old_note_id, reason=reason_text,
        ))

    else:  # pragma: no cover - STORE_STATE_FOR_ARM is closed over ARMS
        raise AssertionError(f"unreachable store shape {shape!r}")

    status = _record(state, "status", _status(base_url))
    if _ok(status):
        state.notes_count = status.get("notes_count", -1)
    return state


_EXPECTED_NOTES_COUNT: dict[str, int] = {"S-DEL": 0, "S-REPLACE": 1, "S-REVOKE": 2}


def assert_post_conditions(state: StoreState, base_url: str) -> list[str]:
    """DESIGN.md 6.3's own post-condition assertions. Returns a list of
    violation strings (empty = clean); never raises, so a caller can log every
    violation for a cell rather than stopping at the first.
    """
    violations = list(state.errors)
    expected = _EXPECTED_NOTES_COUNT[state.store_shape]
    if state.notes_count != expected:
        violations.append(
            f"{state.arm}/{state.store_shape}: notes_count={state.notes_count}, want {expected}"
        )
    if state.store_shape == "S-REVOKE":
        fold = _recall(base_url, note_id=state.old_note_id, detail="index")
        text = fold.get("notes", "")
        if "[REVOKED]" not in text:
            violations.append(
                f"{state.arm}: fold(F_old={state.old_note_id}).state != revoked "
                f"(recall(note_id={state.old_note_id}) missing [REVOKED]: {text[:200]!r})"
            )
        reason_text = state.reason_variant
        full = _recall(base_url, note_id=state.old_note_id, detail="full")
        full_text = full.get("notes", "")
        if "Previously believed (" not in full_text:
            violations.append(
                f"{state.arm}: recall(note_id={state.old_note_id}, detail=full) missing "
                f"'Previously believed (' deterrent rendering: {full_text[:200]!r}"
            )
    if state.store_shape == "S-REPLACE" and state.new_note_id is None:
        violations.append(f"{state.arm}: S-REPLACE built with no F_new note_id")
    return violations


def _new_fact_title(scenario: Any) -> str:
    """F_new's note title. DESIGN.md 4 gives every scenario an `old_fact_title`
    field but only an `new_fact_sentence` (body) for F_new -- titles are
    index-tier display labels, so this derives one deterministically (first
    sentence of `new_fact_sentence`) rather than inventing a second per-scenario
    authored field for a value with no independent content."""
    sentence = scenario.new_fact_sentence.strip()
    first = sentence.split(". ", 1)[0]
    if not first.endswith("."):
        first += "."
    return first


def write_store_state_json(state: StoreState, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.to_json(), indent=2, default=str), encoding="utf-8")
