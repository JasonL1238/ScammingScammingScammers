# Execution log

The task-by-task record of executing [`roadmap.md`](roadmap.md). One entry per
task: scope, the Rule 1 adversarial-review outcome, red-proof evidence for any
new CI gate, and the verification-loop results. Each phase closes with the
roadmap's exit criteria checked off with evidence. An entry lands in the same
commit as its task wherever possible; evidence that can only exist after a push
(CI run URLs) follows in a recorded follow-up.

## Standing verification loop

Run before every commit. CI runs these same checks, plus the install-shape
matrix legs that only CI exercises on every push — so local green predicts push
green for everything except a dependency-resolution difference on a shape not
run locally.

1. Full suite via the project venv (`pytest`). Baselines per install shape,
   recorded at T1.1 — collected count and pass/skip must not regress without a
   deliberate, cited contract change:
   - 557 collected in every shape;
   - py3.13 + `[dev,media]` (the project venv): 555 passed / 2 skipped;
   - `[dev]`-only (no media): 556 passed / 1 skipped.
2. `python -m ssscammers.simscammer.textloop --all-scripts --dry` — exit 0.
3. `ruff check .` — clean.
4. If docker/compose was touched: `docker compose config -q`, postgres up
   against the existing volume, and (for compose restructures) a clean-checkout
   `up --build`.
5. `git status` — no unexpected modifications.

## Red-proof procedure

For every new CI gate: push a throwaway branch seeding the named regression,
**wait for the red run to complete and record its URL**, and only then delete
the branch (or push the revert). Deleting or reverting while the seeded run is
in flight would leave no red evidence. Main never contains the regression.

## Phase 1 — Groundwork: safety hygiene, CI, migration machinery — IN PROGRESS

### T1.1 — CI bootstrap

- **Scope:** `.github/workflows/ci.yml` (jobs: `tests` as the full
  python × install-shape matrix — py3.11/py3.13 × `[dev]`/`[dev,media]` —
  plus the `textloop` release gate on a base-only install and `lint` via
  ruff); pin `--import-mode=prepend` in `pyproject.toml`; sharpen the
  `tests/conftest.py` docstring; record the direct-to-main workflow decision
  in `roadmap.md`; reference this loop from `CLAUDE.md`/`AGENTS.md`; this log.
- **Pre-push verification (all executed locally):**
  - py3.13 + `[dev,media]` (project venv): 557 collected before and after the
    import-mode pin; 555 passed / 2 skipped.
  - py3.13 + `[dev]` (scratch venv, fresh resolve): 556 passed / 1 skipped.
  - py3.11 + `[dev]` (fresh env and resolve, executed during the adversarial
    review): 556 passed / 1 skipped.
  - py3.11 + `[dev,media]` (fresh conda env and resolve — the first-ever
    execution of this documented-supported shape): 555 passed / 2 skipped.
  - textloop `--all-scripts --dry` on a base-only scratch venv: exit 0.
  - `ruff check .` on the project venv and on a fresh ruff 0.16.4 resolve:
    clean.
- **Rule 1 review:** two adversaries (A: correctness/contracts, B:
  design/reuse/verification), then cross-refutation. Findings and outcomes:
  - *A-1 (survived):* the first draft of the pyproject pin comment claimed
    importlib mode fails the suite at collection — disproven by execution
    (suite passes under `--import-mode=importlib`; `tests/conftest.py`'s
    `sys.path.insert` is the mechanism in every mode). **Fixed:** comment
    rewritten to the true rationale (pin the proven default against upstream
    changes); the conftest docstring's ambiguous sentence sharpened.
  - *A-2 + B-F7 (merged, survived):* unconditional `cancel-in-progress`
    destroyed per-commit verification on main and would cancel seeded
    red-proof runs on throwaway branches. **Fixed:** the `concurrency` block
    removed entirely (its only benefit is compute, which Rule 0 does not
    optimize for); wait-for-red added to the red-proof procedure above.
  - *B-F1 (survived, strengthened by A):* this loop omitted ruff and the
    `[dev]`-only shape while claiming to be "the same checks" as CI.
    **Fixed:** ruff added to the loop; the prediction claim scoped honestly
    (matrix legs are CI-only); per-shape baselines recorded.
  - *B-F2 (survived):* `roadmap.md` still demanded merge-blocking CI while
    the owner had chosen a direct-to-main push gate, and the adaptation lived
    only in this log. **Fixed:** the decision recorded in `roadmap.md`'s
    header in the same commit.
  - *B-F3 (survived at reduced severity):* the loop was not referenced from
    `CLAUDE.md`/`AGENTS.md`. **Fixed:** one-line pointer added under
    "Honesty about verification".
  - *B-F4, B-F5 (survived):* pre-verification evidence covered two of three
    job configurations and the baseline ratchet conflated collected count
    with pass count. **Fixed:** the missing shapes executed and recorded;
    baselines restated per shape above.
  - *B-F6 (split):* "the diagonal matrix hides a broken cell today" —
    refuted by execution (py3.13+`[dev]` green). The design half survived:
    the matrix is now the full 2×2, including the never-before-executed
    py3.11+`[dev,media]` cell (pipecat declares `>=3.11`).
  - Dropped in cross-refutation: B's initial claim that the import-mode pin
    guards collection (retracted by B against A-1's evidence — the pin is a
    declaration; nothing behavioral changes if it is deleted today).
- **Red proofs:** pending — run URLs recorded after the gate is live on main.
  Planned seeds: a `<Dial>` verb in `twiml.py` (fails `test_no_outbound`), a
  flipped expectation in `simscammer/scripts.py` (fails `textloop`), an unused
  import (fails `lint`).
- **Escalations:** none. (B-F2 was resolved by recording the owner's
  already-made workflow decision, not by making a new one.)

### Phase 1 exit-criteria checklist

From `roadmap.md` Phase 1, read under the recorded direct-to-main decision
(roadmap header): "merge-blocking" = push-gated, PR-phrased proofs run on
throwaway branches.

- [ ] CI runs on every push and gates all work, and a deliberate throwaway
  branch introducing a `<Dial>` verb fails it.
- [ ] An injected 404 yields a `NOTICE_TEXT` first verb plus a fired alert; a
  healthy re-probe restores the clip.
- [ ] A throwaway `002` migration applies to an existing volume; a deliberate
  mid-list enum insertion fails the revised sync test.
- [ ] PII grep over `.env.example` returns nothing.
- [ ] `docker compose up --build` succeeds from a clean checkout.
- [ ] An unparseable numeric env var produces an asserted warning while still
  falling back to the default.
- [ ] The geocode script exists and has run against the fiction pack with
  results recorded.
- [ ] A checked list confirms no doc claims unbuilt behavior in the present
  tense.
