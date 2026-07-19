# Data handling policy

What vectr stores, where it lives, how long it is kept, and how to delete it.
Everything below is local to the machine running the vectr daemon — vectr makes
no outbound network calls except the optional cloud-embedding APIs you
explicitly configure (`openai/...` or Voyage models), and never transmits your
index, notes, or logs anywhere.

## What is stored, and where

| Data | Contents | Location |
|---|---|---|
| Code index | Embedding vectors, **verbatim chunk text of your source**, file paths, line ranges (ChromaDB: SQLite + HNSW) | `~/.cache/vectr/<workspace-hash>/chroma/` |
| Working-memory notes | Free text saved via `vectr_remember` — findings, decisions, whatever the agent wrote | `~/.cache/vectr/<workspace-hash>/working_context.sqlite` |
| Session snapshots | Copies of note contents + retrieved-chunk metadata, sealed via `vectr_snapshot` | same SQLite file, `snapshots` table |
| Symbol graph | Symbol names, definitions, call edges derived from your source | `~/.cache/vectr/<workspace-hash>/` |
| Codebase passport | The plain-English codebase summary saved by `vectr_map_save` | `~/.cache/vectr/<workspace-hash>/` |
| Instance registry | Workspace path, port, PID per running daemon | `~/.vectr/instances.json` |
| Daemon logs | Startup/indexing log lines (no query text) | `~/.vectr/logs/<workspace-hash>.log` |
| Audit log (**opt-in**) | Index/search/remember/recall/forget events — **including query text** and, in team mode, the client label | the path you set in `VECTR_AUDIT_LOG` (unset = nothing is recorded) |
| API key (**opt-in** auth / team mode) | The shared key, embedded as an `X-Api-Key` header value in **plaintext** | `.mcp.json`, `.cursor/mcp.json`, `.vscode/mcp.json` in the workspace — treat these files as secrets and keep them out of shared or public version control; the daemon itself never persists the key |

The cache (`~/.cache/vectr/`) and state (`~/.vectr/`) directories are created
owner-only (`0700`) on POSIX systems, so other accounts on a shared machine
cannot read them at the filesystem level.

## What is plaintext vs encrypted

By default, everything above is plaintext on disk. With encryption enabled
(`VECTR_ENCRYPT_KEY`, or a passphrase stored in the OS keychain under service
`vectr` / username `encrypt-key`):

- **Encrypted:** note content, note titles, snapshot payloads (Fernet:
  AES-128-CBC + HMAC-SHA256; PBKDF2-SHA256 key derivation, 480k iterations).
- **Still plaintext:** the code index (chunk text and vectors — the search
  engine needs them readable; protect the index with OS full-disk encryption),
  the symbol graph, the passport, note tags and metadata (timestamps, priority,
  kind, author), and note embedding vectors — a lossy numeric projection of
  note content kept for semantic recall. Set
  `VECTR_ENCRYPT_DISABLE_NOTE_VECTORS=1` to omit note vectors entirely
  (recall falls back to exact-text matching).

Vectr never claims more than this. If an attacker can read your disk, the code
index is readable; `VECTR_ENCRYPT_KEY` protects the *notes*, not the index.

## How long data is kept

Indefinitely, until you delete it — with two exceptions:

- **Note TTL (opt-in):** set `VECTR_NOTES_TTL_DAYS=<n>` and the daemon deletes
  notes older than *n* days at startup. Unset = notes never expire.
- **Audit log rotation:** when enabled, the audit log rotates at 10 MB and
  keeps 3 backups (~40 MB ceiling).

Re-indexing replaces stale index entries for changed files; deleting a file
removes its chunks on the next index pass or watcher event.

## How to delete data

| What you want gone | How |
|---|---|
| One note | `vectr_forget(note_id=N)` (MCP) or `POST /v1/forget {"note_id": N}` |
| All notes + snapshots + note vectors for a workspace | `vectr_forget(all=true)` (MCP), `POST /v1/forget {"all": true}`, or `POST /v1/memory/clear` |
| All notes + snapshots + note vectors across every workspace | `vectr forget --all` (CLI; operates directly on the store, daemon not required) |
| The code index for a workspace | delete `~/.cache/vectr/<workspace-hash>/chroma/`, or the whole workspace hash directory |
| Everything vectr has ever stored | stop the daemons (`vectr stop --all`), then delete `~/.cache/vectr/` and `~/.vectr/` |

"Delete everything" means everything: the forget-all paths remove notes,
snapshots (which embed note contents), and the note embedding vectors — not
just the notes table.

## Team mode (central instance)

When one daemon serves multiple clients (`vectr start --host ...` + `vectr
connect`), all of the above lives on the **server host** under the account
running the daemon. Every key-holder can read the shared workspace's index and
notes by design; the server operator can read everything. Client labels
(`--label`) attribute notes and audit lines but are self-declared — they are
collaboration metadata, not an identity or access-control mechanism.

Note IDs come from a single central sequence assigned in write-arrival order, so
concurrent clients interleave: a given client's notes are not contiguous, and
the number of the next note is whoever writes next, not necessarily you. Treat
the `Stored note #N` value returned by each write as the canonical reference to
that note — never infer a client's notes from an ID range.
