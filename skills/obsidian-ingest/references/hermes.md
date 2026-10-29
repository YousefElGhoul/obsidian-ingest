# Hermes setup

`scripts/install-hermes.sh` converts one selected named Hermes profile for
Obsidian Ingest. Ask which profile to use and obtain explicit vault settings before
running it. Do not change the profile's provider/model settings or existing vault
permissions without the user's explicit choices.

The installer supports Hermes 0.22.5 CLI/TUI profiles. It selects only the
`obsidian-ingest` MCP toolset, limits that server to the ten ingestion tools, disables
Hermes skill management/tool search and other toolsets, disables project and external
skill roots, and moves other profile-local skills into a private recovery archive. It
backs up the existing Hermes config, profile `SOUL.md`, and skill settings. Unsupported
Hermes versions, non-CLI messaging profiles, malformed configuration, and failed
effective-tool checks stop installation rather than weakening the restrictions.

Example (replace every path and permission with user-provided values):

```bash
scripts/install-hermes.sh \
  --profile-home "$HOME/.hermes/profiles/obsidian" \
  --vault-root "$HOME/Documents/My Vault" \
  --allow "Projects" \
  --exclude "Projects/Private" \
  --draft-folder "00 Inbox/AI Drafts"
```

Repeat the command to update the managed configuration. Existing vault settings are
preserved unless `--reconfigure-vault` is explicitly supplied with a complete new
`--vault-root` and `--allow` set. Restore the pre-install managed configuration with:

```bash
scripts/install-hermes.sh --profile-home "$HOME/.hermes/profiles/obsidian" --restore
```

The recovery archive remains in the profile's `.obsidian-ingest-installer/` directory.
Restore refuses to overwrite config, `SOUL.md`, vault settings, or skill paths that
changed after installation. Review and reconcile those changes manually if restore
stops.

After success, start a **fresh Hermes session** in that profile. This installer does
not restrict an already-running session. The configured MCP enforces its own path
policy, but the profile's other host processes are outside that policy; OS isolation is
not set up by this command. The selected profile and configured filesystem must be
trusted against concurrent modification.

Hermes compatibility is intentionally pinned to the version verified by the installer.
Messaging gateways and other Hermes versions are unsupported until their effective tool
surface is separately verified.
