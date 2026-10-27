# .factory/holdout/HOLDOUT.md — Independent scenarios

Checks the same product through different combinations and edge cases.
These are NOT visible to the builder. Keep private scenarios outside the
builder's accessible checkout and give them only to the verification
environment. A directory called "holdout" does not make it private by
itself.

## Scenario 1 — Path escape via symlink swap
- Given a trusted checkout with a regular file, swap it to a symlink mid-read.
- Expected: inspection reports FAIL, no source content leaked.

## Scenario 2 — Oversized batch
- Given 65 changed paths.
- Expected: single bounded failure record, no partial processing.

## Scenario 3 — Security: eval in nested function
- Given a .py file containing eval() inside a nested function.
- Expected: security scan flags SEC001, gate FAIL.

## Scenario 4 — Test failure blocks merge
- Given a change set whose tests fail.
- Expected: pipeline gate FAIL, tests gate FAIL.

## Scenario 5 — Clean change passes all gates
- Given a trivial valid change with passing tests.
- Expected: pipeline gate PASS.
