# Hermes setup

Configure a dedicated Hermes profile that receives only user-supplied URLs and
exposes only the `obsidian-ingest` MCP tools needed for source reading and draft
creation. Disable shell, general filesystem, and other vault integrations in that
profile where the host supports it.

Use the active Hermes profile manager to add a local stdio MCP server. Replace the
placeholder with the installed skill directory. The absolute script path allows the
process to launch from any working directory:

```yaml
command: uv
args:
  - run
  - --project
  - /absolute/path/to/skills/obsidian-ingest
  - python
  - /absolute/path/to/skills/obsidian-ingest/mcp/server.py
```

Verify the loaded tool list in Hermes before using a private vault. The MCP applies
configured path restrictions to its own operations; other tools or host processes
with filesystem access can bypass them. OS isolation is optional defense in depth
and is not configured by this skill.

Use a dedicated profile. Start with a user-provided URL and review the generated
drafts in the configured draft folder. A source may produce zero drafts. Do not use
dummy draft writes to test connectivity; verify tool discovery through the harness.
