"""MCP tool dispatch — handle_tools_call and handle_tools_list."""
from __future__ import annotations

import os
from typing import Any

from agent.config import SYMBOL_NAME_PARAM_ALIASES
from integrations.mcp_server._schemas import (
    _EXPLORATION_TOOLS,
    _MEMORY_WRITE_TOOLS,
    _MEMORY_TOOLS,
    _UTILITY_TOOLS,
    MCP_TOOLS,
)
from integrations.mcp_server._session import (
    _memory_enabled_sessions,
    _session_calls_since_save,
    _increment_calls_since_save,
    _reset_calls_since_save,
    _should_nudge_remember,
    _remember_nudge_text,
    enable_memory_for_session,
    is_memory_enabled,
)


def _symbol_name_arg(arguments: dict) -> str:
    """Read the symbol-name argument for vectr_locate/vectr_trace, accepting
    SYMBOL_NAME_PARAM_ALIASES as drop-in aliases for the schema's "name" key
    (F40-class param ergonomics, UPG-TRACE-GRAPH-INCOMPLETE): a tool
    description that reads as a positional example trains a caller to guess
    a different key, and a required-arg error that never says which key is
    correct trains them to abandon the tool rather than retry. "name" wins
    when present alongside an alias."""
    name = arguments.get("name")
    if name:
        return str(name).strip()
    for alias in SYMBOL_NAME_PARAM_ALIASES:
        value = arguments.get(alias)
        if value:
            return str(value).strip()
    return ""


def handle_tools_list(session_id: str | None = None, service: Any = None) -> dict:
    """Return tools appropriate for this session.

    Always shown: exploration tools + vectr_remember + vectr_evict_hint.
    Gated on notes existing: vectr_recall, vectr_forget, vectr_snapshot, vectr_snapshot_list.
    """
    # Hosted/registry deployments (e.g. a catalog's containerised inspector)
    # start with an empty note store but must still advertise the complete
    # tool surface — the memory read tools would otherwise stay hidden until
    # the first note exists. Default-off; editor sessions are unaffected.
    if os.getenv("VECTR_MCP_ALL_TOOLS", "") == "1":
        return {
            "tools": _EXPLORATION_TOOLS + _MEMORY_WRITE_TOOLS + _UTILITY_TOOLS + _MEMORY_TOOLS
        }

    # Pre-enable memory read tools if notes already exist
    if session_id and service and not is_memory_enabled(session_id):
        try:
            notes_count = service.count_notes() if hasattr(service, "count_notes") else 0
            if notes_count > 0:
                enable_memory_for_session(session_id)
        except Exception:
            pass

    base = _EXPLORATION_TOOLS + _MEMORY_WRITE_TOOLS + _UTILITY_TOOLS
    if is_memory_enabled(session_id):
        return {"tools": base + _MEMORY_TOOLS}
    return {"tools": base}


