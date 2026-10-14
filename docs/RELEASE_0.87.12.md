# Agent CLI 0.87.12 — durable conversation recovery and session menus

## What changed

- Interactive TUI/Plain UI now reopens the latest conversation for the
  **same canonical workspace** instead of starting a blank logical session
  on every restart. Ctrl+N or /new still creates a new conversation.
- The TUI hydrates the persisted transcript before accepting input. This
  does not automatically execute unfinished mutations or repeat a failed
  tool call. Goal and contract resumption remain host-owned.
- /resume accepts a full or unambiguous 4+-character hexadecimal session
  prefix, regardless of age, and a 1-based numeric history index. It
  rejects ambiguous IDs and sessions outside the current workspace.
- Conversation picker now loads the latest 100 sessions; the /history
  and /resume commands remain available for older conversations.
- First user message titles each session for recognizable menu entries.
  Messages, including long task prompts, stay in the existing per-workspace
  SQLite database.
- Context compaction includes bounded beginning/end excerpts of oversized
  messages instead of silently dropping all large prompts. It does not
  assert that all bytes fit inside a 1200-token model context.
- Local user-turn history write failures fail visibly before dispatch to
  prevent an apparently saved task from disappearing.
- /fork hydrates the newly forked transcript; /resume only confirms a
  successful UI switch.

## Storage, privacy and recovery

Location: `<workspace>/.kitt/history/history.sqlite3` (WAL mode).
Messages are redacted before persistence. The complete retained original
is accessible with /history and /export. Reusing a different --root points
to a different history database. --no-history opts out; non-resume `-p`
invocations remain separate one-shot sessions. A failed/autonomous goal
must be resumed through /contract-resume or /goal-resume according to its
durable state; switching transcripts alone never marks tasks as executed.

## Testing

Unit/integration regressions cover cross-restart restoration, 60+ session
ID resume, workspace isolation, large user prompt retention and fork UI.
The remote CI validates supported Python and CLI surfaces. The root
installer needs its agent pin and downstream locks updated separately.

## Fail-closed history write validation

A user prompt must persist before the local model turn can start. If SQLite
fails, the TUI reports a turn-start error and does not dispatch the task.
If writing a completed assistant answer fails, the bridge emits TurnFailed
instead of reporting TurnCompleted. Regression tests verify both cases.

## Remembering explicit session selection

A user-chosen /resume session is touched in the same workspace SQLite DB,
so the next interactive invocation restores the selected older conversation
even if newer threads also exist. No global history leak or schema migration.

## Session switch safety

New Conversation and Fork are blocked while an active turn is running.
History Resume is likewise blocked until the turn reaches a terminal
state; this prevents switching the visible transcript away from the
actual durable execution owner.
