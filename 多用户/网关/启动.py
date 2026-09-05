"""Run the public and instance-control listeners as separate HTTP surfaces."""

from __future__ import annotations

import asyncio
import signal

import uvicorn


async def main() -> None:
    public = uvicorn.Server(uvicorn.Config(
        "多用户.网关.应用:public_app", host="0.0.0.0", port=8080,
        proxy_headers=False, forwarded_allow_ips="", log_level="info",
    ))
    control = uvicorn.Server(uvicorn.Config(
        "多用户.网关.应用:control_app", host="0.0.0.0", port=8081,
        proxy_headers=False, forwarded_allow_ips="", log_level="info",
    ))
    public.install_signal_handlers = lambda: None
    control.install_signal_handlers = lambda: None
    loop = asyncio.get_running_loop()

    def stop() -> None:
        public.should_exit = True
        control.should_exit = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop)
    tasks = {asyncio.create_task(public.serve()), asyncio.create_task(control.serve())}
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    if any(task.exception() for task in done if not task.cancelled()):
        stop()
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
