"""Verbatim user-excerpt binding (UPG-MEM-PROVENANCE-USER-STATED).

The trust ladder (bm2-design-skeleton.md §5) has exactly two classes an AI
session can reach on its own: `agent` and `auto`. That leaves a note which
merely TRANSCRIBES something the user said rendering as "Memory to verify
(recorded by an AI session, not human-endorsed)" — understating what it is —
while the mirror risk is the opposite: an agent misremembering "the user said
X" and that claim resurfacing with no frame around it at all.

Neither problem is fixable by letting the writer declare its own authority:
an agent asserting "this came from the user" is self-attestation, exactly the
one-call trust forgery the `provenance="human"` rejection already refuses.
What CAN be checked by machine is whether the note actually quotes the user.
So a write may attach a verbatim excerpt of the user turn it transcribes, and
this module answers one deterministic question about it:

    is that excerpt genuinely present, verbatim, inside the note's content?

Exact characters, compared after whitespace normalization (a transcribed
excerpt is routinely re-wrapped; a re-wrapped quote is still verbatim). Case
is significant, and nothing here is semantic: no embedding, no similarity, no
paraphrase allowance, no query-content inspection. The check either succeeds
on the strings themselves or it does not, and only a success earns the
`user-stated` class (`_store.remember()`). A failure stores the note in the
ordinary `agent` class and reports why — the excerpt is discarded rather than
persisted, so the database never holds an unverified "the user said this".
"""
from __future__ import annotations

from agent.config import MEMORY_WRITE_USER_QUOTE_MIN_CHARS

# Rejection reasons surfaced back to the caller at write time. Fixed protocol
# strings, same category as the provenance framing constants in
# agent/trigger_engine.py: the wording IS the contract a caller reads, not an
# operator-tunable value (the one tunable here — how much of the user's actual
# wording an excerpt must carry — is memory_write.user_quote.min_chars).
USER_QUOTE_TOO_SHORT = (
    "user_quote not bound: the excerpt is shorter than "
    "{min_chars} characters, too little of the user's own wording to be "
    "evidence of anything — this note was stored with 'agent' provenance."
)
USER_QUOTE_NOT_CONTAINED = (
    "user_quote not bound: the excerpt does not appear verbatim in this "
    "note's content (whitespace-insensitive, case-sensitive), so nothing "
    "mechanically ties the note to what the user said — this note was stored "
    "with 'agent' provenance. Quote the user's words inside `content` to bind "
    "it."
)
# Auto-bind (UPG-PROVENANCE-NEVER-RISES) rejection reasons. Not caller-facing
# in the way the two above are — there is no explicit `user_quote` argument
# for a caller to have gotten wrong — but returned for the same symmetry and
# testability `bind_user_quote()` offers, and to keep every "why didn't this
# bind" answer computed by one pure function rather than re-derived ad hoc at
# a call site.
AUTO_QUOTE_TOO_SHORT = (
    "auto-bind not applied: this note's content is shorter than "
    "{min_chars} whitespace-normalized characters, too little of it to be "
    "evidence of anything even if it does match the captured user turn — "
    "this note keeps its ordinary provenance."
)
AUTO_QUOTE_NOT_CONTAINED = (
    "auto-bind not applied: this note's content does not appear verbatim in "
    "the most recently captured user turn (whitespace-insensitive, "
    "case-sensitive), so nothing mechanically ties the note to what the user "
    "said — this note keeps its ordinary provenance."
)
AUTO_QUOTE_NO_RECENT_TURN = (
    "auto-bind not applied: no user turn was captured recently enough (see "
    "memory_write.user_quote.auto_bind_max_age_seconds) to compare against — "
    "this note keeps its ordinary provenance."
)


def normalize_for_binding(text: str) -> str:
    """Collapse every run of whitespace to a single space and strip the ends.

    The ONLY transform applied before comparison. Line wrapping, indentation
    and trailing whitespace differ freely between a user turn and its
    transcription into a note; the words themselves must not.
    """
    return " ".join(text.split())


