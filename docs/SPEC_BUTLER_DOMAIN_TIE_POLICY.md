# SPEC — Butler domain-detection tie policy

> Autonomous overnight cycle 2 (to-spec → implementation). This spec is a local
> artifact only: no user interview was conducted (there is no user present in an
> overnight run) and it was not published to any issue tracker or labelled
> `ready-for-agent`. The seam below is proposed from existing tests; it was not
> user-confirmed.

## Problem Statement

When the Butler gateway has no LLM configured, it routes an incoming message to
a registered agent identity using deterministic domain-keyword scoring. A single
message can score equally for two domains — for example, "scan for evidence"
hits both the security keyword "scan" and the evidence keyword "evidence". The
public contract for this case is currently backwards: the detector's docstring
says it returns "None on tie/empty", while the implementation actually returns
the first-declared domain in the keyword registry (verified by a read-only
probe: `"scan for evidence" -> "security"`, `"remember the risk" -> "memory"`,
`"firewall and proof" -> "security"`). Callers therefore cannot tell from the
contract whether an ambiguous message is refused, routed to the first domain,
or routed arbitrarily. The behaviour itself is real and shipping; only its
documentation is wrong. This is a contract/documentation gap, not a runtime
defect, and any future reordering of the keyword table would silently change
routing unless the tie rule is pinned by a test.

## Solution

Pin the contract to the behaviour that already ships: on a non-empty message
with a positive score, domain detection resolves ties deterministically to the
first-declared domain in the keyword registry; it returns None only for blank
input or when no domain scores at all. Correct the public docstring to say this,
and add a characterization test that locks the tie policy in place. No routing
behaviour changes. Follow-on slices may name the scoring weights as module
constants and unify the duplicate scoring routine, but those are explicitly
outside this smallest slice.

## User Stories

1. As a Butler operator with no LLM configured, I want an ambiguous message to
   always route to a predictable agent identity, so that routing is reproducible
   across restarts and keyword-table edits.
2. As a Butler operator, I want the tie rule stated in the public contract, so
   that I can reason about routing without reading the scoring loop.
3. As a maintainer, I want a regression test that pins first-wins tie-breaking,
   so that a future keyword-table reorder cannot silently change which agent
   handles a message.
4. As a maintainer, I want the empty-input and no-hit cases to stay None, so that
   the gateway's documented no-signal fallback is preserved.
5. As a maintainer, I want the change to be behaviour-preserving, so that no
   caller, orchestrator path, or integration test needs to be updated.
6. As a maintainer, I want the phrase-match and single-token weights to remain
   identical, so that non-tie routing scores are untouched.
7. As a reviewer, I want the seam to be the existing public detector method, so
   that no new test-only API or HTTP surface is introduced.
8. As an operator, I want the declaration order of the domain registry treated
   as a stable contract, so that a cosmetic reorder cannot alter production
   routing.
9. As a maintainer, I want the diff limited to documentation and a test, so
   that the risk of this change is effectively zero.
10. As a future contributor, I want the two None cases (blank and no-hit)
    written down explicitly, so that I do not "fix" them into a tie result.

## Implementation Decisions

- The Butler keyword router keeps its public domain-detection method and its
  return types unchanged; no runtime branch, new parameter, or new return value
  is added in the smallest slice.
- The documented tie policy is first-declared-domain wins. The declaration order
  of the domain-keyword registry is therefore part of the routing contract and
  is treated as stable; no sorting, randomness, or "None on tie" behaviour is
  adopted.
- "None" is reserved for exactly two cases: blank/whitespace-only input, and no
  domain scoring above zero. Both stay unchanged.
- Only the public docstring is corrected; the scoring loop, comparison, weights,
  and registry order are left byte-for-byte untouched.
- A characterization test is added at the existing seam that drives the public
  detector method with tie and no-hit messages; it asserts the current behaviour
  rather than a proposed new one.
