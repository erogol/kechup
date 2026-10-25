# SPEC — claim on a blocked, unowned task (exit 2 path)

## Problem

`kechup claim T<n>` on a blocked task with `owner=None` fails with:
`error [already_claimed]: already claimed by None`.

Evidence: trace 70a7a8e5 (2026-10-12), task T27, state=blocked,
owner=None, claimed_at=None.

Cause: `claim_task` (kechup/commands.py:227) raises `already_claimed`
for every displayed state outside `todo` and `doing (stale)`.

## Required behavior

1. Claim on a blocked task (with or without an owner) reports a
   blocked-state error and says that the task needs unblocking first.
   The task stays blocked. Claim does not auto-unblock.
2. Claim eligibility stays unchanged: only `todo` and `doing (stale)`
   tasks can be claimed.
3. A genuinely-owned doing task keeps the exact `already_claimed`
   error with the owner id.

## Design

In `claim_task`, before the existing refusal, add one branch:
when `displayed.state == "blocked"`, raise a new error code
`task_blocked` (exit 2), message `"<id> is blocked; unblock it
first"`. No other change. `data` carries `id` and `blocker_cause`
when present. Guide text updated: claim also reports
`task_blocked` for a blocked task; error-code list updated.

## Tests

- `test_claim_blocked_unowned_refusal`: block a todo task, claim fails
  exit 2 with `task_blocked` and the exact message, task stays blocked
  with no owner. It also asserts the `id` and `blocker_cause` data.
- `test_claim_blocked_keeps_owner_case_refusal`: same error for a
  blocked task that an agent owns.
- Existing `test_claim_wrong_state_refusal` stays green and proves
  the owned case.

## Out of scope

No auto-unblock. No eligibility change. No new dependency. No
refactor. No service change.