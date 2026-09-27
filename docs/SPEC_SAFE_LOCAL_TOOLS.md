# Two opt-in local read-only tools — implementation checkpoint

Status: proposed implementation contract, not an enabled beta capability. Companion: `SPEC_BETA_BOUNDARY_AND_PEER_EXECUTION.md`. User requested additional tools in parallel with closing the beta gaps. This document deliberately selects two low-risk, network-free capabilities; neither is added to the beta allowlist or the deployed bot by this checkpoint.

## Scope / rationale

The existing `Tool` protocol (`src/agency/tools/base.py`) exposes `ToolSpec`, `ToolContext`, and `ToolResult`; `register_all` (`src/agency/tools/builtin/__init__.py`) registers web, memory and sandbox tools. Add `calculator` (bounded arithmetic) and `utc_time` (UTC time formatting) for explicitly nonbeta local tasks only. They do not need network, secrets, filesystem reads, memory, or peer grants. Compared with adding more web, filesystem, or execution tools, these have a smaller egress/privacy surface. Calculators must never evaluate arbitrary Python expressions; the clock must never use a caller-controlled timezone/file path. Their `READ_ONLY` label is not an authority grant. `BetaToolPolicy` continues to deny them because its default allowlist is only `web_search`.

## Interfaces

- New modules `src/agency/tools/builtin/calculator.py` and `src/agency/tools/builtin/utc_time.py` implement the existing async `Tool.run(args, ctx) -> ToolResult` protocol and export `CalculatorTool`, `UtcTimeTool`.
- `CalculatorTool.spec.name = "calculator"`, schema requires one `expression: string`, `additionalProperties: false`. Bounded length (at most 120 characters) and bounded parsed AST/depth; accept finite numbers and `+`, `-`, `*`, `/`, unary `+`/`-` and parentheses only. Reject division by zero, non-finite results, unsafe operators, calls, names, attributes and huge magnitude before expensive work. Return a finite numeric result; no `eval`/`exec`.
- `UtcTimeTool.spec.name = "utc_time"`, no arguments (`additionalProperties: false`), returns an ISO-8601 UTC timestamp. Clock injected at construction for hermetic tests, with a timezone-aware UTC result. Do not expose local timezone or filesystem paths.
- Register both via `register_all` only in explicitly nonbeta tool construction. Never add either to beta's allowlist or Telegram command menu as part of this slice. Preserve legacy existing five tools and registration behavior. If production cannot express separate beta/nonbeta registration yet, do not merge registration ahead of that boundary; keep implementation/test commits isolated until a reviewed integration seam exists.

## Verification and release gates

Test-first focused regressions for correct arithmetic/time, schema validation, hostile expressions, overflow/resource exhaustion, and no side effects. Run the full offline suite, Ruff check/format, mypy and independent review before integration. Explicit beta-policy tests must deny both even with a trusted principal and attacker-selected `allowed_tools` settings must not silently widen the deployed beta configuration. Tests use injected clocks, no live DNS/provider; do not run against canonical runtime `data/lattice.db`. A tool is not called usable merely because its class exists: prove nonbeta `register_all -> ToolRegistry.call` and beta denial end to end. Peer governance, consequential writes, browser fetch and deployment remain separate blocked gates.
