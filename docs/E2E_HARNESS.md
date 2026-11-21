# harness/END-TO-END.md — User journeys

Two to five journeys in plain English. Name the value you expect. "The page
loads" passes against an app that returns an empty body forever, so name real
value. Journeys describe what the product does TODAY (a PRD is the exception).

## Template

### Journey 1 — <name>
- Actor: <who>
- Steps: <what they do>
- Expected value: <concrete, nonempty, nonzero outcome>
- Failure signal: <what would make this journey fail>

## Current journeys for The Agency (draft — replace with real ones)

### Journey 1 — Submit a task and get a result
- Actor: human user
- Steps: open Butler gateway, submit a research task, receive a completed
  result with evidence.
- Expected value: a nonempty result containing cited sources.
- Failure signal: empty result, no citations, or error.

### Journey 2 — Inspect a change set safely
- Actor: developer/agent
- Steps: call forge.inspect_changes on a set of relative paths; receive a
  report.
- Expected value: a report with hashes and syntax verdicts; no source
  content leaked.
- Failure signal: crash, leaked source, or path escape accepted.

### Journey 3 — Run the Forge pipeline
- Actor: developer/agent
- Steps: run run_pipeline on changed paths; receive a verdict.
- Expected value: PASS only when inspection + security + tests all pass.
- Failure signal: false PASS or false FAIL.