def handle_tools_call(
    tool_name: str,
    arguments: dict,
    service: Any,
    session_id: str | None = None,
    client_label: str = "",
) -> dict:
    """Dispatch an MCP tool call. `service` is the VectrService instance.

    `client_label` (team mode) is the connecting client's attribution label,
    read from the `X-Vectr-Client` request header. It becomes the default note
    author when a `vectr_remember` call does not declare its own `agent`, and
    attributes every audit event this call triggers (search/index/recall too).
    """
    # Attribute audit events (opt-in audit log) to the connecting client. Set
    # unconditionally — including to "" — so a subsequent call in the same task
    # never inherits a previous call's label. Task-local via ContextVar, so
    # concurrent clients never cross-attribute.
    from agent.working_context_store import set_audit_client
    set_audit_client(client_label)

    # Count every tool call — used by the tool-call-count eviction trigger.
    # Reads the calling session's own advisor (UPG-EVICT-SESSION-SCOPE).
    try:
        service._advisor_for(session_id).increment_tool_call()
    except Exception:
        pass

    # Count per-tool calls for benchmark metrics (accurate across parent + sub-agents)
    try:
        service.increment_call_count(tool_name)
    except Exception:
        pass

    # Increment turn-count nudge counter for all tool calls
    _increment_calls_since_save(session_id)

    # Count retrieval-specific calls (search/locate/trace) for the retrieval-count trigger
    if tool_name in ("vectr_search", "vectr_locate", "vectr_trace"):
        try:
            service._advisor_for(session_id).increment_retrieval_call()
        except Exception:
            pass

    # ---- vectr_search ----
    if tool_name == "vectr_search":
        query = arguments.get("query", "")
        n_results = min(int(arguments.get("n_results", 5)), 50)
        language = arguments.get("language") or None

        if not query:
            return _mcp_error("query is required")

        # Memory-only mode: search is disabled for this workspace
        if getattr(service, "memory_only", False):
            from app.service import _MEMORY_ONLY_MSG
            return {"content": [{"type": "text", "text": _MEMORY_ONLY_MSG}], "isError": False}

        if service.total_chunks == 0:
            return {
                "content": [{"type": "text", "text": "Vectr is still indexing the codebase. Try again in a few moments."}],
                "isError": False,
            }

        results, query_ms = service.search(
            query, n_results=n_results, language=language
        )

        # Record the rendered chunks into the calling session's eviction
        # advisor (UPG-EVICT-SESSION-SCOPE) — the single recording site for
        # vectr_search, at render time, against the calling session only.
        try:
            service.record_results(results, session_id=session_id)
        except Exception:
            pass

        sections: list[str] = []

        # UPG-NOTFOUND-FLOOR (F46/F52), extended by UPG-SCORE-DISPLAY-FLAT: lead
        # with a low-confidence banner when either (a) the query names a
        # concept with no lexical anchor anywhere in the indexed corpus, or
        # (b) the top result's own absolute relevance score (below) is itself
        # below the configured floor. The per-result score is now an absolute
        # query-doc relevance value rather than a per-query rank-derived one,
        # so a caller can also read it directly — this banner remains the
        # explicit, hard-to-miss signal that the whole result set may be a
        # weak/unrelated guess. Results are still shown in full below it;
        # nothing is suppressed.
        if getattr(results, "low_confidence", False):
            from agent.config import NOTFOUND_FLOOR_BANNER
            sections.append(f"─── Low confidence ───\n{NOTFOUND_FLOOR_BANNER}")

        # UPG-3.1: a language filter that matched nothing because that language
        # isn't indexed (not merely a query miss) should tell the caller what IS
        # indexable, rather than silently returning empty.
        if language and not results:
            indexed = service.indexed_languages()
            if language.lower() not in {l.lower() for l in indexed}:
                avail = ", ".join(indexed) if indexed else "(none yet)"
                sections.append(
                    f"─── No results: language={language!r} is not indexed ───\n"
                    f"Indexed languages: {avail}.\n"
                    f"Re-run without the language filter, or use one of the above."
                )

        # L3 chunks
        sections.append(_format_search_results(results, query, query_ms, service.total_chunks))

        # UPG-QUERYTYPE-REROUTE: additive, high-precision symbol-graph hint —
        # exact identifier-shaped-token matches only (never a keyword/intent
        # match), appended BELOW the L3 results. Nothing is prepended,
        # reordered, or reweighted; empty when no identifier-shaped token in
        # the query resolves exactly.
        hint_symbols = service.identifier_hint_symbols(query)
        if hint_symbols:
            hint_lines = ["─── Symbol graph (exact matches for query identifiers) ───"]
            for s in hint_symbols:
                hint_lines.append(f"  [{s.kind}] {s.name}  {s.file_path}:{s.start_line}")
            sections.append("\n".join(hint_lines))

        # UPG-NEARMISS-SYMBOL-NAMES: additive, honestly-labeled follow-on —
        # for an identifier-shaped token that did NOT resolve exactly, show
        # the nearest existing symbol names so the caller learns it
        # misremembered a name instead of seeing nothing. Explicitly labeled
        # inexact ("No exact match ... nearest symbol names"), appended below
        # the exact-match section above; never reorders/replaces L3 results.
        nearmiss_pairs = service.identifier_hint_nearmiss(query)
        if nearmiss_pairs:
            nm_lines = []
            for token, syms in nearmiss_pairs:
                names = ", ".join(f"{s.name} ({s.file_path}:{s.start_line})" for s in syms)
                nm_lines.append(
                    f"─── No exact match for {token!r}; nearest symbol names: {names} ───"
                )
            sections.append("\n".join(nm_lines))

        content_text = "\n\n".join(sections)

        # auto-append eviction hint only on a FRESH context-pressure escalation
        # (UPG-7.1) — gated so it can't repeat on every response. Reads the
        # calling session's own advisor (UPG-EVICT-SESSION-SCOPE).
        # UPG-REMEMBER-BANNER-FATIGUE: at most one remember-related banner per
        # response — the eviction hint (when it fires) already tells the
        # caller to call vectr_remember, so the softer turn-count nudge only
        # runs when the eviction hint didn't.
        hint = service.auto_eviction_hint(session_id=session_id)
        if hint:
            content_text += f"\n\n─── Context management hint ───\n{hint}"
        elif _should_nudge_remember(session_id):
            content_text += _remember_nudge_text(session_id)

        return {"content": [{"type": "text", "text": content_text}], "isError": False}

    # ---- vectr_fetch ----
    if tool_name == "vectr_fetch":
        ids = arguments.get("ids")
        if not isinstance(ids, list) or not ids:
            return _mcp_error("ids is required (a non-empty list of chunk ids)")
        ids = [str(i) for i in ids]

        # Memory-only mode: no code index to fetch from — same guard as search/locate/trace.
        if getattr(service, "memory_only", False):
            from app.service import _MEMORY_ONLY_MSG
            return {"content": [{"type": "text", "text": _MEMORY_ONLY_MSG}], "isError": False}

        try:
            entries = service.fetch(ids)
        except ValueError as exc:
            return _mcp_error(str(exc))

        # Record fetched chunks into the calling session's eviction advisor —
        # mirrors the recording behaviour of vectr_search above.
        try:
            for e in entries:
                if e["found"]:
                    service.record_chunk(
                        file_path=e["file_path"],
                        lines=f"{e['start_line']}-{e['end_line']}",
                        symbol_name=e.get("symbol_name", ""),
                        content=e["content"],
                        chunk_id=e["id"],
                        session_id=session_id,
                    )
        except Exception:
            pass

        text = _format_fetch_results(entries)
        return {"content": [{"type": "text", "text": text}], "isError": False}

    # ---- vectr_status ----
    if tool_name == "vectr_status":
        status = service.status()
        notes_count = status.get("notes_count", 0)

        # if this session has prior notes, enable memory tools immediately
        if notes_count > 0:
            enable_memory_for_session(session_id)

        recall_hint = (
            f"  → call vectr_recall(query=...) to retrieve them"
            if notes_count > 0
            else "  → no prior notes; skip vectr_recall"
        )
        mode = status.get("mode", "full")
        lines = [
            "Vectr status",
            f"  Mode           : {mode}",
            f"  Indexed files  : {status['indexed_files']}",
            f"  Total chunks   : {status['total_chunks']}",
            f"  Symbols indexed: {status.get('symbol_count', 'n/a')}",
            f"  Prior notes    : {notes_count}{recall_hint}",
            f"  Last indexed   : {status['last_indexed']}",
            f"  Embed model    : {status['embed_model']}",
            f"  Workspace      : {status['workspace_root']}",
        ]
        if mode == "memory-only":
            lines.append(
                "  → memory-only mode: search/locate/trace are disabled; "
                "memory tools (remember/recall/snapshot) and hooks are active"
            )
        elif mode == "search-only":
            lines.append(
                "  → search-only mode: working-memory tools (remember/recall/forget/"
                "snapshot) are disabled; search, locate, trace and map are active"
            )

        # UPG-NOTES-EMBED-MIGRATION: surfaces a mid-failure state only — the
        # migration that keeps note vectors and the configured embed model in
        # sync runs synchronously at startup, so this is normally absent.
        notes_mismatch = status.get("notes_embed_model_mismatch")
        if notes_mismatch:
            lines.append(
                f"  WARNING        : working-memory notes are stamped with embed "
                f"model {notes_mismatch!r} but {status['embed_model']!r} is "
                "configured — semantic recall ranking may be degraded until "
                "migration completes on the next restart"
            )

        # Per-language coverage + symbol availability (UPG-3.3). Tells the agent
        # where locate/trace will work (symbol graph) vs. where to use search only.
        langs = status.get("languages") or []
        if langs:
            lines.append("  Languages      : (✓ = locate/trace available; others are search-only)")
            for L in langs[:8]:
                mark = "✓ locate/trace" if L["symbols"] else "search-only"
                lines.append(f"      {L['language']:<12} {L['files']:>5} files   {mark}")
            if len(langs) > 8:
                lines.append(f"      … +{len(langs) - 8} more")
            top = langs[0]
            if not top["symbols"]:
                lines.append(
                    f"  → Primary language ({top['language']}) has no symbol graph — "
                    "prefer vectr_search; locate/trace will be empty here."
                )
        # Warn when declared grammars are not importable in this environment.
        grammars_unavailable = status.get("grammars_unavailable") or []
        if grammars_unavailable:
            lines.append(
                f"  WARNING        : tree-sitter grammar(s) not importable: "
                f"{', '.join(grammars_unavailable)} — locate/trace disabled for these; "
                "reinstall vectr deps (pip install -e .) to enable"
            )
        if status.get("semantic_weight") is not None:
            lines.append(
                f"  Retrieval      : semantic={status['semantic_weight']:.0%}  "
                f"bm25={status['bm25_weight']:.0%}  "
                f"graph_first={status['graph_first']}"
            )
            if status.get("strategy_rationale"):
                lines.append(f"  Strategy why   : {status['strategy_rationale']}")

        # Watcher backlog (UPG-WATCHER-PRESSURE-GOVERNOR): only surfaced when
        # there is something to report, so a quiet workspace's status stays terse.
        pending_files = status.get("watcher_pending_files", 0)
        burst_mode = status.get("watcher_burst_mode", False)
        batch_running = status.get("watcher_batch_running", False)
        if pending_files or burst_mode or batch_running:
            last_ms = status.get("watcher_last_batch_duration_ms", 0)
            lines.append(
                f"  Watcher        : {pending_files} file(s) pending"
                f"{' (burst mode)' if burst_mode else ''}"
                f"{' — batch running' if batch_running else ''}"
                f"{f', last batch {last_ms}ms' if last_ms else ''}"
            )

        # Hook injection counters (UPG-HOOK-INJECT-OBSERVABILITY): only shown
        # when at least one hook has actually injected notes, so a workspace
        # with no Claude Code hooks installed (or whose hooks haven't fired
        # yet) stays terse — same quiet-when-zero pattern as the watcher line
        # above. Without this line, hook injection is invisible: notes land
        # silently in the model's context and there's no way to tell a
        # working memory system from a dead one.
        hook_counts = status.get("hook_injection_counts") or {}
        if hook_counts:
            hook_parts = ", ".join(f"{kind} {n}" for kind, n in hook_counts.items())
            lines.append(f"  Hook injections: {hook_parts}")

        # inject adaptive instruction style hint at session start
        try:
            style = service.suggest_instruction_style()
            style_hints = {
                "additive":    "Use vectr tools when they'd be faster than reading files — see CLAUDE.md.",
                "directed":    "This is a large/unfamiliar codebase. Use vectr_map → vectr_search → vectr_locate before reading files.",
                "memory-only": "Prior notes exist. Call vectr_recall(query=...) first; use search only to fill gaps.",
            }
            hint = style_hints.get(style, "")
            if hint:
                lines.append(f"  Tool style     : [{style}] {hint}")
        except Exception:
            pass

        text = "\n".join(lines)
        return {"content": [{"type": "text", "text": text}], "isError": False}

    # ---- vectr_map ----
    if tool_name == "vectr_map":
        text = service.get_map()
        return {"content": [{"type": "text", "text": text}], "isError": False}

    # ---- vectr_map_save ----
    if tool_name == "vectr_map_save":
        summary = arguments.get("summary", "").strip()
        if not summary:
            return _mcp_error("summary is required")
        overwrite = bool(arguments.get("overwrite", False))
        result = service.save_map(summary, overwrite=overwrite)
        if not result["saved"]:
            return {
                "content": [{"type": "text", "text": (
                    "A passport already exists for this workspace — not overwritten. "
                    "Call vectr_map_save again with overwrite=true to replace it. "
                    "Existing summary:\n\n"
                    f"{result['existing_summary']}"
                )}],
                "isError": False,
            }
        return {
            "content": [{"type": "text", "text": (
                f"Passport saved ({len(summary)} chars). "
                "Future vectr_map calls will return this summary instantly."
            )}],
            "isError": False,
        }

    # ---- vectr_locate ----
    if tool_name == "vectr_locate":
        name = _symbol_name_arg(arguments)
        if not name:
            return _mcp_error("name is required")

        # Memory-only mode: locate is disabled for this workspace
        if getattr(service, "memory_only", False):
            from app.service import _MEMORY_ONLY_MSG
            return {"content": [{"type": "text", "text": _MEMORY_ONLY_MSG}], "isError": False}

        limit = int(arguments.get("limit", 10))
        caller_file = arguments.get("caller_file", "").strip() or None
        symbols = service.locate_with_snippets(name, limit=limit, caller_file=caller_file)
        text = service.format_locate(symbols, name)

        # Record the rendered snippets into the calling session's eviction
        # advisor (UPG-EVICT-SESSION-SCOPE) — same render-time recording
        # contract as vectr_search. No chunk_id: a symbol's line range is not
        # guaranteed to match a stored chunk boundary, so vectr_fetch is never
        # advertised for these (would risk a re-fetch key that doesn't work).
        try:
            rendered_symbols = getattr(symbols, "symbols", None)
            if rendered_symbols is None and isinstance(symbols, list):
                rendered_symbols = symbols
            for s in rendered_symbols or []:
                if getattr(s, "snippet", ""):
                    service.record_chunk(
                        file_path=s.file_path,
                        lines=f"{s.start_line}-{s.end_line}",
                        symbol_name=s.name,
                        content=s.snippet,
                        session_id=session_id,
                    )
        except Exception:
            pass

        # UPG-REMEMBER-BANNER-FATIGUE: at most one remember-related banner.
        hint = service.auto_eviction_hint(session_id=session_id)  # UPG-7.1: gated, not every response
        if hint:
            text += f"\n\n─── Context management hint ───\n{hint}"
        elif _should_nudge_remember(session_id):
            text += _remember_nudge_text(session_id)
        return {"content": [{"type": "text", "text": text}], "isError": False}

    # ---- vectr_trace ----
    if tool_name == "vectr_trace":
        name = _symbol_name_arg(arguments)
        if not name:
            return _mcp_error("name is required")

        # Memory-only mode: trace is disabled for this workspace
        if getattr(service, "memory_only", False):
            from app.service import _MEMORY_ONLY_MSG
            return {"content": [{"type": "text", "text": _MEMORY_ONLY_MSG}], "isError": False}

        direction = arguments.get("direction", "both")
        if direction not in ("callers", "callees", "both"):
            direction = "both"
        limit = int(arguments.get("limit", 20))
        include_builtins = bool(arguments.get("include_builtins", False))
        trace_result = service.trace_with_snippets(
            name, direction=direction, limit=limit, include_builtins=include_builtins
        )
        text = service.format_trace(trace_result, name)
        # UPG-REMEMBER-BANNER-FATIGUE: at most one remember-related banner.
        hint = service.auto_eviction_hint(session_id=session_id)  # UPG-7.1: gated, not every response
        if hint:
            text += f"\n\n─── Context management hint ───\n{hint}"
        elif _should_nudge_remember(session_id):
            text += _remember_nudge_text(session_id)
        return {"content": [{"type": "text", "text": text}], "isError": False}

    # ---- vectr_remember ----
    if tool_name == "vectr_remember":
        content = arguments.get("content", "").strip()
        if not content:
            return _mcp_error("content is required")

        # Search-only mode: the working-memory layer is disabled for this workspace
        if getattr(service, "search_only", False):
            from app.service import _SEARCH_ONLY_MSG
            return {"content": [{"type": "text", "text": _SEARCH_ONLY_MSG}], "isError": False}

        tags = arguments.get("tags") or None
        priority = arguments.get("priority", "medium")
        if priority not in ("high", "medium", "low"):
            priority = "medium"
        kind = arguments.get("kind", "finding")
        title = arguments.get("title", "") or ""
        # UPG-SUBAGENT-MEMORY: optional caller-declared agent/subagent
        # identifier for multi-agent shared-memory attribution — never inferred.
        # In team mode, fall back to the connecting client's label (X-Vectr-Client
        # header) when the call itself does not declare an agent.
        agent = (arguments.get("agent", "") or "") or client_label
        note_id = service.remember(content=content, tags=tags, priority=priority, kind=kind, title=title, agent=agent)
        # reset the turn-count nudge, the eviction advisor's remember-fatigue
        # counter (UPG-REMEMBER-BANNER-FATIGUE), and enable memory tools
        _reset_calls_since_save(session_id)
        service.note_remembered(session_id=session_id)
        enable_memory_for_session(session_id)
        return {
            "content": [{"type": "text", "text": f"Stored note #{note_id}. Recall with vectr_recall — <50ms, verbatim, any time."}],
            "isError": False,
        }

    # ---- vectr_recall ----
    if tool_name == "vectr_recall":
        # Search-only mode: the working-memory layer is disabled for this workspace
        if getattr(service, "search_only", False):
            from app.service import _SEARCH_ONLY_MSG
            return {"content": [{"type": "text", "text": _SEARCH_ONLY_MSG}], "isError": False}

        query = arguments.get("query") or None
        tags = arguments.get("tags") or None
        priority = arguments.get("priority") or None
        kind = arguments.get("kind") or None
        boot = bool(arguments.get("boot", False))
        limit = int(arguments.get("limit", 10))
        detail = arguments.get("detail", "index") or "index"
        sort_by = arguments.get("sort_by", "relevance") or "relevance"
        max_age_days = arguments.get("max_age_days") or None
        if max_age_days is not None:
            try:
                max_age_days = float(max_age_days)
            except (TypeError, ValueError):
                max_age_days = None
        note_id_arg = arguments.get("note_id") or None
        if note_id_arg is not None:
            try:
                note_id_arg = int(note_id_arg)
            except (TypeError, ValueError):
                note_id_arg = None
        text = service.recall(
            query=query, tags=tags, priority=priority, limit=limit, kind=kind, boot=boot,
            detail=detail, sort_by=sort_by, max_age_days=max_age_days, note_id=note_id_arg,
        )
        hint = service.auto_eviction_hint(session_id=session_id)  # UPG-7.1: gated, not every response
        if hint:
            text += f"\n\n─── Context management hint ───\n{hint}"
        return {"content": [{"type": "text", "text": text}], "isError": False}

    # ---- vectr_evict_hint ----
    if tool_name == "vectr_evict_hint":
        hint = service.eviction_hint(session_id=session_id)
        if not hint:
            hint = "No retrieved chunks to evict. Context window is clean."
        return {"content": [{"type": "text", "text": hint}], "isError": False}

    # ---- vectr_snapshot ----
    if tool_name == "vectr_snapshot":
        label = arguments.get("label", "").strip()
        if not label:
            return _mcp_error("label is required")

        # Search-only mode: the working-memory layer is disabled for this workspace
        if getattr(service, "search_only", False):
            from app.service import _SEARCH_ONLY_MSG
            return {"content": [{"type": "text", "text": _SEARCH_ONLY_MSG}], "isError": False}

        # UPG-EVICT-SESSION-SCOPE: an explicit "session_id" argument (e.g. a
        # multi-agent caller labeling a shared snapshot) overrides the calling
        # MCP session; otherwise default to the transport-level session_id so
        # the snapshot captures THIS session's own retrieved chunks rather than
        # the anonymous shared advisor's (near-always empty for a real session).
        snapshot_session_id = arguments.get("session_id") or session_id
        snapshot_id = service.snapshot_session(label=label, session_id=snapshot_session_id)
        return {
            "content": [{"type": "text", "text": f"Snapshot saved: {snapshot_id}\nLabel: {label}\nNotes are available via vectr_recall any time — later in this session or in future sessions."}],
            "isError": False,
        }

    # ---- vectr_snapshot_list ----
    if tool_name == "vectr_snapshot_list":
        # Search-only mode: the working-memory layer is disabled for this workspace
        if getattr(service, "search_only", False):
            from app.service import _SEARCH_ONLY_MSG
            return {"content": [{"type": "text", "text": _SEARCH_ONLY_MSG}], "isError": False}

        snapshots = service.list_snapshots()
        if not snapshots:
            text = "No snapshots saved for this workspace yet. Use vectr_snapshot to save one."
        else:
            import datetime
            lines = [f"Saved snapshots ({len(snapshots)}):\n"]
            for s in snapshots:
                ts = datetime.datetime.fromtimestamp(s["created_at"]).strftime("%Y-%m-%d %H:%M")
                lines.append(f"  {s['snapshot_id']}  [{ts}]  {s['label']}")
            lines.append("\nUse vectr_recall to retrieve the notes from any session.")
            text = "\n".join(lines)
        return {"content": [{"type": "text", "text": text}], "isError": False}

    # ---- vectr_forget ----
    if tool_name == "vectr_forget":
        # Search-only mode: the working-memory layer is disabled for this workspace
        if getattr(service, "search_only", False):
            from app.service import _SEARCH_ONLY_MSG
            return {"content": [{"type": "text", "text": _SEARCH_ONLY_MSG}], "isError": False}

        note_id = arguments.get("note_id")
        if note_id is not None:
            try:
                nid = int(note_id)
            except (TypeError, ValueError):
                return _mcp_error("note_id must be an integer (the [#N] id shown by vectr_recall)")
            if service.forget_note(nid):
                return {
                    "content": [{"type": "text", "text": f"Deleted note #{nid}."}],
                    "isError": False,
                }
            return {
                "content": [{"type": "text", "text": f"Note #{nid} not found — nothing deleted."}],
                "isError": False,
            }
        if arguments.get("all") is True:
            deleted = service.forget_all()
            return {
                "content": [{"type": "text", "text": f"Deleted {deleted} working-memory notes. Starting fresh."}],
                "isError": False,
            }
        # No arguments must never destroy data: require an explicit target.
        return _mcp_error(
            "Pass note_id=<N> to delete one note, or all=true to clear every note for this workspace."
        )

    # ---- vectr_ingest_traces ----
    if tool_name == "vectr_ingest_traces":
        events = arguments.get("events")
        if not isinstance(events, list):
            return _mcp_error("events must be a list of trace event dicts")
        result = service.ingest_traces(events)
        text = (
            f"Ingested {result['ingested']} dynamic call edges into the symbol graph. "
            f"({result['skipped_invalid']} events skipped — missing caller or callee field.) "
            "Dynamic edges now appear in vectr_trace results (marked \"(dynamic)\") "
            "alongside static edges."
        )
        # UPG-7.3: an unresolved caller/callee is not an error (the target may be
        # external, runtime-only, or a symbol kind the static extractor doesn't
        # capture) but is worth surfacing — it may also be a typo'd trace event.
        unresolved_callers = result.get("unresolved_callers", 0)
        unresolved_callees = result.get("unresolved_callees", 0)
        if unresolved_callers or unresolved_callees:
            text += (
                f"\n\nWarning: {unresolved_callers} caller name(s) and "
                f"{unresolved_callees} callee name(s) did not match any symbol in "
                "the indexed graph — edges were still ingested, but double-check "
                "these aren't typos:"
            )
            for example in result.get("unresolved_examples", []):
                text += f"\n  {example}"
        return {
            "content": [{"type": "text", "text": text}],
            "isError": False,
        }

    return _mcp_error(f"Unknown tool: {tool_name}")


