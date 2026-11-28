"""MCP Server – transport + tool registration (FR-5.12, FR-5.13).

This file contains ONLY: server construction, tool/resource registration, main().
No business logic here — all logic lives in application/ and domain/.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any, cast

from mcp.server import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.stdio import stdio_server
from mcp.types import Resource, TextContent, Tool
from pydantic import AnyUrl

from cerebra.config import get_config, load_env_file
from cerebra.domain.errors import CerebraError
from cerebra.infrastructure.composition import build_container
from cerebra.mcp_tools.validation import (
    validate_digest,
    validate_duration_seconds,
    validate_language,
    validate_limit,
    validate_privacy_status,
    validate_proposal_id,
    validate_reference,
    validate_top_k,
    validate_topic,
)


def build_server() -> Server:
    cfg = get_config()
    # NFR-TE-1: CI sets CEREBRA_USE_FIXTURES=1; production uses the live adapter (Phase 3).
    container = build_container(cfg, use_fixtures=cfg.use_fixtures)
    app = Server("cerebra")
    _warn_pending_recoveries(container)

    # ── Tool registration (FR-5.12) ──────────────────────────────────────────

    @app.list_tools()  # type: ignore[no-untyped-call, untyped-decorator]
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="find_similar_videos",
                description=(
                    "Find videos similar to a given YouTube video. "
                    "Accepts a video URL or 11-character video ID. "
                    "Returns a ranked list of similar videos scored by metadata similarity. "
                    "READ-ONLY operation."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "reference": {
                            "type": "string",
                            "description": "YouTube video URL or 11-character video ID",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Number of results to return (1-50, default 10)",
                            "default": 10,
                        },
                    },
                    "required": ["reference"],
                },
            ),
            Tool(
                name="find_similar_channels",
                description=(
                    "Find YouTube channels similar to a given channel. "
                    "Accepts a channel URL, @handle, or channel ID. "
                    "READ-ONLY operation."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "reference": {
                            "type": "string",
                            "description": "Channel URL, @handle, or channel ID",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Number of results (1-50, default 10)",
                            "default": 10,
                        },
                    },
                    "required": ["reference"],
                },
            ),
            Tool(
                name="recommend_videos",
                description=(
                    "Recommend videos based on your YouTube likes, subscriptions and playlists. "
                    "Requires authorisation. Results are NOT based on watch history. "
                    "READ-ONLY operation."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "Number of recommendations (1-50, default 10)",
                            "default": 10,
                        },
                        "topic_filter": {
                            "type": "string",
                            "description": "Optional topic to filter recommendations",
                        },
                        "max_duration_seconds": {
                            "type": "integer",
                            "description": "Optional maximum video duration in seconds",
                        },
                    },
                },
            ),
            Tool(
                name="analyse_engagement",
                description=(
                    "Analyse your YouTube engagement: top channels, categories, "
                    "duration preferences and trends. Based on likes, subscriptions and playlists "
                    "— NOT watch history. Requires authorisation. READ-ONLY operation."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "top_k": {
                            "type": "integer",
                            "description": "Number of top channels/categories to return (default 5)",
                            "default": 5,
                        },
                        "period_a_start": {
                            "type": "string",
                            "description": "Period A start date YYYY-MM-DD",
                        },
                        "period_a_end": {
                            "type": "string",
                            "description": "Period A end date YYYY-MM-DD",
                        },
                        "period_b_start": {
                            "type": "string",
                            "description": "Period B start date YYYY-MM-DD",
                        },
                        "period_b_end": {
                            "type": "string",
                            "description": "Period B end date YYYY-MM-DD",
                        },
                    },
                },
            ),
            Tool(
                name="propose_playlist",
                description=(
                    "Propose a YouTube playlist that satisfies constraints (topic, total duration, "
                    "max video length, language). Returns a proposal for review — NOTHING IS CREATED. "
                    "Call create_playlist with the proposal_id after reviewing the proposal. "
                    "Requires authorisation."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "topic": {
                            "type": "string",
                            "description": "Topic or subject for the playlist (required)",
                        },
                        "total_duration_seconds": {
                            "type": "integer",
                            "description": "Target total playlist duration in seconds (required)",
                        },
                        "max_video_duration_seconds": {
                            "type": "integer",
                            "description": "Maximum duration per video in seconds (optional)",
                        },
                        "language": {
                            "type": "string",
                            "description": "BCP-47 language code e.g. 'en' (optional)",
                        },
                    },
                    "required": ["topic", "total_duration_seconds"],
                },
            ),
            Tool(
                name="create_playlist",
                description=(
                    "⚠️ WRITE OPERATION: Creates a YouTube playlist in your account. "
                    "Requires a proposal_id and digest from propose_playlist AND explicit human confirmation. "
                    "The AI client CANNOT approve this on your behalf — you must confirm. "
                    "Requires authorisation with write scope."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "proposal_id": {
                            "type": "string",
                            "description": "UUID from propose_playlist",
                        },
                        "confirmation_digest": {
                            "type": "string",
                            "description": "SHA-256 digest from propose_playlist (proves you reviewed the proposal)",
                        },
                        "privacy_status": {
                            "type": "string",
                            "enum": ["private", "public", "unlisted"],
                            "description": "Playlist visibility (default: private)",
                            "default": "private",
                        },
                    },
                    "required": ["proposal_id", "confirmation_digest"],
                },
            ),
        ]

    @app.call_tool()  # type: ignore[untyped-decorator]
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        try:
            result = await _dispatch(name, arguments, container)
            import json

            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]
        except CerebraError as e:
            import json

            return [TextContent(type="text", text=json.dumps(e.to_dict(), indent=2))]
        except Exception as e:
            import json

            payload = {
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": type(e).__name__,
                    "retryable": False,
                    "suggested_action": "Report this bug.",
                }
            }
            return [TextContent(type="text", text=json.dumps(payload))]

    # ── Resources (FR-5.13) ──────────────────────────────────────────────────

    @app.list_resources()  # type: ignore[no-untyped-call, untyped-decorator]
    async def list_resources() -> list[Resource]:
        return [
            Resource(
                uri=AnyUrl("cerebra://quota_status"),
                name="quota_status",
                description="Current YouTube API quota usage for all pools",
                mimeType="application/json",
            ),
            Resource(
                uri=AnyUrl("cerebra://auth_status"),
                name="auth_status",
                description="Current OAuth authorisation status",
                mimeType="application/json",
            ),
        ]

    @app.read_resource()  # type: ignore[no-untyped-call, untyped-decorator]
    async def read_resource(uri: str) -> list[ReadResourceContents]:
        import json

        def _json(payload: Any) -> list[ReadResourceContents]:
            return [
                ReadResourceContents(
                    content=json.dumps(payload, indent=2), mime_type="application/json"
                )
            ]

        if str(uri) == "cerebra://quota_status":
            return _json(container.quota.status())
        if str(uri) == "cerebra://auth_status":
            out: dict[str, Any] = {"authenticated": bool(container.is_authenticated)}
            if getattr(container, "oauth", None) is not None:
                assert container.oauth is not None
                out.update(container.oauth.status())  # scopes/expiry hints; never tokens
            return _json(out)
        return _json({"error": "unknown resource"})

    return app


async def _dispatch(name: str, args: dict[str, Any], container: Any) -> dict[str, Any]:
    """Route tool calls to use cases (FR-5.12)."""
    if name == "find_similar_videos":
        ref = validate_reference(args.get("reference"))
        limit = validate_limit(args.get("limit"))
        results = await container.find_similar.find_similar_videos(ref, limit)
        return {
            "results": [
                {
                    "rank": r.rank,
                    "video_id": r.video_id,
                    "title": r.title,
                    "channel": r.channel_title,
                    "duration": r.duration_human(),
                    "url": r.url,
                    "score": round(r.score, 4),
                    "tags_missing": r.tags_missing,
                }
                for r in results
            ]
        }

    elif name == "find_similar_channels":
        ref = validate_reference(args.get("reference"))
        limit = validate_limit(args.get("limit"))
        results = await container.find_similar.find_similar_channels(ref, limit)
        return {
            "results": [
                {
                    "rank": r.rank,
                    "channel_id": r.channel_id,
                    "title": r.title,
                    "url": r.url,
                    "score": round(r.score, 4),
                    "subscriber_count": r.subscriber_count,
                }
                for r in results
            ]
        }

    elif name == "recommend_videos":
        limit = validate_limit(args.get("limit"))
        lang = validate_language(args.get("language"))
        max_dur = None
        if "max_duration_seconds" in args and args["max_duration_seconds"] is not None:
            max_dur = validate_duration_seconds(
                args["max_duration_seconds"], "max_duration_seconds"
            )
        return cast(
            dict[str, Any],
            await container.recommend.recommend(
                limit=limit, topic_filter=args.get("topic_filter"), max_duration_seconds=max_dur
            ),
        )

    elif name == "analyse_engagement":
        top_k = validate_top_k(args.get("top_k"))
        period_a = period_b = None
        if args.get("period_a_start") and args.get("period_a_end"):
            period_a = (str(args["period_a_start"]), str(args["period_a_end"]))
        if args.get("period_b_start") and args.get("period_b_end"):
            period_b = (str(args["period_b_start"]), str(args["period_b_end"]))
        return cast(
            dict[str, Any],
            await container.analyse.analyse(top_k=top_k, period_a=period_a, period_b=period_b),
        )

    elif name == "propose_playlist":
        topic = validate_topic(args.get("topic"))
        total = validate_duration_seconds(
            args.get("total_duration_seconds"), "total_duration_seconds"
        )
        max_vid = None
        if args.get("max_video_duration_seconds") is not None:
            max_vid = validate_duration_seconds(
                args["max_video_duration_seconds"], "max_video_duration_seconds"
            )
        lang = validate_language(args.get("language"))
        return cast(
            dict[str, Any],
            await container.propose.propose(topic, total, max_vid, lang),
        )

    elif name == "create_playlist":
        pid = validate_proposal_id(args.get("proposal_id"))
        digest = validate_digest(args.get("confirmation_digest"))
        ps = validate_privacy_status(args.get("privacy_status"))
        return cast(
            dict[str, Any],
            await container.create_pl.create(pid, digest, ps),
        )

    else:
        from cerebra.domain.errors import CerebraError, ErrorCode

        raise CerebraError(
            ErrorCode.INVALID_ARGUMENT,
            f"Unknown tool: {name!r}",
            suggested_action="Check the tool name.",
        )


def _warn_pending_recoveries(container: Any) -> None:
    """ADR-C §3: surface PARTIAL proposals at startup. stderr only (stdout is MCP JSON).

    Startup performs NO writes: finishing an interrupted write is an explicit human action
    (`cerebra-cli recover <id>`), which first reads remote state.
    """
    recover = getattr(container, "recover", None)
    if recover is None:
        return
    try:
        pending = recover.pending()
    except Exception:
        return
    for p in pending:
        print(  # noqa: T201 - stderr notice; stdout is the MCP channel
            f"cerebra: proposal {p.proposal_id} is {p.state.value}; "
            f"run `cerebra-cli recover {p.proposal_id}`",
            file=sys.stderr,
        )


def main() -> None:
    load_env_file()  # optional untracked .env (FR-5.2); real env vars win

    async def _run() -> None:
        server = build_server()
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    asyncio.run(_run())


if __name__ == "__main__":
    main()
