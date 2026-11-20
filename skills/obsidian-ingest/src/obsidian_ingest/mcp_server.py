"""Thin local MCP adapter for source navigation and create-only draft writing.

Operations, validation, limits, result data, and error codes live in
`obsidian_ingest.responses`; this module only maps tool arguments and envelopes.
"""

import inspect
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Any, Literal, NotRequired, TypedDict, get_type_hints

from mcp.server.mcpserver import Image
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, ConfigDict, ValidationError, create_model, with_config

from mcp.server import MCPServer
from obsidian_ingest import responses
from obsidian_ingest.config import VaultSettings
from obsidian_ingest.drafts import DraftWriterSettings
from obsidian_ingest.responses import (
    DEFAULT_NOTE_READ_LIMIT,
    DEFAULT_PAGE_SIZE,
    DEFAULT_SEARCH_LIMIT,
    MAX_PAGE_ENVELOPE_BYTES,
    MAX_PAGE_SIZE,
    compact_json,
)
from obsidian_ingest.source import Source
from obsidian_ingest.source_cache import SourceCache
from obsidian_ingest.vault import VaultReader

__all__ = ["DEFAULT_PAGE_SIZE", "MAX_PAGE_ENVELOPE_BYTES", "MAX_PAGE_SIZE", "create_server"]


_STRICT_ARGUMENTS = ConfigDict(strict=True, extra="forbid")


@with_config(_STRICT_ARGUMENTS)
class RelatedNoteInput(TypedDict):
    # Keep documentation out of the docstring: Pydantic exposes it in the tool schema.
    # Revision is accepted for compatibility; ordinary links only require current access.
    note_id: str
    reason: str
    revision: NotRequired[str]


def _strict_argument_model(function: Callable[..., Any]) -> type[BaseModel]:
    """Build a strict model of a tool's parameters: no coercion and no unknown arguments."""
    hints = get_type_hints(function)
    fields: dict[str, Any] = {}
    for name, parameter in inspect.signature(function).parameters.items():
        default = ... if parameter.default is inspect.Parameter.empty else parameter.default
        fields[name] = (hints[name], default)
    return create_model(f"{function.__name__}_arguments", __config__=_STRICT_ARGUMENTS, **fields)


_ARGUMENT_HINTS = {
    "read_token": "pass the read_token from your final read_source page",
    "cite": "list the source_index numbers (first value of each row) of the rows used",
    "offset": "pass the previous page's next_offset value as offset",
}


def _describe_invalid_arguments(error: ValidationError) -> str:
    """Summarize field failures without echoing untrusted rejected argument values."""
    # Field locations and messages only; rejected values are the caller's data.
    problems = []
    for item in error.errors(include_url=False):
        field = ".".join(str(part) for part in item["loc"]) or "arguments"
        hint = _ARGUMENT_HINTS.get(field)
        problems.append(f"{field}: {item['msg']}" + (f" ({hint})" if hint else ""))
    return "; ".join(problems)


class _StrictArgumentServer(MCPServer):
    """Reject arguments the shared layer would reject before the SDK's lenient coercion.

    The SDK validates in lax mode (true -> 1, "5" -> 5, JSON strings -> lists) and ignores
    unknown arguments, and reports schema failures as uncoded text. Validating the raw
    arguments strictly first gives the CLI's meaning: every malformed, missing, or unknown
    argument is ``INVALID_ARGUMENT``.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Keep strict validators alongside the SDK's registered tools."""
        super().__init__(*args, **kwargs)
        self._strict_models: dict[str, type[BaseModel]] = {}

    def tool(self, *args: Any, **kwargs: Any) -> Callable[[Callable[..., Any]], Any]:
        """Register each tool with both the SDK and a strict raw-argument model."""
        register = super().tool(*args, **kwargs)

        def decorator(function: Callable[..., Any]) -> Any:
            """Preserve the SDK registration while indexing validation by effective name."""
            name = kwargs.get("name") or function.__name__
            self._strict_models[name] = _strict_argument_model(function)
            return register(function)

        return decorator

    async def call_tool(self, name: str, arguments: dict[str, Any], context: Any = None) -> Any:
        """Validate raw input first, then let the SDK execute and envelope the tool."""
        model = self._strict_models.get(name)
        if model is not None:
            try:
                model.model_validate(arguments)
            except ValidationError as error:
                raise ToolError(
                    f"Error executing tool {name}: "
                    f"INVALID_ARGUMENT: {_describe_invalid_arguments(error)}"
                ) from error
        return await super().call_tool(name, arguments, context)