def _storage_cap_truncation_warning(
    content: str, file_path: str, symbol_start_line: int, symbol_end_line: int,
) -> str | None:
    """Detect index-time storage-cap truncation and return the Read() fallback
    warning, or None when the content is complete.

    A symbol chunk that exceeds the indexer's per-chunk cap (large classes/
    methods — see agent/indexer/_chunking.py) is stored capped: fewer lines
    than the symbol's own recorded `symbol_start_line`/`symbol_end_line` span.
    Detected by comparing the rendered content's line count against that span
    (UPG-11.4-b / UPG-FETCH-TRUNCATION-SILENT). No vectr_fetch(...) can
    recover the missing tail — the index itself holds only the capped
    content — so the fallback points at a direct file read reaching it.
    Shared by every surface that renders a chunk's content as prose
    (_format_search_results, _format_fetch_results) so a truncated chunk
    never appears complete on one surface and flagged on another.
    """
    content_lines = content.splitlines()
    symbol_range_lines = (
        symbol_end_line - symbol_start_line + 1
        if (symbol_start_line and symbol_end_line and symbol_end_line > symbol_start_line)
        else 0
    )
    if symbol_range_lines > 0 and len(content_lines) < symbol_range_lines - 5:
        missing = symbol_range_lines - len(content_lines)
        return (
            f"... {missing} more lines (content capped at ~2000 chars) — "
            f"Read({file_path!r}, offset={symbol_start_line - 1}, limit={symbol_range_lines}) for full definition"
        )
    return None