def bind_user_quote(content: str, user_quote: str | None) -> tuple[str, str]:
    """Return `(bound_quote, rejection_reason)` — exactly one is non-empty.

    `bound_quote` is the caller's excerpt with surrounding whitespace
    stripped, otherwise character-for-character as given (the stored evidence
    must be the user's text, not a normalized rewrite of it). An omitted or
    blank `user_quote` returns `("", "")`: no claim was made, so there is
    nothing to reject.

    Pure and total — no I/O, no clock, no store access — so every surface can
    call it to compute the same reason the store computed, without a second
    write path.
    """
    quote = (user_quote or "").strip()
    if not quote:
        return "", ""
    normalized_quote = normalize_for_binding(quote)
    if len(normalized_quote) < MEMORY_WRITE_USER_QUOTE_MIN_CHARS:
        return "", USER_QUOTE_TOO_SHORT.format(min_chars=MEMORY_WRITE_USER_QUOTE_MIN_CHARS)
    if normalized_quote not in normalize_for_binding(content):
        return "", USER_QUOTE_NOT_CONTAINED
    return quote, ""


def bind_user_quote_auto(content: str, recent_user_message: str | None) -> tuple[str, str]:
    """Harness-layer counterpart to `bind_user_quote()` — the actual fix for
    UPG-PROVENANCE-NEVER-RISES: on a live production corpus, `user_quote=`
    binding is a VOLUNTARY parameter the writing agent must remember to pass
    on every `remember()` call, and in practice it is essentially never
    passed — exactly the adoption gap hook-injected recall was already built
    to close for the READ side of memory (a caller choosing to call
    `vectr_recall` vs. notes simply arriving via the hook). This is the WRITE
    side of the same fix: a note earns `user-stated` provenance because the
    HARNESS mechanically checked it against what the user actually typed,
    never because the writer remembered to self-declare it.

    `bind_user_quote()` verifies a caller-declared EXCERPT is genuinely
    inside the note (excerpt ⊆ content) — the excerpt is presumed correct and
    the check confirms the note actually contains it. There is no
    caller-declared excerpt here, only the note's own CONTENT and
    RECENT_USER_MESSAGE (the raw, unmodified text of the most recently
    captured user turn in this workspace — see
    `VectrService._last_user_prompt`, populated only from the verbatim
    `UserPromptSubmit` hook payload, never from any interpretation of it), so
    the containment direction is the MIRROR: CONTENT ⊆ recent_user_message.
    The note's entire body must appear verbatim (whitespace-insensitive,
    case-sensitive — the same normalization `bind_user_quote()` uses) inside
    what the user actually typed. This is deliberately the strict, whole-body
    case rather than a fuzzy or partial match: a note that paraphrases,
    summarizes, or elaborates on the user's words correctly does NOT bind
    (stays at its ordinary provenance) — only a note that transcribes the
    user near-verbatim does, which is exactly the case the `user-stated`
    class exists for. No embedding, no similarity, no query-content
    inspection — content is compared against a captured PAST user message,
    never used to classify or reroute the CURRENT call.

    Returns `(bound_quote, rejection_reason)` exactly like
    `bind_user_quote()`. `bound_quote` is CONTENT itself (stripped) on a
    successful bind — the whole-content containment check makes content its
    own matching span, same as `bind_user_quote()` storing the caller's
    excerpt verbatim on success. Pure and total — no I/O, no clock; the
    caller is responsible for the recency check (comparing the captured
    turn's own timestamp against `auto_bind_max_age_seconds`) before ever
    passing RECENT_USER_MESSAGE in here, same separation of concerns as
    `bind_user_quote()` never touching the clock either."""
    recent = (recent_user_message or "").strip()
    stripped_content = (content or "").strip()
    if not stripped_content:
        return "", ""
    if not recent:
        return "", AUTO_QUOTE_NO_RECENT_TURN
    normalized_content = normalize_for_binding(stripped_content)
    if len(normalized_content) < MEMORY_WRITE_USER_QUOTE_MIN_CHARS:
        return "", AUTO_QUOTE_TOO_SHORT.format(min_chars=MEMORY_WRITE_USER_QUOTE_MIN_CHARS)
    if normalized_content not in normalize_for_binding(recent):
        return "", AUTO_QUOTE_NOT_CONTAINED
    return stripped_content, ""
