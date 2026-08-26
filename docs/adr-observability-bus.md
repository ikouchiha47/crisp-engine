# ADR: Crisp Observability Bus

**Status:** Proposed
**Date:** 2026-08-23

---

## Context

The hook pipeline is a black box. Events fire, watchers may or may not match,
episodes may or may not save, embeddings may or may not succeed. There is no
feedback loop short of reading `~/.cache/crisp/crisp.log` manually. Users
cannot tell whether the system is working at all.

We need real-time observability without compromising the one hard constraint:
**the hook execution path must never block on the observer.**

This is the same constraint statsd, Prometheus client libs, and OpenTelemetry
all solve: the hot path emits and returns; a separate process or thread handles
persistence and delivery.

---

## Decision

### Emission model: fire-and-forget into an in-process ring buffer

`lib/bus.py` exposes a single call site:

```python
bus.emit("episode_saved", {"id": ep.id, "layer": ep.layer, ...})
```

Internally this does one thing: `_deque.append(event)`. That is a single
C-level call under the GIL. It never touches disk, never acquires a lock the
caller cares about, never blocks.

`collections.deque(maxlen=500)` is the ring buffer. When full, the oldest
event is silently dropped -- the same behavior as a statsd UDP packet loss.
The hook path is unaffected.

A daemon background thread (started once at first `emit()`) drains the deque
every 200ms and batch-writes to SQLite via `executemany`. If the write fails
the thread logs and retries next cycle. If the thread crashes the deque keeps
filling and dropping -- the hook path still returns immediately.

Because the thread is `daemon=True` it dies with the process. No orphaned
writers. On clean exit CPython gives daemon threads ~100ms to flush; the
background thread is fast enough to empty a 500-event backlog in one cycle.

### Durability model: WAL SQLite, append-only events table

The event store is a dedicated SQLite file separate from the main memory store:

```
~/.cache/crisp/events.db
```

Schema:

```sql
CREATE TABLE IF NOT EXISTS events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT    NOT NULL,           -- ISO-8601 with ms
    event     TEXT    NOT NULL,           -- event_type string
    session   TEXT,                       -- session_id if known
    project   TEXT,                       -- project root basename
    payload   TEXT    NOT NULL            -- JSON blob
);
```

Clients tail with:

```sql
SELECT id, ts, event, session, project, payload
FROM events
WHERE id > :last_seen
ORDER BY id
LIMIT 100;
```

Pure read, never competes with the writer under WAL.

Ring buffer semantics in SQLite: a periodic housekeeping query deletes rows
older than 7 days (or beyond 50k rows). Run on connection open, not per-write.

### SQLite pragmas

Set on every connection open (both writer and reader connections):

```sql
PRAGMA journal_mode    = WAL;
PRAGMA synchronous     = NORMAL;
PRAGMA cache_size      = -64000;   -- 64 MB page cache
PRAGMA temp_store      = MEMORY;
PRAGMA mmap_size       = 30000000; -- 30 MB mmap
PRAGMA busy_timeout    = 5000;     -- 5s retry on SQLITE_BUSY
PRAGMA wal_autocheckpoint = 5000;  -- checkpoint every ~20 MB, not every 4 MB
PRAGMA page_size       = 4096;     -- set before first write only
```

Rationale per pragma:

| Pragma | Value | Why |
|---|---|---|
| `journal_mode=WAL` | WAL | readers never block writers; commits are batched at checkpoint not per-write |
| `synchronous=NORMAL` | NORMAL | safe in WAL mode -- WAL is synced before checkpoint, not per-commit; ~10-20x faster than FULL |
| `cache_size=-64000` | 64 MB | default 2 MB causes excessive I/O on append bursts |
| `temp_store=MEMORY` | MEMORY | tail-read sorts and temp indices stay in RAM |
| `mmap_size=30000000` | 30 MB | cuts syscall overhead for sequential scans by TUI/web clients |
| `busy_timeout=5000` | 5000 ms | retries on SQLITE_BUSY from checkpoint contention instead of failing |
| `wal_autocheckpoint=5000` | 5000 pages | ~20 MB between checkpoints; default 1000 pages checkpoints too aggressively for high-frequency appends |
| `page_size=4096` | 4096 B | modern FS block alignment; must be set before first write |

