"""生成 gRPC Stub；修正 protoc 默认的同级绝对导入，支持包形式使用。"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent


def main():
    from grpc_tools import protoc
    output = ROOT / "agent_comm" / "generated"
    output.mkdir(parents=True, exist_ok=True)
    code = protoc.main(["grpc_tools.protoc", f"-I{ROOT / 'proto'}",
                        f"--python_out={output}", f"--grpc_python_out={output}",
                        str(ROOT / "proto" / "ai_query.proto")])
    if code:
        raise SystemExit(code)
    stub = output / "ai_query_pb2_grpc.py"
    text = stub.read_text(encoding="utf-8")
    text = text.replace("import ai_query_pb2 as ai__query__pb2",
                        "from . import ai_query_pb2 as ai__query__pb2")
    stub.write_text(text, encoding="utf-8")
    print("gRPC 代码已生成:", output)


if __name__ == "__main__":
    main()

