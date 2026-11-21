# Deploy del MCP en Render

## Prerrequisitos

1. Tu backend corriendo en Render: `https://api-peluqueria-p25h.onrender.com`
2. Una cuenta de Render (gratuita funciona para empezar)
3. Una terminal

## Paso 1: Push del código a GitHub

Si ainda no tienes el repo en GitHub:

```bash
cd auron-mcp
git init && git add . && git commit -m "initial"
git remote add origin https://github.com/TU_USUARIO/auron-mcp.git
git push -u origin main
```

## Paso 2: Crear el servicio en Render

1. Ve a [render.com](https://render.com) → **New** → **Web Service**
2. Conecta tu repo de GitHub
3. Configura:
   - **Name**: `auron-mcp`
   - **Runtime**: Docker
   - **Plan**: Starter ($7/mes) o Free (con limitaciones)
   - **Health Check Path**: `/healthz`

## Paso 3: Variables de entorno

En el dashboard de Render → tu servicio → **Environment** → agrega:

```
AURON_API_BASE_URL=https://api-peluqueria-p25h.onrender.com
AURON_API_TOKEN=tu-token-de-servicio-aqui
AURON_MCP_READ_ONLY=true   # cambiar a false solo tras revisar preview + confirmación
AURON_MCP_OAUTH_ENABLED=true
```

> **Nota**: `AURON_API_TOKEN` es un token de servicio que actúa como
> identidad de fallback. Para uso real, necesitas OAuth PKCE (ver abajo).
>
> **Secreto OAuth (MC-08):** `render.yaml` declara
> `AURON_MCP_OAUTH_CLIENT_SECRET` con `sync: false` y la anotación
> `{env:AURON_MCP_OAUTH_CLIENT_SECRET}`. El valor se inyecta en el dashboard
> (Environment → Secret Files/Environment Groups); **no se escribe aquí**.
> `check_config()` rechaza el arranque si la variable falta o sigue siendo el
> default `auron-mcp-client-secret`. Define también
> `AURON_MCP_OAUTH_ISSUER_URL` y `AURON_MCP_OAUTH_RESOURCE_URL` con la URL
> pública del servicio (por ejemplo `{env:AURON_MCP_PUBLIC_URL}` y
> `{env:AURON_MCP_PUBLIC_URL}/mcp`) para que los metadata de OAuth no apiten
> a `127.0.0.1`.

## Paso 4: Verificar

Una vez desplegado, Render te da una URL como:
`https://auron-mcp.onrender.com`

Prueba:
```bash
# Health check
curl https://auron-mcp.onrender.com/healthz

# Listar tools (necesita auth)
curl -H "Authorization: Bearer TU_TOKEN" \
  https://auron-mcp.onrender.com/mcp \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

## Paso 5: Conectar el frontend (chat widget)

El frontend (Cloudflare) necesita un widget de chat que se conecte al MCP.
Esto es trabajo de frontend — un componente React que:

1. Muestra una caja de chat
2. Envía mensajes al MCP via Streamable HTTP
3. Muestra las respuestas

El MCP ya soporta Streamable HTTP (spec 2026-07-28).

## Auth real (OAuth PKCE) — para producción

El Bearer token está bien para testing. Para producción con múltiples
barberías, necesitas OAuth PKCE:

1. En tu backend de AURON, crea una "aplicación OAuth"
2. Configura las variables en Render:
   ```
   AURON_MCP_OAUTH_ENABLED=true
   AURON_MCP_OAUTH_CLIENT_ID=tu-client-id
   AURON_MCP_OAUTH_CLIENT_SECRET=tu-client-secret
   ```
3. El frontend redirige al usuario al flujo de auth del MCP
4. El MCP valida contra el backend y devuelve un token de sesión

## Variables de entorno completas

| Variable | Valor | Descripción |
|----------|-------|-------------|
| `AURON_MCP_TRANSPORT` | `streamable-http` | Transporte HTTP |
| `AURON_MCP_HTTP_HOST` | `0.0.0.0` | Bind a todas las interfaces |
| `AURON_MCP_HTTP_PORT` | `8080` | Puerto (Render asigna $PORT) |
| `AURON_ADAPTER` | `http` | Usar el backend real |
| `AURON_API_BASE_URL` | `https://api-peluqueria-p25h.onrender.com` | URL del backend |
| `AURON_API_TOKEN` | `(tu token)` | Token de servicio |
| `AURON_MCP_READ_ONLY` | `true` | Default seguro; no cambiar hasta revisar el flujo de escrituras |
| `AURON_MCP_OAUTH_ENABLED` | `true` | Habilitar OAuth |
| `AURON_MCP_OAUTH_CLIENT_SECRET` | `{env:AURON_MCP_OAUTH_CLIENT_SECRET}` | Inyectar en el dashboard (`sync: false`); el default del código se rechaza al arrancar |
| `AURON_MCP_OAUTH_ISSUER_URL` | `{env:AURON_MCP_PUBLIC_URL}` | URL pública del servicio, no `localhost` |
| `AURON_MCP_OAUTH_RESOURCE_URL` | `{env:AURON_MCP_PUBLIC_URL}/mcp` | Resource indicator público |
| `AURON_RATE_LIMIT_MAX_CALLS` | `60` | Límite de llamadas |
| `AURON_RATE_LIMIT_WINDOW_SECONDS` | `60` | Ventana del rate limit |
| `AURON_REDIS_URL` | `rediss://...` | Estado compartido (rate limit, confirmaciones e idempotencia). Vacío = en memoria, una sola réplica |
| `AURON_REDIS_ON_ERROR` | `closed` | Si Redis falla: `closed` rechaza lecturas; `open` las permite. Las escrituras siempre fallan cerrado |
| `AURON_REDIS_TIMEOUT_SECONDS` | `1` | Timeout de conexión/socket a Redis |
| `AURON_HTTP_TIMEOUT_SECONDS` | `30` | Timeout del backend |
