# Qualification repair follow-up contract

Base: `6e14d07`. No release, deployment, provider call or authority expansion is authorized.

The full offline run did not complete: it emitted failures in the cross-process beta admission test and bridge health test, then spent a long interval formatting a caught Lattice exception. No JUnit summary was produced. Zero totals in the timeout metadata mean missing results, not zero failures. Narrow probes reproduce both failures.

## Hermetic bridge health tests

The existing `tests/test_bridges.py::test_bridges_health_check_bool` calls real default transports despite the module claiming all transports are mocked. The network guard correctly rejects Codex's external DNS lookup. CLI health methods can also inspect/run installed CLI authentication/version probes; the offline suite must not depend on host accounts or binaries.

Owner: coordinator. Edit only `tests/test_bridges.py` for this slice; production bridge implementations and networking guards remain unchanged.

Replace host probes with injected `httpx.MockTransport` clients and mocked subprocess/discovery/ACP round-trip seams, while executing the real `health_check` methods. Preserve the original test node and strengthen its assertions: actual healthy booleans, expected HTTP paths, configured CLI commands and ACP initialize request, and injected-client ownership. Add unhealthy HTTP status/transport-error and absent/failing CLI branches. No model or real CLI call is permitted, including when setup fails. Explicitly close injected HTTP clients in the test.

Acceptance: the original isolated failure is recorded; all bridge health/coordinator tests pass under external socket/DNS denial and echo configuration; scoped Ruff lint/format and diff checks pass. A successful mock probe does not establish live bridge health or authentic peer operation.

## Separate unresolved investigations

- Cross-process admission: record parent/child startup, ready enqueue/delivery and gate timing. Failed readiness means no valid overlap experiment; worker gate timeouts must never silently permit execution. Preserve the one-pending-call and zero-unauthorized-call assertions. No production admission fix or longer deadline without causal evidence and a reviewed contract.
- Lattice lifecycle: inspect all actual owners/borrowers, concurrent acquisition, failed initialization and event-loop boundaries. Compare explicit leases with owned handles before changing singleton semantics. Preserve local backend behavior and all HTTP default-deny gates. A closed shared handle is not repaired by suppressing its exception log.
- Logging performance: retain diagnostic evidence and separate it from the caught underlying storage error; do not silence warnings or change verification criteria to obtain a pass.

These investigations may produce proposed contracts but do not authorize a new consensus, voting policy, persistence scope, retention policy or test-deadline increase.
