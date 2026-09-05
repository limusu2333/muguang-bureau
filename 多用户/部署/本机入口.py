"""Expose the isolated user gateway only through the Mac loopback port."""

from __future__ import annotations

import asyncio


async def _copy(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(64 * 1024):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        # 把一端的断开明确传给另一端；否则浏览器或公网客户端离开后，反向复制会
        # 永久等不到结束，长期堆积成 pending task，并拖住后续长连接。
        try:
            writer.write_eof()
        except (AttributeError, ConnectionError, OSError):
            pass


async def _forward(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter) -> None:
    try:
        gateway_reader, gateway_writer = await asyncio.wait_for(
            asyncio.open_connection("gw", 8080), timeout=3,
        )
    except (OSError, asyncio.TimeoutError):
        client_writer.close()
        await client_writer.wait_closed()
        return
    try:
        await asyncio.gather(
            _copy(client_reader, gateway_writer),
            _copy(gateway_reader, client_writer),
        )
    finally:
        gateway_writer.close()
        client_writer.close()
        await asyncio.gather(
            gateway_writer.wait_closed(), client_writer.wait_closed(),
            return_exceptions=True,
        )


async def main() -> None:
    server = await asyncio.start_server(_forward, "0.0.0.0", 8080, limit=64 * 1024)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
