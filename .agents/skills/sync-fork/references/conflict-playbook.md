# Conflict playbook

Where conflicts actually happen, and what to do about each. Every number here came from
measurement, not estimation — re-measure before trusting them, since both the fork and
upstream move:

```bash
# risk = upstream commits in 120 days x lines the fork changes
git log --oneline --since="120 days ago" upstream/main -- <path> | wc -l
git diff --numstat upstream/main -- <path>
```

## Ranked exposure

Only files **upstream also has** can conflict, and generated artifacts among them do not
count because codegen reproduces the fork's additions. That leaves 30 hand-written files
holding 618 added lines, against 8,457 in files upstream does not have — 93%.

Backend paths below are relative to `src/phoenix/`; frontend paths are given in full.
Measured against `upstream/main` at `7d8b917ff`.

| Risk | Churn/120d | +/- | File |
|---|---|---|---|
| 2650 | 25 | 88/18 | `server/api/helpers/playground_clients.py` |
| 1037 | 17 | 61/0 | `config.py` |
| 450 | 25 | 18/0 | `server/api/queries.py` |
| 351 | 13 | 27/0 | `db/models.py` |
| 275 | 5 | 34/21 | `app/src/pages/playground/PlaygroundChatTemplate.tsx` |
| 216 | 9 | 18/6 | `server/daemons/experiment_runner.py` |
| 182 | 13 | 13/1 | `app/src/pages/playground/playgroundUtils.ts` |
| 150 | 5 | 26/4 | `server/api/input_types/PromptVersionInput.py` |
| 145 | 29 | 5/0 | `server/app.py` |
| 140 | 10 | 13/1 | `Makefile` |
| 140 | 5 | 25/3 | `app/src/pages/playground/fetchPlaygroundPrompt.ts` |
| 104 | 8 | 13/0 | `scripts/ddl/postgresql_schema.sql` |
| 85 | 5 | 15/2 | `server/api/evaluators.py` |
| 70 | 1 | 69/1 | `server/api/types/PromptVersionTemplate.py` |

The remaining sixteen score below 60. Five score zero — upstream has not touched them in
four months — which means zero *today*, not zero permanently.

Read the columns together. `app.py` has the highest churn in the repo but the fork adds five
one-line insertions, so it is near-harmless. `PromptVersionTemplate.py` holds 69 fork lines
but upstream touched it once in four months. The dangerous combination is both, which is why
`playground_clients.py` dominates and why `config.py` — sixty-one lines of env-var
declarations in a file edited every fortnight — is second.

Two traps when re-measuring. `git fetch upstream` first, or files upstream changed will
appear as though the fork changed them. And if you script the loop in bash, close the inner
commands' stdin — `git cat-file` and `git log` will eat the loop's input and the loop
silently produces nothing at all:

```bash
git log --oneline --since="120 days ago" upstream/main -- "$path" </dev/null | wc -l
```

## Per-file guidance

### `src/phoenix/server/api/helpers/playground_clients.py` — expect conflicts, keep them small

Where upstream adds providers and models. The fork's remaining lines are 1–3 line
delegations into `playground_media/`, one per provider class, plus an import block.

Resolving: apply upstream's change, re-add the delegation, then run
`uv run pytest tests/unit/server/api/helpers/test_playground_media.py -q`. Those tests read
each provider's payload, so a dropped delegation fails rather than silently sending
text-only.

Do not inline media logic back into this file to resolve a conflict. Put it in
`playground_media/` and call it.

### `config.py`, `queries.py`, `app.py`, `server/api/routers/v1/__init__.py`

Insertions among upstream's own lists — env vars, resolvers, daemon wiring, router
registration. Small and mechanical, but they land where upstream inserts too. Take both
sides; ordering rarely matters.

### `Makefile`

The `.PHONY` line is one long shared line that upstream also edits, so a conflict there is
near-certain eventually. Union the target names, keep the fork's `sync-fork` /
`sync-fork-check` entries.

### `src/phoenix/db/models.py`

`MediaFile` is appended near the end, where upstream also appends new models. Take both.

### `src/phoenix/server/api/evaluators.py`

Two changes, and the interesting one is a *rejection*: an `ImageContentPart` in an LLM
evaluator prompt raises `BadRequest`, because the evaluator path has no media resolution.
If a conflict resolution drops that branch, an image in an evaluator prompt is silently
ignored instead of refused, and the evaluator scores against a prompt missing its image.
The other is `oi.Message(content=message_text(msg))`, which flattens a possibly-structured
content list back to text for the span attributes.

### Frontend

`PlaygroundChatTemplate.tsx` (34 lines), `fetchPlaygroundPrompt.ts` (25) and
`playgroundUtils.ts` (13) carry the most. The substance already sits in fork-owned modules —
`app/src/pages/playground/playgroundMedia.ts`, `media/useMessageMedia.ts`,
`media/MessageMediaButtons.tsx`, `app/src/utils/mediaContentPartFragment.ts` — so what
remains in upstream files is call sites, JSX props, and GraphQL fragment spreads that cannot
move without restructuring upstream's components.

The spreads are the part to be careful with: see the `@inline` hazard below.

## Hazards that never appear as a text conflict

These are the ones that make a sync look successful and break later.

### Two alembic heads

Phoenix calls `command.upgrade(config, "head")` — singular. Two branches can each append a
migration without touching the same line, so git reports nothing and the failure surfaces
only when a database is opened.

