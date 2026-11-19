# Reglas — auron-mcp (gateway MCP)

- Identidad y tenant se resuelven desde la API real (`/api/auth/verify/` + `/api/tenants/current/`). **Nunca** aceptar `tenant_id` ni header de tenant desde argumentos del modelo. `adapters/http.py` lo evita a propósito.
- RBAC fail-closed: rol desconocido = sin permisos; un 403 del backend gana siempre. No añadir atajos de superuser.
- `AURON_MCP_READ_ONLY=true` es el default de seguridad. Todo write pasa por preview → `confirmation_token` de un solo uso → `idempotency_key`. No desactivar el guard.
- Los errores hacia el modelo pasan por `errors.normalize_error()`: nunca filtrar response bodies, tokens ni stack traces. Usar `redact()` en logs.
- Añadir tools nuevas implica: definición en `server.py`, lógica en `tools/__init__.py`, permiso en `policy/roles.py`, endpoint verificado en el backend y tests (incluido `test_tenant_isolation.py`).
- Antes de tocar un endpoint, contrastar con `api_peluqueria/backend/urls.py`: los contratos rotos `reschedule`, `fiscal_summary` y `auth_login` ya están corregidos en el adaptador (2026-11-19) — ver `../ESTADO_Y_PLAN.md`.
- Si el cambio es de transporte, respetar la spec 2026-07-28: validación header↔body con `-32020 HeaderMismatch`, 405 para no-POST, 413 antes de bufferear, allowlist de hosts.
- Estado: sin `AURON_REDIS_URL` todo es en memoria (una sola réplica). Con esa variable, rate limit, confirmaciones e idempotencia usan Redis (extra `redis`); OAuth sigue en memoria y requiere sticky sessions en el balanceador. Install: `pip install -e ".[dev,redis]"`.
- `settings` se congela al importar: en tests, manipular `os.environ` en `conftest.py` **antes** de importar módulos.
- Verificación: `pytest -q`, `ruff check src tests`. `pytest -m load` para carga. No creer el README (dice 15 tools / 78 tests; hay 26 / ~140).
- No commitear `.env`, sesiones `~/.auron-mcp/session.json` ni `audit.jsonl`.
