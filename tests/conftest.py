"""集成测试使用临时端口/数据库；不占用用户的默认服务，不调用外网。"""
from pathlib import Path
import os
import socket
import subprocess
import sys
import time
import json

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def system(tmp_path_factory):
    directory = tmp_path_factory.mktemp("real-system")
    reservations = [socket.socket() for _ in range(5)]
    for sock in reservations:
        sock.bind(("127.0.0.1", 0))
    ports = [sock.getsockname()[1] for sock in reservations]
    for sock in reservations:
        sock.close()
    environment = {**os.environ, "HOST": "127.0.0.1", "REGISTRY_PORT": str(ports[0]),
        "MATH_PORT": str(ports[1]), "WEATHER_PORT": str(ports[2]),
        "ORCHESTRATOR_PORT": str(ports[3]), "GRPC_PORT": str(ports[4]),
        "LLM_PROVIDER": "mock", "STORAGE_BACKEND": "sqlite",
        "SQLITE_PATH": str(directory / "state.sqlite3"),
        "ENABLE_MCP": "true", "ENABLE_RAG": "true", "EMBEDDING_PROVIDER": "local",
        "WEATHER_MODE": "mock", "REQUEST_TIMEOUT": "10", "HEARTBEAT_INTERVAL": "0.5",
        "HEARTBEAT_TTL": "5", "RAG_TOP_K": "5", "RAG_THRESHOLD": "0",
        "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    processes, logs = [], []
    try:
        with httpx.Client(timeout=0.5, trust_env=False) as client:
            for service, port in zip(["registry", "math", "weather", "orchestrator"], ports[:4]):
                logfile = (directory / f"{service}.log").open("w", encoding="utf-8")
                logs.append(logfile)
                process = subprocess.Popen([sys.executable, "-u", "-m", "agent_comm.server", service],
                    cwd=ROOT, env=environment, stdout=logfile, stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                processes.append(process)
                deadline = time.monotonic() + 30
                while True:
                    try:
                        response = client.get(f"http://127.0.0.1:{port}/health")
                        if response.status_code == 200:
                            if service in {"math", "weather"}:
                                rows = client.get(f"http://127.0.0.1:{ports[0]}/v1/agents").json()["agents"]
                                if not any(service in row["tags"] for row in rows):
                                    time.sleep(0.1)
                                    continue
                            break
                    except httpx.HTTPError:
                        pass
                    if process.poll() is not None or time.monotonic() > deadline:
                        logfile.flush()
                        pytest.fail((directory / f"{service}.log").read_text(encoding="utf-8"))
                    time.sleep(0.1)
        logfile = (directory / "rpc.log").open("w", encoding="utf-8")
        logs.append(logfile)
        process = subprocess.Popen([sys.executable, "-u", "-m", "agent_comm.server", "rpc"],
            cwd=ROOT, env=environment, stdout=logfile, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        processes.append(process)
        import grpc
        from agent_comm.generated import ai_query_pb2 as pb
        from agent_comm.generated import ai_query_pb2_grpc as rpc
        deadline = time.monotonic() + 20
        while True:
            try:
                with grpc.insecure_channel(f"127.0.0.1:{ports[4]}") as channel:
                    if rpc.AIQueryServiceStub(channel).Health(pb.Empty(), timeout=0.5).ready:
                        break
            except grpc.RpcError:
                pass
            if process.poll() is not None or time.monotonic() > deadline:
                logfile.flush()
                pytest.fail((directory / "rpc.log").read_text(encoding="utf-8"))
            time.sleep(0.1)
        yield {"address": f"127.0.0.1:{ports[4]}",
               "registry": f"http://127.0.0.1:{ports[0]}",
               "math": f"http://127.0.0.1:{ports[1]}",
               "weather": f"http://127.0.0.1:{ports[2]}",
               "orchestrator": f"http://127.0.0.1:{ports[3]}",
               "db": str(directory / "state.sqlite3"), "logs": str(directory)}
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for logfile in logs:
            logfile.close()

