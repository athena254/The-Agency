# Bounded-autonomy request and decision traceability — October 16, 2026

Status: user-provided handoff preserved below; derived engineering proposals remain proposals. Model-switch metadata is not a product requirement. The repository handoff `docs/HANDOFF_LATTICE_REPAIRS_20261016.md` is separately retained unchanged.

## User handoff supplied in this session (task text; line-end whitespace normalized)

```text
We left here
Guest:
Committed, pushed and ready for handoff.

- Repository: athena254/The-Agency
- Branch: fix/lattice-foundation-20261015
- Handoff commit: 1c7e7da
- All 46 previously unpublished commits, including the handoff, are now on that branch. Git and GitHub API readbacks confirmed the exact commit.
- Both local checkouts are clean. Main was not merged or changed; nothing was deployed.

Handoff documentation: docs/HANDOFF_LATTICE_REPAIRS_20261016.md

It records completed repairs, remaining security/peer-authority work, free-CLI blockers and safe continuation instructions. I also preserved 113 hash-verified evidence/research files outside the temporary cache, including failed runs.

The pushed source/tests match tested ba9ec76: 1,664 Python tests, 14 UI tests, Ruff and strict mypy passed, with one remaining deprecation warning. The handoff commit changes documentation only. CI has not run for this branch-only push.

Forward this file to the next session:

We have many of the ingredients, but we have not yet demonstrated them working together as an autonomous Agency. It is beyond a blank prototype, but it is still at the integration and authority stage—not final polish.

I checked our pushed state against Hermes’s current agent-loop, delegation and security documentation.

Compared with Hermes

Hermes has an integrated loop: receive a goal → choose tools → inspect results → continue or recover → return an answer. Memory, delegation, scheduling and permission controls support that loop.

The Agency has pieces of this:
- Working model adapters and general/research tool loops.
- Persistent conversation history, context assembly and memory retrieval.
- Planning, execution, workflow and recovery components.
- A human gateway and local browser interface.
- Now-reviewed shared-resource ownership and startup/shutdown handling.

The gap is making those pieces behave as one dependable system. Some current paths still drop recalled context, repeat whole-task prompts instead of dispatching real specialists, or label results “verified” without the corresponding checks. Durable recovery exists for particular workflows, not yet for the complete autonomous task lifecycle.

The 1,664 passing tests verify existing behavior; they do not prove those missing capabilities.

Your target is also more demanding

There are two different milestones:

1. Hermes-like bounded autonomy
Give it a permitted task, let it use tools and workers, check its output, retain progress and report the result without constant intervention.

2. Your full Agency
Do that through independently identified peers participating in the Lattice, with affected-consumer input, enforceable grants and no Butler or coordinator granting authority alone.

The first is the nearer milestone. The second still requires substantial protocol, security and integration work. Connecting more agents or adding more CLI tools will not substitute for that.

What I would prove next

One narrow, harmless task through the actual system:

1. You give Butler a goal.
2. The Lattice routes it to the appropriate participating worker.
3. The worker receives the correct authorized context and performs the permitted work.
4. Results are checked honestly and recorded durably.
5. A controlled interruption does not lose progress or duplicate effects.
6. Butler returns the real result—or clearly reports a blocked/failed task.

That demonstration needs the applicable identity, permission and audit boundaries in place. It must not bypass the peer requirements merely to produce a successful demo.

My assessment: we are closer to a useful, restricted autonomous assistant than to the fully autonomous decentralized Agency. I would not yet leave it unattended on meaningful or consequential work. The next priority should be proving that complete vertical task path—not adding more named agents or accumulating more component tests.

Free CLI workers can help us build it faster; they do not themselves close the runtime gaps.


How do we achieve the first hermes like autonomy?
```

## Subsequent owner instruction (verbatim)

```text
Start working towards this
```

## Interpretation and requirement coverage

No remote publication, historical file count or old test total above has been independently re-certified merely by copying the handoff. Current local SHA/source-tree equality and this pass's diagnostic are recorded separately. No deployment, CI or live autonomy claim is inferred from prior qualification.

RQ-01 Permitted goal without constant intervention -> milestone sections 1, 3 and 5; AC-01/02/11/17/19.
RQ-02 Actual tools and workers, not new role names -> sections 3, 5 and 10; AC-02/04; G-05/06.
RQ-03 Correct authorized context and retained conversation -> section 4; AC-03/18; A1-T01 through A1-T09; G-01/02/15.
RQ-04 Honest checking, no unearned verified labels -> sections 4 and 7; AC-05/06; A2; G-03/04.
RQ-05 Durable progress/results -> sections 5 and 6; AC-07/08/10/20/21; G-10/12/13.
RQ-06 Controlled interruption, no blind duplicate effects -> section 6; AC-07/08/09/17; G-12.
RQ-07 Butler presents real results or blockers -> sections 2, 4 and 5; AC-01/15/20; G-02/07/11.
RQ-08 Lattice routes participating independently identified peers -> sections 2, 4 and 8; AC-13/14/15/16; G-14.
RQ-09 Affected-consumer input, enforceable grants, no unilateral Butler/coordinator -> BA-01 through BA-06, D-01 through D-04; AC-12/13/14/16.
RQ-10 Do not bypass identity, permissions or audit for a demo -> sections 2, 6 and 8; AC-10/11/12/14/18; A1 denial/isolation invariants.
RQ-11 Existing passing tests are not missing-capability proof -> sections 9 and 12; AC-22/24; research diagnostic limitations.
RQ-12 Prioritize one narrow harmless vertical path, not more agents/CLIs -> sections 3, 10 and 11; proposed public-fixture-summary/v1 class.
RQ-13 Build workers are not runtime autonomy -> sections 8 and 10; free-lane requalification/privacy policy; no worker launched at this checkpoint.
RQ-14 Main and live runtime unchanged, preserve evidence/failures -> sections 1, 9 and 12; AC-23/24; docs-only commit and audit manifest.
RQ-15 Standing documentation -> committed spec -> approval -> implementation workflow -> section 1, section 12 and A1 approval boundary. "Start" is not retrospective approval of an unwritten specification.

## Corrections to the preceding engineering recommendation

- A report write is a narrowly permissioned side effect; the first class is input-read-only, not effect-free.
- A hosted model requires an explicitly allowed provider connection/data policy even when every task tool is offline.
- Existing MeteredBinding is Telegram-bound; local tasks must not fabricate Telegram principals to reuse it.
- Existing recoverable workflows use an OS-held lock. Do not replace that with clock-based stealing when connecting task recovery. Multi-peer assignment fencing is a different, separately specified boundary.
- Existing verification labels are held in a local unreturned list. A2 must make truthful assessments observable, not merely change those literals.
- The first maintenance repair does not require all future peer-governance decisions. The real peer-authorized task demonstration does require the identity/policy/input/grant contracts applicable to it. This does not authorize a centralized workaround.

The fixture task, numeric ceilings, standing task-class grants, new logical schemas, component sequence and new tools are engineering proposals, not recorded owner selections. Existing owner selections on Byzantine tolerance and affected input remain binding; no old contradictory spec paragraph is adopted as a new decision.
