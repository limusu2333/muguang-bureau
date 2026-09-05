"""Forward only the internal reranker bridge to the host loopback service."""

from __future__ import annotations

import asyncio
import os


async def _copy(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(64 * 1024):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        try:
            writer.write_eof()
        except (AttributeError, ConnectionError, OSError):
            pass


async def _forward(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter) -> None:
    target_host = os.environ.get("XJ_RELAY_TARGET_HOST", "host.docker.internal")
    target_port = int(os.environ.get("XJ_RELAY_TARGET_PORT", "37657"))
    if target_host != "host.docker.internal" or not 1024 <= target_port <= 65535:
        client_writer.close()
        await client_writer.wait_closed()
        return
    try:
        host_reader, host_writer = await asyncio.wait_for(
            asyncio.open_connection(target_host, target_port), timeout=3,
        )
    except (OSError, asyncio.TimeoutError):
        client_writer.close()
        await client_writer.wait_closed()
        return
    try:
        await asyncio.gather(
            _copy(client_reader, host_writer),
            _copy(host_reader, client_writer),
        )
    finally:
        host_writer.close()
        client_writer.close()
        await asyncio.gather(
            host_writer.wait_closed(), client_writer.wait_closed(),
            return_exceptions=True,
        )


async def main() -> None:
    server = await asyncio.start_server(_forward, "0.0.0.0", 4101, limit=64 * 1024)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
