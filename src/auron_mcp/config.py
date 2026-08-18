import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    """Runtime settings, all derived from environment variables.

    Never trust tenant/user/role input from the model: identity comes from the
    AURON session (see auth/provider.py), never from these settings either.
    """

    name: str = "AURON MCP"
    transport: str = "stdio"
    read_only: bool = True
    adapter: str = "http"
    api_base_url: str = ""
    api_token: str = ""
    audit_log_path: str = ""
    rate_limit_max_calls: int = 30
    rate_limit_window_seconds: float = 60.0
    http_timeout_seconds: float = 15.0
    identity_cache_ttl: float = 300.0
    allow_superuser_without_tenant: bool = False
    http_host: str = "127.0.0.1"
    http_port: int = 8765
    http_path: str = "/mcp"
    http_allowed_hosts: tuple[str, ...] = ()
    http_bearer_token: str = ""
    http_max_body_bytes: int = 4_194_304
    oauth_enabled: bool = False
    oauth_issuer_url: str = "http://127.0.0.1:8765"
    oauth_resource_url: str = "http://127.0.0.1:8765/mcp"
    oauth_service_documentation_url: str = ""
    oauth_client_id: str = "auron-mcp-client"
    oauth_client_secret: str = "auron-mcp-client-secret"
    oauth_client_redirect_uris: tuple[str, ...] = ("http://127.0.0.1:6274/api/callback",)
    oauth_dynamic_client_registration: bool = False
    otel_enabled: bool = False
    otel_endpoint: str = "http://127.0.0.1:4318/v1/traces"
    otel_service_name: str = "auron-mcp"
    confirmation_ttl_seconds: int = 300
    idempotency_ttl_seconds: int = 86_400
    idempotency_max_entries: int = 10_000

    @classmethod
    def from_env(cls, env: dict | None = None) -> "Settings":
        env = env or os.environ
        return cls(
            name=env.get("AURON_MCP_NAME", "AURON MCP"),
            transport=env.get("AURON_MCP_TRANSPORT", "stdio"),
            read_only=env.get("AURON_MCP_READ_ONLY", "true").lower() == "true",
            adapter=env.get("AURON_ADAPTER", "http"),
            api_base_url=env.get("AURON_API_BASE_URL", "").rstrip("/"),
            api_token=env.get("AURON_API_TOKEN", ""),
            audit_log_path=env.get("AURON_AUDIT_LOG_PATH", ""),
            rate_limit_max_calls=int(env.get("AURON_RATE_LIMIT_MAX_CALLS", "30")),
            rate_limit_window_seconds=float(env.get("AURON_RATE_LIMIT_WINDOW_SECONDS", "60")),
            http_timeout_seconds=float(env.get("AURON_HTTP_TIMEOUT_SECONDS", "15")),
            identity_cache_ttl=float(env.get("AURON_IDENTITY_CACHE_TTL", "300")),
            allow_superuser_without_tenant=(
                env.get("AURON_ALLOW_SUPERUSER_NO_TENANT", "false").lower() == "true"
            ),
            http_host=env.get("AURON_MCP_HTTP_HOST", "127.0.0.1"),
            http_port=int(env.get("AURON_MCP_HTTP_PORT", "8765")),
            http_path=env.get("AURON_MCP_HTTP_PATH", "/mcp"),
            http_allowed_hosts=tuple(
                h.strip()
                for h in env.get("AURON_MCP_HTTP_ALLOWED_HOSTS", "").split(",")
                if h.strip()
            ),
            http_bearer_token=env.get("AURON_MCP_HTTP_BEARER_TOKEN", ""),
            http_max_body_bytes=int(env.get("AURON_MCP_HTTP_MAX_BODY_BYTES", "4194304")),
            oauth_enabled=env.get("AURON_MCP_OAUTH_ENABLED", "false").lower() == "true",
            oauth_issuer_url=env.get("AURON_MCP_OAUTH_ISSUER_URL", "http://127.0.0.1:8765"),
            oauth_resource_url=env.get("AURON_MCP_OAUTH_RESOURCE_URL", "http://127.0.0.1:8765/mcp"),
            oauth_service_documentation_url=env.get("AURON_MCP_OAUTH_SERVICE_DOC_URL", ""),
            oauth_client_id=env.get("AURON_MCP_OAUTH_CLIENT_ID", "auron-mcp-client"),
            oauth_client_secret=env.get("AURON_MCP_OAUTH_CLIENT_SECRET", "auron-mcp-client-secret"),
            oauth_client_redirect_uris=tuple(
                u.strip()
                for u in env.get("AURON_MCP_OAUTH_CLIENT_REDIRECT_URIS", "").split(",")
                if u.strip()
            ),
            oauth_dynamic_client_registration=(
                env.get("AURON_MCP_OAUTH_DYNAMIC_CLIENT_REGISTRATION", "false").lower() == "true"
            ),
            otel_enabled=env.get("AURON_MCP_OTEL_ENABLED", "false").lower() == "true",
            otel_endpoint=env.get("AURON_MCP_OTEL_ENDPOINT", "http://127.0.0.1:4318/v1/traces"),
            otel_service_name=env.get("AURON_MCP_OTEL_SERVICE_NAME", "auron-mcp"),
            confirmation_ttl_seconds=int(
                env.get("AURON_MCP_CONFIRMATION_TTL_SECONDS", "300")
            ),
            idempotency_ttl_seconds=int(
                env.get("AURON_MCP_IDEMPOTENCY_TTL_SECONDS", "86400")
            ),
            idempotency_max_entries=int(
                env.get("AURON_MCP_IDEMPOTENCY_MAX_ENTRIES", "10000")
            ),
        )


settings = Settings.from_env()
