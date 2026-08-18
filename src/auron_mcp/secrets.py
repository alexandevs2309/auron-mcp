"""Secret handling: redaction and fail-fast startup validation.

Redaction guarantees configured secrets never appear in log/error output even
if an upstream response or exception message echoes them. Startup validation
refuses to serve with a broken or unsafe configuration (shipped default
secrets, missing mandatory URLs).
"""

SHIPPED_OAUTH_CLIENT_SECRET = "auron-mcp-client-secret"


class ConfigurationError(RuntimeError):
    """Raised when the runtime configuration is unsafe or incomplete."""


def redact(value: str) -> str:
    """Replace configured secrets inside ``value`` with ``[REDACTED]``.

    Applies to the static bearer/API token and the OAuth client secret. The
    shipped default OAuth secret is only redacted once replaced by a real
    secret (a secret that is itself the default is not worth masking).
    """
    for secret in _configured_secrets():
        if secret and secret in value:
            value = value.replace(secret, "[REDACTED]")
    return value


def _configured_secrets() -> set[str]:
    from .config import settings

    secrets: set[str] = set()
    if settings.api_token:
        secrets.add(settings.api_token)
    if settings.http_bearer_token:
        secrets.add(settings.http_bearer_token)
    if (
        settings.oauth_enabled
        and settings.oauth_client_secret
        and settings.oauth_client_secret != SHIPPED_OAUTH_CLIENT_SECRET
    ):
        secrets.add(settings.oauth_client_secret)
    return secrets


def validate_config(settings) -> list[str]:
    """Return a list of configuration problems (empty when healthy).

    Hard problems raise :class:`ConfigurationError` from :func:`check_config`.
    """
    problems: list[str] = []

    if settings.oauth_enabled:
        if (
            not settings.oauth_client_secret
            or settings.oauth_client_secret == SHIPPED_OAUTH_CLIENT_SECRET
        ):
            problems.append(
                "AURON_MCP_OAUTH_CLIENT_SECRET is unset or still the shipped default; "
                "set a real secret before enabling OAuth"
            )
        if not settings.oauth_issuer_url:
            problems.append("AURON_MCP_OAUTH_ISSUER_URL is required when OAuth is enabled")
        if not settings.oauth_resource_url:
            problems.append("AURON_MCP_OAUTH_RESOURCE_URL is required when OAuth is enabled")

    if settings.adapter == "http" and not settings.api_base_url:
        problems.append("AURON_API_BASE_URL is required when AURON_ADAPTER=http")

    return problems


def check_config(settings) -> None:
    """Fail fast at startup when the configuration is unsafe."""
    problems = validate_config(settings)
    if problems:
        raise ConfigurationError(
            "Refusing to start with an unsafe configuration:\n"
            + "\n".join(f"  - {p}" for p in problems)
        )
