# Infrastructure tools

These pinned CLIs support setup and diagnostics. Installing them does not
authenticate an account, deploy an application, change a database, or connect a
broker. The supported runtime is Node 22.13+; this installation was verified
with Node 24.22.1 on Linux.

From the repository root:

```sh
python3 scripts/bootstrap_dev.py --install --infrastructure
```

Or install only these optional tools:

```sh
npm ci --prefix tools/automation/infrastructure --no-audit --no-fund
npm run --prefix tools/automation/infrastructure verify
```

The lockfile pins Netlify CLI 27.17.0, Vercel CLI 65.14.0, and Supabase CLI
3.1.0. Invoke their executables from this directory's `node_modules/.bin`.
Use each CLI's `--help` before a new operation; Supabase CLI targets and
commands have changed between major versions.

## Account connections

Prefer already-authorized ChatGPT plugins when available. The local Codex
project MCP configuration registers official Netlify and Vercel endpoints and
a read-only, project-scoped Supabase endpoint. It does not supply OAuth grants.
Trusted local Codex clients can inspect the entries with `codex mcp list` and
authenticate a selected server with `codex mcp login SERVER_NAME`. Complete
provider consent privately. Do not copy tokens or callback URLs into project
files, PRs, or chat.

Grace/ChatGPT web does not load local Codex configuration. Connect its plugins
in the account used by Grace. A Codex Cloud environment needs its own verified
repositories, setup, network access and account connections. Do not claim a
local MCP definition proves either cloud environment is connected.

The optional Robinhood definition is disabled. It is an official endpoint, but
connecting it requires the owner's OAuth/onboarding and does not implement
the bot's deterministic risk gate or multi-leg options adapter. The existing
Alpaca paper REST adapter needs no extra MCP or SDK for its implemented scope.

## Deployment targets

- Vercel terminal: `liddar-terminal-2tpb`, not the old March
  `liddar-terminal` project/domain.
- Vercel `self-learning`: verify the reported production collision and repair
  its production branch before merging the backend setup PR.
- Supabase: verify the intended personal project before any migration.
- Netlify: connection tools are prepared; no project/site migration or
  duplicate deployment is implied.

## Official references checked November 10, 2026

- [Codex MCP configuration](https://learn.chatgpt.com/docs/extend/mcp)
- [Netlify Codex setup](https://docs.netlify.com/build/build-with-ai/agent-setup-guides/set-up-codex-for-netlify/)
- [Vercel MCP](https://vercel.com/docs/agent-resources/vercel-mcp)
- [Supabase MCP scope and read-only configuration](https://supabase.com/docs/guides/ai-tools/mcp)
- [Robinhood external-agent onboarding](https://robinhood.com/us/en/support/articles/agentic-trading-overview/)
