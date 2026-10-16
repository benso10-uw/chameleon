# Agent handoff

Before operating or changing the local web UI, read:

- [Web UI handoff for LLMs](docs/agents/webui-handoff.md): startup, exact controls,
  trace interpretation, troubleshooting, code map and verification.
- [User operating guide](docs/WORKBENCH.md).
- [Measured quality and speed findings](docs/QUALITY-AND-SPEED.md).

Read `CONTEXT.md` and relevant `docs/adr/` decisions before engine changes.
Keep credentials in ignored local configuration; never print or commit `.env`
contents. Generated results under `output/` are ignored and should stay local.
Do not push commits or send GitHub messages unless the user requests it.