def _tool_errors[**P, R](function: Callable[P, R]) -> Callable[P, R]:
    """Report expected application failures as coded tool errors."""

    @wraps(function)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        """Translate only known failures; unexpected exceptions retain their traceback."""
        try:
            return function(*args, **kwargs)
        except ToolError:
            raise
        except Exception as error:
            mapped = responses.as_operation_error(error)
            if mapped is None:
                raise
            raise ToolError(str(mapped)) from error

    return wrapper


def create_server(
    settings: DraftWriterSettings,
    *,
    cache_db: Path | None = None,
    vault_settings: VaultSettings | None = None,
) -> MCPServer:
    """Create an MCP server backed by persistent, option-specific source handles."""
    server = _StrictArgumentServer("obsidian-ingest")
    if vault_settings is not None and (
        settings.vault_path.resolve() != vault_settings.root.resolve()
        or settings.draft_folder != vault_settings.draft_folder
    ):
        raise ValueError("Draft writer settings must match trusted vault configuration")
    vault = VaultReader(vault_settings) if vault_settings is not None else None
    opened: list[SourceCache] = []

    def cache() -> SourceCache:
        """Lazily retain the first successfully opened acquisition cache."""
        # Open per use until it succeeds, like each CLI invocation: an unusable cache is
        # reported as SOURCE_CACHE_ERROR by source tools instead of preventing startup,
        # and vault tools keep working.
        if not opened:
            opened.append(SourceCache(cache_db))
        return opened[0]

    def load_source(source_id: str) -> Source:
        """Resolve persistent source handles through the transport-neutral error mapping."""
        return responses.load_source(cache(), source_id)

    @server.tool()
    @_tool_errors
    def ingest_source(
        url: str, exclude_sponsors: bool = True, refresh: bool = False
    ) -> dict[str, Any]:
        """Ingest a source URL; reuse its persistent capture, refresh re-extracts. Sponsor segments are excluded by default."""
        return responses.ingest(cache(), url, exclude_sponsors=exclude_sponsors, refresh=refresh)

    @server.tool()
    @_tool_errors
    def get_source_overview(source_id: str) -> dict[str, Any]:
        """Get source metadata, exclusions, and acquisition evidence without the source text."""
        return responses.source_overview(load_source(source_id))

    @server.tool()
    @_tool_errors
    def list_sections(source_id: str) -> list[dict[str, Any]]:
        """List sections (creator chapters for videos) with zero-based indices."""
        return responses.sections(load_source(source_id))

    @server.tool()
    @_tool_errors
    def list_exclusions(source_id: str) -> list[dict[str, Any]]:
        """List excluded ranges (such as sponsor segments) and the source indices they remove."""
        return responses.exclusions(load_source(source_id))

    # Compact text avoids duplicating bounded source rows in structured output.
    @server.tool(structured_output=False)
    @_tool_errors
    def read_source(
        source_id: str,
        start: float | None = None,
        end: float | None = None,
        include_excluded: bool = False,
        offset: int = 0,
        limit: int = DEFAULT_PAGE_SIZE,
        read_token: str | None = None,
    ) -> str:
        """Read the source in compact JSON pages, in order: pass each page's next_offset as offset, with its read_token, until next_offset is null. Writing requires the final read_token."""
        page = responses.source_page(
            load_source(source_id),
            start=start,
            end=end,
            include_excluded=include_excluded,
            offset=offset,
            limit=limit,
            read_token=read_token,
        )
        return compact_json(page)

    @server.tool(structured_output=False)
    @_tool_errors
    def read_section(
        source_id: str,
        section_index: int,
        include_excluded: bool = False,
        offset: int = 0,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> str:
        """Read one section in compact JSON pages; pass next_offset as offset until it is null."""
        page = responses.section_page(
            load_source(source_id),
            section_index,
            include_excluded=include_excluded,
            offset=offset,
            limit=limit,
        )
        return compact_json(page)

    @server.tool(structured_output=False)
    @_tool_errors
    def get_thumbnail(source_id: str) -> list[Image | str]:
        """Optional: fetch the source's thumbnail image for a vision-capable model. Not needed to understand the source; skip it if you cannot view images."""
        result = responses.thumbnail(load_source(source_id))
        image = Image(data=result.data, format=result.image.mime_type.removeprefix("image/"))
        return [image, compact_json(result.summary())]

    @server.tool()
    @_tool_errors
    def create_draft(
        source_id: str,
        contents: str,
        read_token: str,
        filename: str | None = None,
        cite: list[int] | None = None,
        related_notes: list[RelatedNoteInput] | None = None,
    ) -> dict[str, str]:
        """Create a source-attributed draft. Start contents with an H1 that is the note's title (any characters allowed); no source section. read_token is the token from your final read_source page; writing is refused until the whole source was read. filename is optional and derived from the H1 as kebab-case. Pass related_notes ({note_id from search_vault, reason}) for every vault note the idea connects to, and cite: the source_index (first value of each row) of the rows the note uses."""
        return responses.write_draft(
            settings,
            load_source(source_id),
            contents=contents,
            filename=filename,
            cite=cite,
            related_notes=[dict(item) for item in related_notes] if related_notes else None,
            vault=vault,
            read_token=read_token,
        )

    # Without trusted read scopes, expose publication but no vault navigation/edit tools.
    if vault is not None:

        @server.tool(structured_output=False)
        @_tool_errors
        def search_vault(
            query: str = "",
            scope: Literal["title", "content", "both"] = "both",
            cursor: str | None = None,
            limit: int = DEFAULT_SEARCH_LIMIT,
        ) -> str:
            """Search note filenames and content. Each query word is matched on its own as a whole word; notes matching the most distinct words come first, with matched_terms listed. Use specific words, not common ones. Empty query browses notes."""
            return compact_json(
                responses.search_vault(vault, query, scope=scope, cursor=cursor, limit=limit)
            )

        @server.tool(structured_output=False)
        @_tool_errors
        def read_vault_note(
            note_id: str,
            offset: int = 0,
            limit: int = DEFAULT_NOTE_READ_LIMIT,
            revision: str | None = None,
        ) -> str:
            """Read bounded pages of one accessible note; pass its revision on continuation pages."""
            return compact_json(
                responses.read_vault_note(
                    vault, note_id, offset=offset, limit=limit, revision=revision
                )
            )

        @server.tool()
        @_tool_errors
        def create_edit_suggestion(
            source_id: str,
            target_note_id: str,
            target_revision: str,
            change_summary: str,
            reasoning: str,
            proposed_content: str,
            read_token: str,
            cite: list[int] | None = None,
        ) -> dict[str, str]:
            """Create a review-only edit-<note>.md draft for a verified accessible note; never changes it. proposed_content is only the new or changed lines, not a copy of the note, with no Source or Related Notes section. read_token is the token from your final read_source page; cite lists the source_index of the rows used."""
            return responses.write_edit_suggestion(
                settings,
                load_source(source_id),
                vault,
                target_note_id=target_note_id,
                target_revision=target_revision,
                change_summary=change_summary,
                reasoning=reasoning,
                proposed_content=proposed_content,
                cite=cite,
                read_token=read_token,
            )

    return server
