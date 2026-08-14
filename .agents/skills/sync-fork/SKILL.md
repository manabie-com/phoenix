---
name: sync-fork
description: >
  Sync this fork of Phoenix with upstream Arize-ai/phoenix and resolve the resulting
  conflicts. Use when the user asks to sync the fork, pull or merge upstream, update
  from Arize-ai/phoenix, fix a conflicted PR, or when a PR shows CONFLICTING /
  mergeStateStatus DIRTY. Also use before starting work on the fork, to check how far
  behind it is and what a sync would break.
metadata:
  internal: true
---

# Syncing this fork with upstream

This fork carries media (image and PDF) support for Prompt Management and the
Playground on top of a fast-moving upstream. The job of a sync is to take upstream's
work without losing the fork's, and to make sure a mis-resolved conflict fails loudly
rather than silently shipping a broken prompt.

## The principle

**Fork code lives in files upstream does not have.** Those cannot conflict, ever. Where
the fork must touch an upstream file, it does so in as few lines as possible — a one-to-
three-line delegation that git can auto-merge, never an interleaved block.

Measure it before and after any change to the fork's structure:

```bash
git fetch upstream          # do this first, always — see below
# lines in files upstream owns  = the whole conflict surface
# lines in files only we have   = cannot conflict
git diff --numstat upstream/main
```

**Fetch first.** A stale `upstream/main` makes every number a measurement against a
baseline that no longer exists, and the failure is quiet: files upstream changed show up
as though the fork had changed them. The tell is a file in the diff that the fork has no
business touching — check `git log upstream/main..HEAD -- <path>` before believing it.

At the time of writing, 93% of the fork's hand-written lines sit in fork-only files: 8,457
there against 618 spread over 30 files upstream owns. Keep that ratio going up. Generated
artifacts are excluded from both counts — codegen reproduces them, so they are noise here
even though they are the largest part of the raw diff.

## Runbook

```bash
make sync-fork-check     # read-only preview; safe to run any time
make sync-fork           # merge + resolve everything mechanical
```

Then verify, in this order — cheapest and most diagnostic first:

```bash
uv run pytest tests/unit/db/test_migration_heads.py    # would the server even boot?
make typecheck-python && (cd app && pnpm typecheck)
make format-python && make lint-python && git diff --exit-code   # what CI actually gates on
uv run pytest tests/unit/server/api/helpers tests/unit/db -q
cd app && pnpm vitest run
```

The third line is not optional and is easy to get wrong. CI's "Format and Lint" job runs
`make format-python`, then `make lint-python`, then `git diff --exit-code` — and
`format-python` does **not** sort imports. Only `lint-python` (`ruff check --fix`) does, and
only repo-wide: a `ruff check` scoped to one directory will pass while five other files are
unsorted. A scripted import rewrite once left six such files, all of which passed locally
and failed CI on the `git diff` step.

`make sync-fork` deliberately leaves the merge uncommitted so it can be reviewed before it
becomes history. Committing and pushing are the user's calls — do not do either unasked.

`git rerere` is enabled, so any resolution made once is replayed automatically next time.
Do not disable it.

## What the tooling resolves, and what it will not

| Category | Handling |
|---|---|
| Generated artifacts | Takes upstream's copy, re-runs codegen. **Never merge these by hand** — codegen reproduces the fork's additions deterministically. |
| Migration chain | Re-points the earliest fork-local migration onto upstream's new head. |
| Hand-written overlap | Left alone on purpose. This is the only part that needs judgment. |

Generated artifacts means anything under `__generated__/`, plus `app/schema.graphql`,
`schemas/openapi.json`, and the generated Python and TypeScript clients. If codegen is
needed manually: `make graphql` then `make openapi`.

## Reading a conflict

Before resolving anything, ask which of these it is:

1. **Upstream fixed something the fork also fixed.** Take upstream's version and delete
   the fork's. This has already happened once: the fork carried a `SpanDetails` bug fix
   that upstream shipped 17 hours earlier and more cleanly, and it caused *100% of that
   sync's conflicts*. Check `git log upstream/main -- <path>` before assuming the fork's
   version is wanted.
2. **Upstream changed code the fork's media branch sits inside.** Keep both: apply
   upstream's change, then re-apply the fork's delegation. Then run the provider tests —
   see Safety nets.
3. **Genuinely divergent logic.** Rare. Stop and ask rather than guessing.

`references/conflict-playbook.md` has per-file guidance with measured upstream churn, and
the list of hazards that never appear as text conflicts.

## Safety nets — and what their failures mean

These matter more than the merge. The dangerous outcome is not a conflict; it is
resolving one wrong and not finding out.

**`tests/unit/db/test_migration_heads.py`** — Phoenix runs `alembic upgrade head`,
*singular*. Two heads means the server will not start. Git reports **no conflict** for
this, because two branches can each append a migration without touching the same line. If
this fails after a sync, re-point the earliest fork-local migration's `down_revision` at
upstream's new head (`make sync-fork` does it; do it by hand if you merged manually).

**`TestEveryProviderAcceptsImagesNow`** in
`tests/unit/server/api/helpers/test_playground_media.py` — asserts each provider's payload
actually carries the media. A failure here means a conflict resolution dropped a media
branch. This is worth understanding: with the branch gone, every provider still returns a
valid *text-only* message, so any test that merely checks the builder returned something
will pass while the model receives no image at all.

**`app/src/schemas/__tests__/contentPartSelectionSets.test.ts`** — a GraphQL document that
selects `TextContentPart` without the media parts silently drops media from every prompt it
loads. This test also fires on **upstream's** new documents, roughly every two months. When
it does, decide which kind of document it is:

