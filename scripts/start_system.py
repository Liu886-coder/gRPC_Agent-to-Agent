"""前台监督 5 个服务（新增 weather）。Ctrl+C 统一退出；日志写入 logs。"""
from pathlib import Path
import argparse
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import json

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent_comm.config import Settings

# start_system.py：启动并管理整套服务
#         ↓ 分别创建五个进程：注册中心、数学、天气、协调器、RPC
# server.py：决定每个进程运行哪种服务
#         ↓
# registry.py / agents.py / rpc_server.py：实现具体功能


def http_get(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=1) as response:
        return json.loads(response.read())


def main():
    parser = argparse.ArgumentParser(description="启动并监督全部服务")
    parser.add_argument("--check", action="store_true", help="启动、执行冒烟测试、自动停止")
    args = parser.parse_args()
    settings = Settings.from_env()
    (ROOT / "logs").mkdir(exist_ok=True)
    ports = list(settings.service_ports.values())
    if len(set(ports)) != len(ports):
        raise SystemExit("五个服务必须使用不同端口")
    for port in ports:
        with socket.socket() as probe:
            try:
                probe.bind((settings.host, port))
            except OSError:
                raise SystemExit(f"端口 {port} 已被占用，请先关闭旧服务或修改 .env")
    stopped = False
    def stop(*_):
        nonlocal stopped
        stopped = True
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    processes, logs = [], []    # 逐个启动服务，确认就绪后再启动下一个。
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        for name in settings.service_ports:
            logfile = (ROOT / "logs" / f"{name}.log").open("w", encoding="utf-8")
            logs.append(logfile)
            process = subprocess.Popen([sys.executable, "-u", "-m", "agent_comm.server", name],
                cwd=ROOT, env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
                stdout=logfile, stderr=subprocess.STDOUT, creationflags=flags)
            processes.append(process)
            deadline = time.monotonic() + 40
            ready = False
            while not stopped and time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f"{name} 启动退出，查看 logs/{name}.log")
                try:
                    if name == "rpc":
                        import grpc
                        from agent_comm.generated import ai_query_pb2 as pb
                        from agent_comm.generated import ai_query_pb2_grpc as rpc
                        with grpc.insecure_channel(settings.grpc_address) as channel:
                            ready = rpc.AIQueryServiceStub(channel).Health(pb.Empty(), timeout=1).ready
                    else:
                        port = settings.service_ports[name]
                        ready = http_get(f"http://{settings.host}:{port}/health")["status"] == "ok"
                        if ready and name in {"math", "weather"}:
                            # 专业 Agent 不仅要能访问，还要已登记到注册中心。
                            ready = any(name in row["tags"] for row in
                                        http_get(settings.registry_url + "/v1/agents")["agents"])
                except Exception:
                    ready = False
                if ready:
                    break
                time.sleep(0.2)
            if stopped:
                return
            if not ready:
                raise RuntimeError(f"{name} 启动等待超时，查看 logs/{name}.log")
            print(f"[OK] {name}", flush=True)
        print(f"\nAgent Communication RPC Framework 已启动")
        print(f"模式={settings.llm_provider} 存储={settings.storage_backend}")
        print(f"gRPC={settings.grpc_address}  Registry={settings.registry_url}")
        print("另开 PowerShell 运行客户端；本窗口按 Ctrl+C 停止全部服务。", flush=True)
        if args.check:
            result = subprocess.run([sys.executable, str(ROOT / "scripts" / "smoke_test.py")],
                                    cwd=ROOT, timeout=settings.request_timeout * 3 + 10)
            if result.returncode:
                raise RuntimeError("冒烟测试失败")
            return
        while not stopped:
            for process in processes:
                if process.poll() is not None:
                    raise RuntimeError("服务意外退出，请检查 logs")
            time.sleep(0.25)
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for logfile in logs:
            logfile.close()
        print("所有服务已停止。", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError) as exc:
        print("启动失败:", exc)
        raise SystemExit(1)