def _format_search_results(results, query: str, query_ms: int, chunks_searched: int) -> str:
    if not results:
        return f"No results found for: {query}"
    lines = [f"Found {len(results)} results for '{query}' ({query_ms}ms, {chunks_searched} chunks searched)\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"{'─' * 60}")
        dup = f"  (+{r.dup_count} more identical)" if getattr(r, "dup_count", 0) else ""
        chunk_id = getattr(r, "chunk_id", "") or f"{r.file_path}:{r.lines}"
        lines.append(f"[{i}] {chunk_id}  score {r.score:.3f}{dup}")
        if r.symbol_name:
            # UPG-11.4: include symbol line-range so the caller knows the full
            # definition's extent even when the displayed content is capped —
            # vectr_fetch(ids=[chunk_id]) below restores it in full if needed.
            sym_range = ""
            s_start = getattr(r, "symbol_start_line", 0)
            s_end = getattr(r, "symbol_end_line", 0)
            if s_start and s_end:
                sym_range = f"  [lines {s_start}–{s_end}]"
            lines.append(f"    symbol: {r.symbol_name}{sym_range}  language: {r.language}")
        lines.append("")
        content_lines = r.content.splitlines()
        # UPG-11.4-b: emit an expand hint when the stored content was truncated
        # by the 2000-char cap (searcher stores content[:2000], ~48 lines for
        # dense methods) — see _storage_cap_truncation_warning().
        # UPG-CTX-EVICT: the display-cap expand hint points at
        # vectr_fetch(ids=[...]) — vectr_fetch returns the chunk's FULL stored
        # content (no 80-line display cap) and keeps the caller in the vectr
        # tool family. The STORAGE-cap branch below must NOT use it: a chunk
        # capped at ~2000 chars at index time is stored capped, so vectr_fetch
        # would return the same truncated content — only a file read reaches
        # the missing tail.
        s_start = getattr(r, "symbol_start_line", 0)
        s_end = getattr(r, "symbol_end_line", 0)
        truncation_warning = _storage_cap_truncation_warning(r.content, r.file_path, s_start, s_end)
        if len(content_lines) > 80:
            # Hard cap: the content itself is long but we also cap the display.
            lines.append("\n".join(content_lines[:80]))
            lines.append(
                f"... {len(content_lines) - 80} more lines — "
                f"vectr_fetch(ids=[{chunk_id!r}]) restores the full chunk"
            )
        elif truncation_warning:
            # Content was silently capped by the 2000-char storage limit before
            # it reached the full symbol body.  Show what we have, then prompt.
            lines.append(r.content)
            lines.append(truncation_warning)
        else:
            lines.append(r.content)
        lines.append("")
    lines.append(
        'Results are re-fetchable anytime: vectr_fetch(ids=["<id>"]) restores '
        "a chunk after it leaves your context."
    )
    return "\n".join(lines)


def _format_fetch_results(entries: list[dict]) -> str:
    """Render vectr_fetch results using the same id + symbol + content
    conventions as _format_search_results (UPG-CTX-EVICT), so a restored
    chunk reads identically to how it first appeared in a search response.

    UPG-FETCH-TRUNCATION-SILENT: a chunk stored incomplete because of the
    indexer's per-chunk storage cap (see _storage_cap_truncation_warning) is
    STILL stored incomplete when re-fetched by id — vectr_fetch restores the
    exact stored bytes, not the original file. Apply the same truncation
    check search already applies so a re-fetch never silently looks complete
    on exactly the large chunks a caller is most tempted to evict.
    """
    lines: list[str] = []
    missing = [e["id"] for e in entries if not e["found"]]
    for e in entries:
        lines.append(f"{'─' * 60}")
        if not e["found"]:
            lines.append(f"[{e['id']}] not found")
            lines.append("")
            continue
        lines.append(f"[{e['id']}]")
        if e.get("symbol_name"):
            lines.append(f"    symbol: {e['symbol_name']}  language: {e.get('language', '')}")
        lines.append("")
        content = e.get("content", "")
        lines.append(content)
        truncation_warning = _storage_cap_truncation_warning(
            content, e.get("file_path", ""), e.get("start_line", 0), e.get("end_line", 0),
        )
        if truncation_warning:
            lines.append(truncation_warning)
        lines.append("")
    if missing:
        from app.service import _FETCH_NOT_FOUND_NOTE
        lines.append(_FETCH_NOT_FOUND_NOTE)
    return "\n".join(lines)


def _mcp_error(message: str) -> dict:
    return {
        "content": [{"type": "text", "text": f"Error: {message}"}],
        "isError": True,
    }
