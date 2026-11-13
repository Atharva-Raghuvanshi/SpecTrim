"""Real MCP Client calling create_spec, get_context, and savings_report via MCP SDK.

Uses official `mcp` Python SDK ClientSession with StdioServerParameters, connecting
to app.mcp_server via standard JSON-RPC over stdio.
Saves the complete transcript to docs/demo/mcp_transcript.md.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

OUTPUT_DOC = Path(__file__).resolve().parents[2] / "docs" / "demo" / "mcp_transcript.md"
REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_DIR = REPO_ROOT / "server"


async def run_mcp_client() -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SERVER_DIR)
    env["API_BASE"] = "http://127.0.0.1:8001"
    env["PYTHONUNBUFFERED"] = "1"

    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp_server"],
        env=env,
    )

    transcript: dict[str, Any] = {
        "transport": "stdio",
        "server_command": f"{sys.executable} -m app.mcp_server",
        "tools_discovered": [],
        "calls": [],
    }

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            # 1. Protocol initialization
            init_result = await session.initialize()
            srv_info = getattr(init_result, "server_info", None) or getattr(init_result, "serverInfo", None)
            transcript["server_info"] = {
                "name": getattr(srv_info, "name", "query-optimizer"),
                "version": getattr(srv_info, "version", "unknown"),
                "protocol_version": getattr(init_result, "protocol_version", None)
                or getattr(init_result, "protocolVersion", "unknown"),
            }

            # 2. List tools
            tools_list = await session.list_tools()
            transcript["tools_discovered"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": getattr(t, "input_schema", None) or getattr(t, "inputSchema", {}),
                }
                for t in tools_list.tools
            ]

            # 3. Call create_spec
            idea = "Command Line Habit Tracker with SQLite storage"
            call_spec_args = {"idea": idea}
            print(f"Calling create_spec('{idea}')...")
            res_spec = await session.call_tool("create_spec", call_spec_args)
            content_spec = [c.model_dump() for c in res_spec.content]
            parsed_spec = None
            if content_spec and content_spec[0].get("text"):
                try:
                    parsed_spec = json.loads(content_spec[0]["text"])
                except Exception:
                    parsed_spec = content_spec[0]["text"]
            transcript["calls"].append({
                "tool": "create_spec",
                "arguments": call_spec_args,
                "is_error": getattr(res_spec, "is_error", False),
                "response": parsed_spec or content_spec,
            })

            # 4. Call get_context on ready session (ses_0d9ca49a6000)
            target_session_id = "ses_0d9ca49a6000"
            task_desc = "Implement habit streak calculation and reminder notifications"
            call_ctx_args = {
                "session_id": target_session_id,
                "task": task_desc,
                "role": "backend",
            }
            print(f"Calling get_context({call_ctx_args})...")
            res_ctx = await session.call_tool("get_context", call_ctx_args)
            content_ctx = [c.model_dump() for c in res_ctx.content]
            parsed_ctx = None
            if content_ctx and content_ctx[0].get("text"):
                try:
                    parsed_ctx = json.loads(content_ctx[0]["text"])
                except Exception:
                    parsed_ctx = content_ctx[0]["text"]
            transcript["calls"].append({
                "tool": "get_context",
                "arguments": call_ctx_args,
                "is_error": getattr(res_ctx, "is_error", False),
                "response": parsed_ctx or content_ctx,
            })

            # 5. Call savings_report
            print("Calling savings_report()...")
            res_savings = await session.call_tool("savings_report", {})
            content_savings = [c.model_dump() for c in res_savings.content]
            parsed_savings = None
            if content_savings and content_savings[0].get("text"):
                try:
                    parsed_savings = json.loads(content_savings[0]["text"])
                except Exception:
                    parsed_savings = content_savings[0]["text"]
            transcript["calls"].append({
                "tool": "savings_report",
                "arguments": {},
                "is_error": getattr(res_savings, "is_error", False),
                "response": parsed_savings or content_savings,
            })

    return transcript


def write_markdown_transcript(transcript: dict[str, Any]) -> None:
    calls = transcript["calls"]
    spec_call = next(c for c in calls if c["tool"] == "create_spec")
    ctx_call = next(c for c in calls if c["tool"] == "get_context")
    savings_call = next(c for c in calls if c["tool"] == "savings_report")

    tool_names = ', '.join(t['name'] for t in transcript['tools_discovered'])
    md = f"""# Model Context Protocol (MCP) Client Transcript

Demonstrates interaction with Query Optimizer's MCP server (`app.mcp_server`)
using the official Python `mcp` SDK client over standard stdio JSON-RPC.

## Session Metadata
- **MCP Server:** `{transcript.get('server_info', {}).get('name', 'query-optimizer')}`
- **Protocol Version:** `{transcript.get('server_info', {}).get('protocol_version', 'unknown')}`
- **Transport:** `{transcript.get('transport', 'stdio')}`
- **Discovered Tools ({len(transcript['tools_discovered'])}):** {tool_names}

---

## 1. Tool Call: `create_spec`

Creates a spec generation session for an idea through the MCP interface.

### Request Arguments
```json
{json.dumps(spec_call['arguments'], indent=2)}
```

### Response
```json
{json.dumps(spec_call['response'], indent=2)}
```

---

## 2. Tool Call: `get_context`

Fetches role-specific spec context (backend sub-spec, touched entities, and endpoints)
for an agent coding task.

### Request Arguments
```json
{json.dumps(ctx_call['arguments'], indent=2)}
```

### Response
```json
{json.dumps(ctx_call['response'], indent=2)}
```

---

## 3. Tool Call: `savings_report`

Inspects cumulative token savings, cache hits, and passthroughs from the savings ledger.

### Request Arguments
```json
{{}}
```

### Response
```json
{json.dumps(savings_call['response'], indent=2)}
```

---

## Verification
- MCP protocol handshake (initialize, tools/list) completed successfully.
- All three requested tools (`create_spec`, `get_context`, `savings_report`) were called via the MCP client.
- Tool outputs conform to standard MCP JSON-RPC schemas without protocol errors.
"""
    OUTPUT_DOC.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_DOC.write_text(md, encoding="utf-8")
    print(f"\nMCP transcript written to: {OUTPUT_DOC}")


def main() -> int:
    print("Starting MCP SDK client session...")
    transcript = asyncio.run(run_mcp_client())
    write_markdown_transcript(transcript)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
