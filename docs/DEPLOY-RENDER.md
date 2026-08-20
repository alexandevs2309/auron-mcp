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
AURON_MCP_READ_ONLY=false
AURON_MCP_OAUTH_ENABLED=true
```

> **Nota**: `AURON_API_TOKEN` es un token de servicio que actúa como
> identidad de fallback. Para uso real, necesitas OAuth PKCE (ver abajo).

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
| `AURON_MCP_READ_ONLY` | `false` | Permitir escrituras |
| `AURON_MCP_OAUTH_ENABLED` | `true` | Habilitar OAuth |
| `AURON_RATE_LIMIT_MAX_CALLS` | `60` | Límite de llamadas |
| `AURON_RATE_LIMIT_WINDOW_SECONDS` | `60` | Ventana del rate limit |
| `AURON_HTTP_TIMEOUT_SECONDS` | `30` | Timeout del backend |
