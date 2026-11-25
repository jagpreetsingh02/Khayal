"""Refuse unsafe production config at startup (APP_MODE=dev or aws).

Mock mode is the zero-key demo and is never blocked: an empty SESSION_SECRET gets a random
one per start (app/api/auth.py) and scripts/start.py makes an MCP_TOKEN_SECRET (and, for
MCP_AUTH_MODE=secret, an MCP_SHARED_SECRET) per run. dev/aws need MCP_AUTH_MODE=token.
In dev/aws every problem is collected first, then the process stops with one message that
lists them all, so a deploy is fixed in one pass instead of one error at a time.
"""

import logging

from app.config import Settings

log = logging.getLogger(__name__)
MIN_SECRET_LENGTH = 32
DEMO_TOKEN_DEFAULT = "change-me"


def config_problems(settings: Settings, component: str = "api") -> tuple[list[str], list[str]]:
    """(errors, warnings) for this config. Errors only outside mock mode. Both processes
    (api, mcp) read the same .env, so both refuse the same unsafe config."""
    errors: list[str] = []
    warnings: list[str] = []
    if settings.app_mode == "mock":
        if not settings.session_secret and component == "api":
            warnings.append(
                "SESSION_SECRET is empty: using a random one for this run "
                "(dashboard sessions end when the app restarts)."
            )
        return errors, warnings

    if settings.mcp_auth_mode != "token":
        errors.append(
            f"MCP_AUTH_MODE={settings.mcp_auth_mode} is allowed only in APP_MODE=mock (one shared "
            "bearer lets its holder claim any sender). Use MCP_AUTH_MODE=token."
        )
    for name, value in (
        ("SESSION_SECRET", settings.session_secret),
        ("MCP_TOKEN_SECRET", settings.mcp_token_secret),
    ):
        if not value:
            errors.append(f"{name} is not set (python -c 'import secrets; "
                          "print(secrets.token_urlsafe(32))' makes one).")  # fmt: skip
        elif len(value) < MIN_SECRET_LENGTH:
            errors.append(f"{name} is shorter than {MIN_SECRET_LENGTH} characters.")
    if settings.session_ttl_hours <= 0:
        errors.append("SESSION_TTL_HOURS must be greater than 0.")
    if settings.channel == "twilio":
        if not settings.public_base_url:
            errors.append(
                "CHANNEL=twilio needs PUBLIC_BASE_URL (Twilio webhook signatures are "
                "checked against it)."
            )
        if not settings.twilio_auth_token:
            errors.append("CHANNEL=twilio needs TWILIO_AUTH_TOKEN (webhook signature check).")
    if (
        settings.channel == "telegram"
        and settings.telegram_mode == "webhook"
        and not settings.telegram_webhook_secret
    ):
        errors.append("CHANNEL=telegram with TELEGRAM_MODE=webhook needs TELEGRAM_WEBHOOK_SECRET.")
    if settings.channel == "simulator" and component == "api":
        warnings.append(
            "CHANNEL=simulator: the web simulator exists only in APP_MODE=mock, so no parent "
            "can chat here. Set CHANNEL=telegram or CHANNEL=twilio."
        )
    if settings.dashboard_demo_token == DEMO_TOKEN_DEFAULT and component == "api":
        warnings.append(
            "DASHBOARD_DEMO_TOKEN is still 'change-me' (the demo login is off in "
            f"APP_MODE={settings.app_mode}; sign-in is by one-time code only)."
        )
    return errors, warnings


def enforce(settings: Settings, component: str = "api") -> None:
    """Log warnings; stop the process with one message listing every error."""
    errors, warnings = config_problems(settings, component)
    for w in warnings:
        log.warning("%s", w)
    if errors:
        lines = "\n".join(f"  - {e}" for e in errors)
        raise SystemExit(
            f"Khayal {component} refuses to start: unsafe config for "
            f"APP_MODE={settings.app_mode} ({len(errors)} problem"
            f"{'s' if len(errors) != 1 else ''}):\n{lines}\n"
            "Fix these in .env (see .env.example), or use APP_MODE=mock for the demo."
        )