- feeds a prompt round-trip (loaded, then saved back) → add the media selections, because
  otherwise saving erases media;
- read-only display → add it to `EXEMPT` **with the reason**, since omitting media there is
  cosmetic.

## Gotchas that no test will catch

1. **After any migration edit, diff `scripts/ddl/postgresql_schema.sql` before
   committing.** A repo hook regenerates it, and on PostgreSQL 17+ it emits ~327
   `NOT NULL <column>` table constraints — legal on 17, absent from the `postgres:12`
   upstream CI uses. Restore the committed file (`git checkout HEAD -- <path>`) unless the
   schema genuinely changed. Do not patch `scripts/ddl/generate_ddl_postgresql.py`: it is
   upstream's generator and upstream's bug, and patching it enlarges the conflict surface.
2. **Merging or renumbering migrations breaks existing dev databases.** The schema stays
   correct but `alembic_version` points at a revision that no longer exists, and Phoenix
   refuses to start. Back the database up, then stamp it at the surviving revision.
3. **There is no Python reloader.** Restart the backend after backend changes, and tell the
   user to re-attach their debugger — the dev server runs under `debugpy`.
4. **`pnpm lint:fix` edits a file the fork has no business touching.** It strips nine
   duplicate `orange-*` members from upstream's `app/src/components/core/types/style.ts`.
   Harmless — the tokens are declared earlier in the same union — but it is an unrelated
   *deletion* inside an upstream file, the shape git merges worst. Revert it
   (`git checkout upstream/main -- <path>`) and never let `git add -A` sweep it in.
5. **Keep the branch to one concern.** Do not fold unrelated fixes into it. That single
   rule has prevented more conflict than every structural change combined — an unrelated
   `SpanDetails` fix once caused 100% of a sync's conflicts, on a bug upstream had already
   fixed more cleanly seventeen hours earlier.

## Label the sync PR `upstream-sync`

Do this as soon as the PR exists. Without it the **OpenAPI Schema Backward
Compatibility** check fails on any sync where upstream retired an endpoint, and the
failure is neither actionable nor a fork defect — the check compares the PR base
against its head, so it reports upstream's own breaking changes as if the fork had
made them.

The August 2026 sync is the worked example: upstream replaced
`/agents/{agent_id}/sessions/{session_id}/chat` and `.../summary` with
`/v1/agent_sessions/*`. Both routes were defined in
`src/phoenix/server/api/routers/agents.py` at the merge-base, so they were upstream's
all along, and no fork code called either one.

`.github/workflows/openapi-schema.yaml` carries a one-line `if:` that skips the job
when this label is present. That is a fork line in an upstream-owned file, kept to a
single insertion with no comment precisely to hold the footprint at one line — this
section is the comment. Do not widen the condition to a branch prefix like `claude/*`:
that would disable the gate for every Claude-authored PR, including one that really
does break the fork's own API.

The label does not re-trigger the workflow, so add it before pushing, or re-run the
check afterwards.

## Running the Playwright suite against a sync

Worth doing after a sync, because it is the only layer that exercises real Relay data in a
browser — see the `@inline` hazard in the playbook for what the other layers miss.

```bash
cd app && pnpm build          # the harness serves the built app, not the dev server
PHOENIX_PORT=6020 PHOENIX_GRPC_PORT=14320 \
  pnpm playwright test tests/prompt-management.spec.ts tests/playground.spec.ts \
  --project=chromium --reporter=list
```

Both ports must be overridden. `baseURL` defaults to port 6006 with
`reuseExistingServer` true locally, so a bare run points Playwright at the developer's own
dev server — which has no auth, so the login setup fails — and the test harness also binds
gRPC 4317, which that server already holds. Chromium may need `pnpm exec playwright install
chromium` first; Firefox and WebKit are usually absent, so pass `--project=chromium`.

## Adding to the fork

When the sync is done and there is new fork work to write, put it in fork-owned paths:

- provider media handling → `src/phoenix/server/api/helpers/playground_media/` (a module
  per provider, `__init__.py` re-exporting);
- media content parts and their ORM conversion → `src/phoenix/db/types/media_parts.py`,
  `src/phoenix/server/api/input_types/MediaContentInput.py`;
- media inside a playground message → `src/phoenix/server/api/helpers/message_media.py`;
- the shared GraphQL media selection and its readers →
  `app/src/utils/mediaContentPartFragment.ts`;
- playground media state and controls → `app/src/pages/playground/playgroundMedia.ts`,
  `app/src/pages/playground/media/`;
- prompt and trace rendering → `app/src/components/prompt/media/`,
  `app/src/pages/trace/span/media/`;
- leave only a one-to-three-line call in the upstream file.

Tests go in new files too, rather than into upstream's. A fork assertion added to an
upstream test file conflicts on every upstream edit to that file, for no benefit —
`test_message_media.py` and `getChatCompletionInputMedia.test.ts` exist for exactly that
reason.

If a block of fork logic has to be duplicated across upstream files, extract it instead.
Duplication is worse than its line count suggests: a merge can fix one copy and leave the
other stale, with no test failing.

The one place this needs care rather than enthusiasm is a shared **GraphQL selection**. The
`@inline` fragment above is the right structure — it is what keeps five spread sites down to
one line each — but the deduplication changes how the data arrives at runtime, and getting
that wrong fails silently in a way nothing catches. Read the `@inline` section of the
playbook before touching it.
