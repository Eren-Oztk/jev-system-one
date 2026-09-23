#!/usr/bin/env python
"""MCP üzerinden gerçek araç çağrısı: Hermes'in yapacağı çağrının aynısı.

Kullanım: .venv\\Scripts\\python.exe scripts\\mcp_probe.py
"""

from __future__ import annotations

import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = StdioServerParameters(
    command=sys.executable,
    args=["-m", "systemone.mcp_server"],
)


async def main() -> int:
    async with stdio_client(SERVER) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("tools:", [t.name for t in tools.tools])

            r = await session.call_tool(
                "system_one",
                {
                    "state": {
                        "channel": "e-posta",
                        "message": "Merhaba, sitemizdeki iletişim formundan teklif istedik ama "
                                   "3 gündür kimse dönmedi. Bir de faturamızda iki kalem görünüyor, "
                                   "acilen netleşmesi lazım.",
                    },
                    "pack": "support_triage",
                    "engine": "auto",
                },
            )
            payload = r.content[0].text if r.content else "{}"
            data = json.loads(payload)
            print("engine :", data["engine"], "|", data["model"])
            print("verdict:", data["verdict"], "| latency_ms:", data["latency_ms"])
            for qid, a in data["answers"].items():
                print(f"  {qid:<20} {a['type']:<6} {a['value']!r} {a.get('yes')} [{a['verdict']}]")

            p = await session.call_tool("jev_packs", {})
            packs = json.loads(p.content[0].text)
            print("pack'ler:", sorted(packs))

            d = await session.call_tool("jev_doctor", {"live": False})
            print("doctor  :", json.loads(d.content[0].text))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
