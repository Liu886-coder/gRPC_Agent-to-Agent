"""分别启动 registry / math / weather / orchestrator / rpc，每种服务使用独立进程。"""


import argparse
import asyncio
import logging
import uvicorn

from .config import Settings
from .agents import create_agent_app
from .registry import create_registry_app


def main():
    parser = argparse.ArgumentParser(description="Agent Communication RPC Framework")
    parser.add_argument("service", choices=["registry", "math", "weather", "orchestrator", "rpc"])
    args = parser.parse_args()
    settings = Settings.from_env()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.service == "rpc":
        from .rpc_server import serve
        try:
            asyncio.run(serve(settings))
        except KeyboardInterrupt:
            pass
        return
    if args.service == "registry":
        app, port = create_registry_app(settings), settings.registry_port
    else:
        app = create_agent_app(args.service, settings)
        port = settings.service_ports[args.service]
    uvicorn.run(app, host=settings.host, port=port, log_level="info", access_log=False)


if __name__ == "__main__":
    main()

