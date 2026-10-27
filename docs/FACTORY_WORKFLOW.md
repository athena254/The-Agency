# Factory workflow (4 steps)

A software factory is a workflow of skills and domain knowledge. It runs with
any model and any harness. The whole system lives in a handful of markdown
files, so it stays portable.

## 1. Isolate
Every feature starts in a fresh git worktree branched from origin/main. Each
agent keeps its own station. Never edit main directly. See
`src/agency/forge/worktree.py`.

## 2. Build
Write service-layer code with explicit inputs and structured returns. A human
developer must be able to read it. No cleverness.

## 3. Prove
Record a before state and an after state as video, screenshots, or numbers.
Tests alone are not enough; show the behavior changed. See
`harness/END-TO-END.md`.

## 4. Ship
An independent reviewer (NYX or Greptile) scores the PR; loop back to build
until it earns 5/5 with zero unresolved comments. A human reviews the visual
proof, not the raw code, before merge.
