# 2026-11-04 — Voice mode B7: clean filler words in the rewrite step

Plan: `docs/plan/voice-mood/voice-mode-v2-01-backend-plan.md`, step B7 (Stage 3).
Follows `6445853` (B6).

## What changed

- `chats/services/rag/prompts.py`
  - The rewrite system prompt is split into `_REWRITE_INTRO_AND_RULES` (intro + the
    four shared rules) and a last-rules piece; the human message is
    `_REWRITE_HUMAN`.
  - `query_rewriter_prompt` = intro + `_TEXT_REWRITE_LAST_RULE` ("If the question is
    already standalone, return it as-is"). Renders byte-for-byte as before.
  - New `voice_query_rewriter_prompt` = intro + `_VOICE_REWRITE_LAST_RULES`: remove
    filler words (um, uh, like, you know), repeated words and false starts, keep the
    meaning; a standalone question is returned as-is **but still without fillers**.
- `chats/services/rag/query_rewriter.py`
  - New `voice_query_rewrite_chain`.
  - `get_rewritten_query(original_query, chat_history, mode: ChatMessage.Mode)`: voice
    → always `voice_query_rewrite_chain` (also with no history); text → unchanged
    (no history → original returned, no LLM call; else `query_rewrite_chain`).
- `chats/services/rag/pipeline.py` — passes `mode` to `get_rewritten_query`; reranks
  with `rewritten_query` in voice mode, `original_query` in text mode. Generation and
  saving still use `original_query`.
- `CLAUDE.md` — `prompts.py`, `query_rewriter.py`, `pipeline.py` rows; B7
  verification footer.
- Plan Progress: B7 ticked as offline-verified.

## Why

Fillers ("um", "uh", repeats) in spoken questions add noise to the retrieval
embedding and the cross-encoder scores. The answer LLM handles fillers fine, so it
keeps seeing — and the database keeps saving — the original question.

## How / decisions

- Prompt split mirrors the answer prompts (2026-11-04 decision): shared pieces, only
  the last rules differ. The original text prompt had trailing spaces after
  "latest" and "chunks"; the first rewrite of the block dropped them (caught by the
  byte comparison) and they were restored, so text mode is unchanged.
- No logging of the cleaned vs original query was added (the plan's test step
  suggests logging both). Questions are user content; verification used recorders
  instead.

## Affected modules and behaviors

- Voice-mode asks make one more LLM call on the **first** question of a chat (about
  0.5–1 s before retrieval; small `gpt-4.1-mini` cost). Follow-up voice questions
  already made a rewrite call.
- Voice-mode reranking now scores chunks against the cleaned query.
- Text mode: unchanged (same prompt bytes, same no-history shortcut, same rerank
  query).
- No change to API shapes, saving, auth, async tasks, webhooks or analytics.

## Intended outcome

Spoken questions retrieve and rerank as well as cleanly typed ones; signal: for a
filler-heavy voice question the rewritten query has no fillers, while the saved and
displayed question is the original.

## Verification

Offline only — no LLM calls (scratchpad `b7_offline.py`).

- Text rewrite prompt rendered with `format_messages` vs a pre-change capture →
  identical (after restoring the two trailing spaces; first comparison was `False`).
- `get_rewritten_query` with both chains replaced by recorders, question
  `"um so what's the, uh, the main idea"`:
  text/no history → no chain, original returned; text/history → text chain;
  voice/no history → voice chain (history sent `''`); voice/history → voice chain.
- `ask_stream` with rewrite chain → `"CLEAN"`, retrieval/rerank/generation recorded,
  real `bulk_create` inside a rolled-back transaction:
  text → retrieve, rerank, generate all got the original;
  voice → retrieve `CLEAN`, rerank `CLEAN`, generate the original;
  saved user rows: original content with `mode` `text` / `voice`; 0 rows left after
  rollback.
- `uv run ruff check .` → only the pre-existing `chats/tests.py` F401.

**Not verified:** the real LLM's output for `voice_query_rewriter_prompt` (filler
removal quality, whether it keeps the meaning) — needs one paid call.

## Critical notes

- The two trailing spaces in `_REWRITE_INTRO_AND_RULES` are load-bearing for "text
  mode unchanged"; an editor set to trim trailing whitespace would alter the text
  prompt (harmless to the model, but no longer byte-identical).
- Rollback: revert the commit; no migration.
