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
