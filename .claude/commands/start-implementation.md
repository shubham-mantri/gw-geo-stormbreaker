---
description: Begin subagent-driven M0 implementation of gw-geo-stormbreaker
---

You are the **orchestrator** for implementing M0 of gw-geo-stormbreaker.

Follow `CLAUDE.md` → "On 'start implementation'". Concretely:

1. Read `docs/trd.md` (binding interface contracts) and `docs/tasks/README.md` (DAG + waves).
2. Invoke the `superpowers:subagent-driven-development` skill.
3. Dispatch tasks **by wave, in parallel within each wave** (one fresh subagent per task, in a
   single batched message):
   - Wave 0: T01, T02, T03
   - Wave 1: T04, T05, T06, T07
   - Wave 2: T08, T09, T10, T11, T12
   - Wave 3: T13 → T14
   Each subagent prompt: "Implement `docs/tasks/M0-T<NN>-*.md` exactly, TDD-first. Honor the TRD
   interface contracts verbatim. Run `make check`. Commit on your own branch `m0/T<NN>-<slug>`.
   Return a summary + commit hash."
4. **Between waves**, run the review gate: `pytest -q` green, `ruff` + `mypy src/gw_geo/common`
   clean, interfaces match the TRD. Merge the wave's branches locally, then start the next wave.
5. After each wave, report status to the user. Keep everything local (no PRs unless asked).

Do not write feature code yourself. Do not start a task before its dependencies have merged. Do
not allow TRD interface changes without surfacing them.

$ARGUMENTS
