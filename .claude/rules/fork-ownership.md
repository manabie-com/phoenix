# Fork ownership — keep customizations out of upstream files

This repo is a fork of `Arize-ai/phoenix` (remote `upstream`) and is synced regularly.
Every line the fork adds to a file upstream also has is a future merge conflict. Every
line in a file upstream does **not** have can never conflict, no matter how much either
side changes.

So: **when adding or customizing behavior, put it in a new fork-owned file. Touching an
upstream file is the exception, and it needs a reason.**

## Before editing any file

Check who owns it:

```bash
git cat-file -e upstream/main:<path> && echo "UPSTREAM-OWNED — minimize" || echo "fork-owned — free"
```

If upstream owns it, ask whether the change can live somewhere else instead. Usually it
can: extract the logic into a new module and leave a call behind.

## Budget for upstream files

When you genuinely must touch one, the whole footprint should be a **one-to-three-line
delegation** that git can auto-merge — an import plus a call. Never an interleaved block
of fork logic inside upstream's.

Shape matters as much as size. In descending order of merge safety:

1. **A new line appended at the end of the file** — safest.
2. **A new line inserted somewhere upstream rarely edits.** Avoid the spots upstream
   itself appends to; that's where both sides collide.
3. **A same-line token swap**, upstream's surrounding lines untouched.
4. **Deleting or reflowing upstream's lines** — worst. Avoid entirely if there is any
   alternative. A deletion inside an upstream file is the shape git merges worst.

Also avoid shared single lines that both sides extend — a Makefile `.PHONY` list, a long
import line, an array of registered items. Prefer a mechanism that accumulates across
separate declarations.

## Worked examples in this repo

**`Makefile`** — fork targets live in `mk/fork.mk`, including their own `.PHONY`
declaration (`.PHONY` accumulates, so the fork never touches upstream's shared one). The
root Makefile carries a single `-include mk/fork.mk` at EOF, deliberately *after*
`clean-all` because upstream appends new sections just above `# Cleanup`.

**`app/src/api/__fixtures__/mockedApiGet.ts`** — upstream's test literals stop compiling
once the fork's media endpoints enlarge the `paths` union, so the edit has to land in
upstream's test file. The fix is a fork-owned helper plus one import and same-line
`vi.mocked(authApiFetch.GET)` → `mockedApiGet()` swaps, leaving every upstream line intact.

**`src/phoenix/server/api/helpers/playground_media/`** — a module per provider, with only
a 1–3 line delegation left in upstream's `playground_clients.py`.

## Where fork code goes

- provider media handling → `src/phoenix/server/api/helpers/playground_media/`
- media content parts and ORM conversion → `src/phoenix/db/types/media_parts.py`,
  `src/phoenix/server/api/input_types/MediaContentInput.py`
- media inside a playground message → `src/phoenix/server/api/helpers/message_media.py`
- shared GraphQL media selection → `app/src/utils/mediaContentPartFragment.ts`
- playground media state and controls → `app/src/pages/playground/playgroundMedia.ts`,
  `app/src/pages/playground/media/`
- prompt and trace rendering → `app/src/components/prompt/media/`,
  `app/src/pages/trace/span/media/`
- fork-only make targets → `mk/fork.mk`
- test helpers → a `__fixtures__/` directory beside the module they are about

## Tests

Fork tests go in **new test files**, never as extra assertions inside upstream's. An
assertion added to an upstream test file conflicts on every upstream edit to that file,
for no benefit. `test_message_media.py` and `getChatCompletionInputMedia.test.ts` exist
for exactly this reason.

## Other standing rules

- **Do not duplicate fork logic across upstream files.** Extract it. A merge can fix one
  copy and leave the other stale with no test failing.
- **Keep a branch to one concern.** An unrelated fix folded into a branch once caused
  100% of a sync's conflicts.
- **Never let `git add -A` sweep tool-driven edits into upstream files.** `pnpm lint:fix`
  in particular rewrites `app/src/components/core/types/style.ts`, which the fork has no
  business touching. Revert with `git checkout upstream/main -- <path>`.
- **Never hand-edit generated artifacts** (`__generated__/`, `app/schema.graphql`,
  `schemas/openapi.json`, generated clients). Re-run `make graphql` / `make openapi`.

## Measure after any structural change

```bash
git fetch upstream    # a stale upstream/main makes every number meaningless
git diff --numstat upstream/main
```

Lines in files upstream owns are the entire conflict surface; lines in fork-only files are
free. Keep the ratio moving toward fork-only. Current: 30 upstream-owned files carrying
+607/−108 hand-written lines, against ~12,400 in fork-only files.

## See also

The `sync-fork` skill (`.agents/skills/sync-fork/`) covers performing a sync and resolving
conflicts, and its `references/conflict-playbook.md` ranks per-file exposure by measured
upstream churn.
