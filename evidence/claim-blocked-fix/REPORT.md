# REPORT — claim on a blocked task (mission 51b52b761f22)

Owner-direct recovery of blocked mission e0977613470e, task
kechup-claim-blocked-state. Base: build/v1 40e117f.

## Problem

`kechup claim` on a blocked, unowned task printed
`error [already_claimed]: already claimed by None`
(trace 70a7a8e5, 2026-10-12, task T27, state=blocked, owner=None,
claimed_at=None).

Cause: `claim_task` raised `already_claimed` for every displayed state
outside `todo` and `doing (stale)`.

## Change

- kechup/commands.py, `claim_task`: before the generic refusal, a
  `blocked` displayed state now raises `task_blocked` (exit 2), message
  `<id> is blocked; unblock it first`, data `{"id", "blocker_cause"}`.
  No auto-unblock. Claim eligibility stays `todo` and `doing (stale)`.
  A genuinely-owned doing task keeps the exact `already_claimed` error.
- kechup/guide.py: claim help text and exit-code list updated.
- tests/test_commands.py: two new tests (blocked unowned, blocked
  owned), plus exact message and data assertions. Existing
  `test_claim_wrong_state_refusal` unchanged and green.

## Evidence

- Red (before fix): new test failed with
  `assert 'already_claimed' == 'task_blocked'`.
- Real CLI red (base code, fresh project): exit 2,
  `error [already_claimed]: already claimed by None`.
- Real CLI green (candidate code, fresh project): exit 2,
  `error [task_blocked]: T1 is blocked; unblock it first`.
- Base suite (40e117f, private export): 216 passed.
- Candidate suite: 218 passed. No new failures.
- Advisor review 1 (pre-commit diff): APPROVE. Its nonblocking
  suggestion (assert exact message and payload) was applied.
- Advisor review 2 (final complete committed diff, including the
  later test and SPEC changes): APPROVE, model openai/gpt-6-astra
  (backup model; the primary launcher auth failed). No blockers.
- dev:plan skill loaded. Its validate gate maps to the trace
  evidence; the done state is the observable CLI behavior above.

## Out of scope

No auto-unblock, no eligibility change, no dependency, no refactor,
no service change, no restart.