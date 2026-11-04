"""所有进程共用的配置；环境变量优先于项目 .env。"""
from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


def flag(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).lower() in {"true", "1", "yes"}


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    # 以下为整套系统的整个服务端口，目前共计5个 #
    registry_port: int = 8500
    orchestrator_port: int = 5000
    math_port: int = 5001
    weather_port: int = 5002  # 新增独立天气 Agent 的 HTTP 监听端口。
    grpc_port: int = 50051
    # ================================== #
    llm_provider: str = "mock"
    qwen_api_key: str = ""
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_model: str = "qwen-plus"
    storage_backend: str = "sqlite"
    sqlite_path: str = str(ROOT / "data" / "agent_comm.sqlite3")
    redis_url: str = "redis://localhost:6379/0"
    enable_mcp: bool = True
    enable_rag: bool = True
    embedding_provider: str = "local"
    embedding_key: str = ""
    embedding_model: str = "text-embedding-v2"
    embedding_url: str = "https://dashscope.aliyuncs.com/api/v1/services/embeddings/text-embedding/text-embedding"
    rag_top_k: int = 5
    rag_threshold: float = 0.0
    weather_mode: str = "mock"
    request_timeout: float = 30.0
    heartbeat_interval: float = 5.0
    heartbeat_ttl: float = 20.0

    @classmethod
    def from_env(cls):
        load_dotenv(ROOT / ".env", override=False)
        path = Path(os.getenv("SQLITE_PATH", "data/agent_comm.sqlite3"))
        settings = cls(
            host=os.getenv("HOST", "127.0.0.1"),
            registry_port=int(os.getenv("REGISTRY_PORT", "8500")),
            orchestrator_port=int(os.getenv("ORCHESTRATOR_PORT", "5000")),
            math_port=int(os.getenv("MATH_PORT", "5001")),
            weather_port=int(os.getenv("WEATHER_PORT", "5002")),
            grpc_port=int(os.getenv("GRPC_PORT", "50051")),
            llm_provider=os.getenv("LLM_PROVIDER", "mock").lower(),
            qwen_api_key=os.getenv("QWEN_API_KEY", ""),
            qwen_base_url=os.getenv("QWEN_BASE_URL", cls.qwen_base_url).rstrip("/"),
            qwen_model=os.getenv("QWEN_MODEL", "qwen-plus"),
            storage_backend=os.getenv("STORAGE_BACKEND", "sqlite"),
            sqlite_path=str(path if path.is_absolute() else ROOT / path),
            redis_url=os.getenv("REDIS_URL", cls.redis_url),
            enable_mcp=flag("ENABLE_MCP"),
            enable_rag=flag("ENABLE_RAG"),
            embedding_provider=os.getenv("EMBEDDING_PROVIDER", "local"),
            embedding_key=os.getenv("DASHSCOPE_API_KEY", ""),
            embedding_model=os.getenv("EMBEDDING_MODEL", "text-embedding-v2"),
            embedding_url=os.getenv("EMBEDDING_URL", cls.embedding_url),
            rag_top_k=int(os.getenv("RAG_TOP_K", "5")),
            rag_threshold=float(os.getenv("RAG_THRESHOLD", "0.0")),
            weather_mode=os.getenv("WEATHER_MODE", "mock"),
            request_timeout=float(os.getenv("REQUEST_TIMEOUT", "30")),
            heartbeat_interval=float(os.getenv("HEARTBEAT_INTERVAL", "5")),
            heartbeat_ttl=float(os.getenv("HEARTBEAT_TTL", "20")),
        )
        if settings.llm_provider not in {"mock", "qwen"}:
            raise ValueError("LLM_PROVIDER 必须为 mock 或 qwen")
        if settings.llm_provider == "qwen" and not settings.qwen_api_key:
            raise ValueError("Qwen 模式需要设置 QWEN_API_KEY；演示可用 mock")
        if settings.embedding_provider not in {"local", "dashscope"}:
            raise ValueError("EMBEDDING_PROVIDER 必须为 local 或 dashscope")
        if settings.storage_backend not in {"sqlite", "redis"}:
            raise ValueError("STORAGE_BACKEND 必须为 sqlite 或 redis")
        if settings.weather_mode not in {"mock", "live"}:
            raise ValueError("WEATHER_MODE 必须为 mock 或 live")
        if not 1 <= settings.rag_top_k <= 100 or not -1 <= settings.rag_threshold <= 1:
            raise ValueError("RAG_TOP_K 范围 1~100，RAG_THRESHOLD 范围 -1~1")
        if settings.request_timeout <= 0 or settings.heartbeat_interval <= 0 or settings.heartbeat_ttl <= 0:
            raise ValueError("超时和心跳配置必须大于零")
        for port in settings.service_ports.values():
            if not 1 <= port <= 65535:
                raise ValueError("端口必须位于 1~65535")
        return settings

    @property
    def registry_url(self):
        return f"http://{self.host}:{self.registry_port}"

    @property
    def orchestrator_url(self):
        return f"http://{self.host}:{self.orchestrator_port}"   # 通过 HTTP 发送请求

    @property
    def math_url(self):
        return f"http://{self.host}:{self.math_port}"

    @property
    def weather_url(self):
        return f"http://{self.host}:{self.weather_port}"

    @property
    def service_ports(self):
        """统一维护服务顺序和端口，启动器与单服务入口使用同一份映射。"""
        return {"registry": self.registry_port, "math": self.math_port, "weather": self.weather_port,
                "orchestrator": self.orchestrator_port, "rpc": self.grpc_port}

    @property
    def grpc_address(self):
        return f"{self.host}:{self.grpc_port}"   #   获取IP地址和监听端口