- The phrase-match weight and the single-token match weight stay at their current
  values; promoting them to named constants is a separate, later slice of this
  spec, not part of the smallest slice.
- The scoring routine used by the fallback path is left as-is in this slice;
  unifying it with the detector's scoring is deferred to avoid widening the
  diff.

## Testing Decisions

- A good test here asserts only externally observable behaviour of the public
  detector (which domain is returned for a given message, or None), never the
  internal scoring loop, weights, or registry-order mechanics.
- Module tested: the Butler keyword router. No new module or seam is created.
- **Seam used (existing, highest available):** the public `detect_domain`
  method, exercised in the Butler integration test module's existing router
  section (`tests/test_butler.py`, `test_detect_domains` and its neighbours).
  This is a pure, synchronous, no-I/O seam; one seam only.
- Prior art: the existing domain-detection assertions in `tests/test_butler.py`
  and `tests/test_research_integration.py` already call this exact seam; the new
  test follows their style.
- The test is a characterization/regression test: it must pass against the
  unmodified code (proving behaviour preservation) and fail only if the tie
  policy or the None cases change.

## Acceptance Criteria

- AC1: For a message that scores equally for two domains, `detect_domain`
  returns the domain declared first in the domain-keyword registry
  (deterministic first-wins), never `None` and never a later domain.
- AC2: For blank or whitespace-only input, `detect_domain` returns `None`.
- AC3: For a message with no keyword hit in any domain, `detect_domain` returns
  `None`.
- AC4: A single unambiguous domain hit still returns that domain (no regression).
- AC5: The public docstring states the first-wins tie policy and the two `None`
  cases; it no longer claims "None on tie".
- AC6: No runtime code path, return type, or scoring weight changes; the diff is
  limited to the docstring and the new characterization test.
- AC7: Existing Butler router and research-integration tests continue to pass
  unchanged.

## Test Plan

1. Add to the existing router section of `tests/test_butler.py` a parametrized
   characterization test over at least three tie messages with **different**
   first-declared winners, so the test would catch both a "return None on tie"
   change and a registry reorder. Observed tie cases to pin:
   `"scan for evidence"` → `security` (security declared before evidence),
   `"remember the risk"` → `memory` (memory declared before risk),
   `"firewall and proof"` → `security`.
2. Assert the two `None` cases: `""` / `"   "` → `None`, and a no-hit message
   such as `"xyzzy qwerty"` → `None`.
3. Assert one single-hit case to guard AC4, e.g. the existing
   `"scan for vulnerabilities and exploits"` → `security`.
4. Confirm the new test passes against the **unmodified** committed code before
   any docstring change (behaviour-preservation proof), then make the docstring
   edit and re-run it.
5. Re-run the two existing seam files (`tests/test_butler.py`,
   `tests/test_research_integration.py`) with the worktree's own venv pytest;
   capture exit codes. Run ruff check / ruff format --check / mypy on the changed
   files and the source package as additional gates.
6. Because this is docs + characterization test only, no new HTTP endpoint, no
   peer-execution path, and no secret is introduced.

## Out of Scope

- Changing tie behaviour to `None` or to any other policy.
- Reordering, adding, or removing domain keywords, or changing phrase/word
  weights.
- Merging the detector's scoring loop with the fallback `_score` helper.
- Any HTTP/API surface, beta route, peer execution, NYX/ATHENA integration,
  merge/push/release/deploy, or secret handling.
- Editing any dirty/WIP file or the canonical checkout during this phase.

## Further Notes

- The contradiction is verified, not assumed: the docstring at the detector
  reads "or None on tie/empty", while the strict-greater comparison keeps the
  first maximum. The read-only probe results above are the evidence.
- This spec is intentionally narrow: it converts a vague contract into a
  written, tested one with zero runtime change. It is distinct from cycle 1's
  duplication findings, which targeted repeated implementation rather than an
  ambiguous contract.
- No prototype produced a decision-rich snippet, so no inline code is included
  in Implementation Decisions, consistent with the to-spec template.