Caught by `tests/unit/db/test_migration_heads.py`. Fix by re-pointing the earliest
fork-local migration's `down_revision` at upstream's new head.

### A dev database stamped at a deleted revision

If fork migrations were merged or renumbered, existing databases have the right schema but
an `alembic_version` row naming a revision that no longer exists. Phoenix refuses to start
with *"Can't locate revision identified by ..."*.

Back the database up first — for SQLite use the backup API, not `cp`, because WAL mode means
a plain copy can be torn:

```python
import sqlite3
src = sqlite3.connect("file:<db>?mode=ro", uri=True)
dst = sqlite3.connect("<backup>")
src.backup(dst)
```

Then stamp it at the surviving revision.

### The DDL snapshot hook

A hook regenerates `scripts/ddl/postgresql_schema.sql` on any migration edit. On PostgreSQL
17+ it emits ~327 `NOT NULL <column>` table constraints. That form is legal from 17 but
absent from `postgres:12`, which upstream CI uses — so the regenerated file is unloadable on
the oldest supported server.

Always diff this file before committing after a migration change, and restore the committed
version unless the schema genuinely changed. Leave upstream's generator alone.

### Fork tests pinned to third-party SDKs

`TestProviderSdkContracts` asserts the fork's media allowlists equal the Anthropic, Bedrock
and Google SDKs' own literal unions. Upstream runs weekly dependency upgrades, so the
trigger is frequent even though a real failure only happens when a provider adds a format.
A failure here is information, not breakage: a provider now supports something the fork
could accept.

### OpenInference coupling

`src/phoenix/server/api/helpers/playground_media/_tracing.py` records a document as a *text* block naming it, because
OpenInference's `MessageContent` is a closed union of text, image and reasoning with no
document type. If a dependency bump adds one, that workaround becomes wrong rather than
merely suboptimal, and nothing will fail to say so. Re-check it whenever
`openinference-instrumentation` moves.

### Relay `@inline` fragments, and why the media selection is deduplicated carefully

`app/src/utils/mediaContentPartFragment.ts` holds the media selection once as an `@inline`
fragment. That is what reduces the media selection in four upstream-owned files —
`utils/promptUtils.ts`, `components/prompt/PromptChatMessagesCard.tsx`,
`pages/playground/fetchPlaygroundPrompt.ts`, `pages/playground/experimentRehydration.ts` —
to five one-line spreads. The trap is in how Relay delivers it.

An `@inline` fragment's fields are **not** left on the parent object at runtime. They are
stashed under a `__fragments` key, and only `readInlineData(fragment, ref)` returns them.
A consumer that spreads the fragment and then reads `part.url` gets `undefined` — with no
type error, because the generated `$data` type is only reachable through `readInlineData`,
and the union arm the reader produces ends in `"%other"`. Every layer stays green:
typecheck, `pnpm vitest`, the selection-set guard test. The prompt page renders no media and
the playground's load→save round-trip *erases* it.

So: never spread this fragment and read its fields directly. Go through
`readMediaContentPart` / `flattenMediaContent` from that module, which call `readInlineData`
and merge the result back onto the part. `readInlineData` throws on a plain object, so the
flatten shim also exists for code paths handling already-materialised parts.

`app/src/utils/__tests__/mediaContentPartFragment.test.ts` asserts against the real
`__fragments` shape rather than a hand-written mock, which is the only reason a regression
here would be caught. Keep it that way — a mock shaped like the *expected* data would pass
while production is broken.

### Places the fork modifies upstream behaviour

Mostly the fork only adds. The exceptions are worth knowing because a plausible-looking
resolution breaks them without any test naming the cause:

- `src/phoenix/server/daemons/experiment_runner.py` changes `_build_messages` from sync to
  async so media can be resolved. If upstream edits that method the conflict is semantic,
  not textual, and a careless resolution silently breaks media in experiments.
- `src/phoenix/server/api/subscriptions.py` inserts one
  `await resolve_message_media(session, messages)` inside its own `async with db()` block,
  positioned deliberately *after* the message list is final so a single batch covers every
  image. Move it earlier while resolving a conflict and it resolves an incomplete list;
  keep it where the messages stop changing.
- `src/phoenix/server/api/evaluators.py` raises rather than ignoring — see above.

## Verifying a sync properly

```bash
uv run pytest tests/unit/db/test_migration_heads.py            # boots at all?
uv run pytest tests/unit/server/api/helpers/test_playground_media.py \
              tests/unit/server/api/helpers/test_message_media.py -q --db all
make typecheck-python && (cd app && pnpm typecheck)
make format-python && make lint-python && git diff --exit-code   # CI's Format and Lint job
cd app && pnpm vitest run
```

A media-suite pass on both dialects plus a green migration-head test covers every failure
mode listed above except three:

- the **DDL snapshot**, which needs the eyeball diff;
- the **`@inline` delivery** trap, which only the frontend suite catches, and only because
  `app/src/utils/__tests__/mediaContentPartFragment.test.ts` uses real Relay data;
- **import ordering**, which typechecks and tests both ignore. `make format-python` does not
  sort imports and a directory-scoped `ruff check` only covers what you point it at, so the
  `git diff --exit-code` line above is the check that matters. Six unsorted files once
  reached a PR this way.