Also run `PRAGMA optimize` on connection close (housekeeping path only) so
SQLite keeps its query planner stats fresh.

### Event types emitted

| event | emitted by | key payload fields |
|---|---|---|
| `hook_fired` | `hooks.py main()` | agent, event_type, session_id, project |
| `watcher_matched` | `WatcherRegistry.run()` | watcher_name, tool_name, episode_count |
| `watcher_skipped` | `WatcherRegistry.run()` | watcher_name, tool_name |
| `episode_saved` | `hooks._save()` | id, layer, category, importance, embedded |
| `embed_result` | `hooks._embed()` | provider, success, fallback_used, episode_id |
| `reflect_ran` | `reflector` | l0_in, l1_created, l2_created |
| `instinct_ran` | instinct distiller | patterns_found, promoted |
| `error` | anywhere | source, message |

### Client architecture: core separate from display

```
lib/bus.py          -- emit(), tail(), EventBus (no UI deps, no server deps)
lib/monitor/
  common.py         -- shared event formatting, color codes
  tui.py            -- Textual app; polls bus.tail() on a timer
  server.py         -- FastAPI + SSE; streams bus.tail() to browser
```

`lib/bus.py` has zero imports beyond `collections`, `threading`, `sqlite3`,
`json`, `datetime`. It can be imported anywhere without pulling in Textual or
FastAPI.

### Client 1: TUI (`crisp monitor` or `crisp monitor --tui`)

Textual app with three panels:
- **Live feed** -- scrolling event stream, color by event type, newest at bottom
- **Stats sidebar** -- watcher hit counts, episode saves per layer, embed
  success/fallback/fail rates, all scoped to the current session
- **Recent episodes** -- last 10 saved episodes with layer/category/importance

Polls `bus.tail(since_id)` every 500ms on a Textual worker timer.

### Client 2: Web dashboard (`crisp monitor --web [--port 7654]`)

FastAPI serves one HTML page (no JS framework, bundled inline). An `EventSource`
connects to `/stream` which is an SSE endpoint yielding `bus.tail()` results as
`data: <json>\n\n` frames. Same data as the TUI. Useful when Textual is
unavailable or the terminal doesn't support it.

---

## Consequences

**Good:**
- Hook path overhead is one `deque.append` -- unmeasurable
- System keeps working if no client is connected -- events drain from the
  deque into SQLite, or drop silently when full, like UDP statsd
- WAL + NORMAL synchronous means writes are fast and readers are never blocked
- Core (`bus.py`) has no dependencies; clients are optional extras
- Events persist in SQLite for post-mortem analysis even after the session ends

**Bad / accepted:**
- Up to 500 events can be lost on unclean process kill (SIGKILL) -- acceptable,
  this is observability data not business data
- 200ms flush latency in the TUI -- fine for human-readable monitoring
- SQLite is not appropriate if multiple machines need to read events -- accepted,
  this is a local dev tool

---

## Alternatives considered

**Unix socket / named pipe:** lower latency but requires the client to be
running when events fire. If the TUI is closed, events are lost or the writer
blocks. Rejected.

**Redis pub/sub:** fast and multi-consumer but requires a Redis process. Adds
infra for a local dev tool. Rejected.

**Direct SQLite write on the hook path:** simple but blocks on disk I/O and
WAL checkpoint contention. Rejected -- the daemon thread model costs almost
nothing and eliminates the blocking risk.

**Structured log parsing (tail crisp.log):** already exists but logs are
human-formatted, lossy, and not queryable. The event bus replaces the need to
parse logs for monitoring.
