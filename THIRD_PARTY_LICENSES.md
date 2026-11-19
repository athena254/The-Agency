# Optional third-party components

## Honcho (optional memory adapter)

- Upstream: https://github.com/plastic-labs/honcho
- License: AGPL-3.0 (server and SDK)
- Integration: separate service over HTTP via the pinned `honcho-ai` SDK
  (pinned in `pyproject.toml`); the adapter in
  `src/agency/memory/honcho/` never imports Honcho server code.
- The default SMS memory and the rest of The Agency remain MIT. Enabling the
  adapter does not change the license of the core project, but deploying a
  modified Honcho service may create AGPL obligations for that service.
- If you distribute a modified Honcho service, offer its corresponding source
  under AGPL-3.0.
