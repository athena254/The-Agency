# Tools import catalog (research, 2026-11-20)

Discovered GitHub tools/plugins that the Agency can integrate. Each entry: purpose, repo, license, install, integration status.

## Computer use
- Anthropic Computer Use — browser/desktop control. Status: stub adapter (requires API + sandbox). Repo: anthropic computer-use (via awesome-web-agents).

## Web scraping
- Crawl4AI — LLM-friendly crawler, Markdown + JSON-schema extraction. MIT. `pip install crawl4ai`
- ScrapeGraphAI — LLM + graph extraction, multi-provider. `pip install scrapegraphai`
- Scrapling — adaptive parser, Turnstile bypass, MCP server. `pip install scrapling`
- webTool — Playwright all-in-one, SSRF/robots/rate-limit safety. `pip install web-agent`
- Firecrawl (hosted) — scrape/crawl/map/search/interact. API key.
- Jina Reader — prepend r.jina.ai/ to any URL for LLM text. Free, no key.

## Web search
- SerpAPI — multi-engine search. `pip install serpapi-search-tools-python` (native tool constructors)
- Brave Search API — free tier.
- Tavily — agent-native search. 1000 free/month.
- Exa — semantic search for agents.
- agentsearcher — self-hosted, CPU-only, Tavily-compatible MCP, no keys. `pipx install agentsearcher`
- agent-search-stack — self-hosted SearXNG + 16 MCP tools.

## Research (engagement-ranked, multi-source)
- mvanhorn/last30days-skill — Reddit/X/YouTube/HN/Polymarket/GitHub/web, engagement-ranked brief. MIT. `npx skills add mvanhorn/last30days-skill -g` or clone. 70k stars.

## Google Workspace
- googleworkspace/cli (gws) — Rust, dynamic Discovery Service, 100+ agent skills (SKILL.md), MCP server. Apache-2.0. `npm install -g @googleworkspace/cli`. Auth via `gws auth setup && gws auth login`.

## Hermes plugins
- NousResearch/hermes-official-plugins — Home Assistant, Spotify, Claude direct, memory wiki, MCP serve, etc.
- korchasa/awesome-hermes-plugins — curated list (tools, memory, context engines, browser backends).
- Hermes Tweet (Xquik-dev/hermes-tweet), hermes-claude-code (MCP bridge to Claude Code), cc-import (import Claude Code plugins into Hermes).

## Claude Code plugins
- pleaseai/claude-code-plugins — cross-runtime marketplace (Claude Code, Codex, Antigravity).
- bbx06/claude-code-hermes-plugin — MCP bridge Claude Code ↔ Hermes.
- futuretyler/cc-import — import Claude Code plugins (skills + agents) into Hermes.
- Chrome DevTools MCP, Playwright CLI, Grafana MCP, Vercel, etc.

## Integration plan (Agency)
- Add tool adapters in `src/agency/tools/builtin/external.py` (or `tools/external.py`) wrapping: web scrape (httpx + trafilatura/Jina fallback), gws CLI, last30days, computer-use stub.
- Register via `register_external_tools()` (opt-in, like `register_opt_in_local_tools`).
- Document install commands; tools fail gracefully (ok=False) when binary/API missing.
- Forge `build_tool` can author new tools from these patterns.
EOF