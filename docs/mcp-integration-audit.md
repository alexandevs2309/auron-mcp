# Auditoría de integración — AURON MCP v1 ↔ AURON Suite

> Repositorio auditado: `api_peluqueria` (backend de AURON Suite / Beauty Edition)
> Rama: `master`. Repo remoto: `github.com/alexandevs2309/api_peluqueria.git`.
> Estado: contiene cambios locales sin commitear (auth_api, employees_api).
> Fecha: 2026-08-13. No se modificó ningún archivo del backend.

---

## 1. Arquitectura actual

### 1.1 Stack

- **Django 5.2.11 + DRF 3.16.0 + SimpleJWT 5.5.1** (gunicorn/uvicorn WSGI/ASGI HTTP; **sin Daphne/Channels**).
- PostgreSQL (Prometheus-instrumentado) + Redis (cache/Celery). Prometheus y Sentry integrados.
- Custom user: `apps/auth_api.User` (`apps/auth_api/models.py:30`). `USERNAME_FIELD=email`.
- `backend/urls.py` — todo bajo `/api/`; `/api/schema/` y `/api/docs/` solo en DEBUG.

### 1.2 Autenticación

- `REST_FRAMEWORK.DEFAULT_AUTHENTICATION_CLASSES` (`backend/settings.py:199-203`):
  `DualJWTAuthentication` (header `Authorization: Bearer` **o** cookie `access_token`) + fallback SimpleJWT.
- `SIMPLE_JWT` (`settings.py:239-250`): access 30 min, refresh 7 días, rotación activa.
- `CustomTokenObtainPairSerializer` (`apps/auth_api/serializers.py:372-380`): inyecta claims
  `tenant_id` y `tenant_subdomain` en el JWT.
- Identidad del usuario autenticado:
  - `GET /api/auth/verify/` (`apps/auth_api/views.py:1341-1355`, `DualJWTAuthentication` + `IsAuthenticated`)
    → `{authenticated, user:{id, email, role, branch_id}}` (rol efectivo vía `get_effective_role_api`).
  - `GET /api/tenants/current/` (`apps/tenants_api/views.py:199-205`, `IsAuthenticated`)
    → `TenantSerializer` (id, name, subdomain, currency, plan, estado de suscripción).
  - `GET /api/auth/permissions/` (`apps/auth_api/permissions_check.py:5-19`) — **no lista permisos**;
    solo devuelve `is_superuser/is_staff/is_active/email/full_name`.

### 1.3 Multi-tenancy

- `TenantMiddleware` (`apps/tenants_api/middleware.py:30-351`, registrado `settings.py:163`):
  resuelve `request.tenant` desde el claim `tenant_id` del JWT, con validación defensiva
  `TENANT_MISMATCH` contra `user.tenant_id` (middleware.py:172-180). Fallback a `user.tenant`.
  Paywall (trial/grace), geo-lock, rutas públicas exentas.
- `TenantQuerySetMixin` (`apps/tenants_api/base_viewsets.py:8-57`): no-superuser filtra por
  `request.tenant`; **restricción de sucursal**: empleado no-`CLIENT_ADMIN` con perfil de sucursal
  ve/opera solo su sucursal. `TenantScopedViewSet`/`TenantScopedReadOnlyViewSet` (líneas 60-151).
- Aislamiento probado: `apps/tenants_api/tests_cross_tenant_isolation.py` (13 tests) + IDOR en
  `tests_security.py`.

### 1.4 RBAC

- Modelos: `Role` (scope GLOBAL/TENANT/MODULE), `UserRole` (`apps/roles_api/models.py:8-51`).
- Permisos reales = codenames de Django (`app.codename`), incluyendo permisos custom del modelo
  (p.ej. `pos_api.refund_sale`, `employees_api.manage_employee_loans`, `inventory_api.adjust_stock`).
- Chequeo en vistas vía permission classes DRF:
  - `TenantPermissionByAction` (`apps/core/tenant_permissions.py:105-160`) — **deny-by-default**:
    si la acción no está en `view.permission_map`, se deniega. Superuser bypass.
  - `tenant_permission('app.codename')` factory para `@api_view` (tenant_permissions.py:91-102).
  - `HasFeaturePermission` (feature del plan de suscripción, `subscriptions_api/permissions.py`).
- Roles canónicos (`apps/roles_api/views.py:15-24`): `Super-Admin`, `Client-Admin`, `Client-Staff`,
  `Estilista`, `Cajera`, `Manager`, `Utility`. Mapa de permisos por rol en
  `apps/roles_api/default_permissions.py:22-202`.
- Rol efectivo para API: `get_effective_role_api` (`apps/auth_api/role_utils.py`) normaliza a
  UPPER_SNAKE (p.ej. `SUPERADMIN`, `CLIENT_ADMIN`, `ESTILISTA`, `CAJERA`, `MANAGER`).
- `RoleBasedPermissionBackend` (`apps/roles_api/backends.py:11-124`) **no filtra por tenant por
  diseño** (documentado en backends.py:15-18); el aislamiento lo aplican las permission classes DRF.
- Observación: `PermissionMiddleware` y `TenantValidationMiddleware` existen pero **no están
  registrados** en `MIDDLEWARE` (settings.py:152-171). No afectan a la integración MCP.

### 1.5 API (endpoints de negocio relevantes)

| Capacidad | Endpoint real | Método | Permiso requerido | Tenant scoped |
| --- | --- | --- | --- | --- |
| Resumen de ventas del día | `/api/pos/summary/daily/` (`pos_api/views.py:1428`) | GET | `IsAuthenticated` | Sí |
| Búsqueda de ventas | `/api/pos/sales/search_sales/?date_from&date_to&client_id&employee_id&payment_method` (`pos_api/views.py:993`) | GET | `pos_api.view_sale` | Sí |
| Ventas (detalle) | `/api/pos/sales/?…` (`pos_api/views.py:21`) | GET | `pos_api.view_sale` | Sí |
| Citas de hoy | `/api/appointments/today/` (`appointments_api/views.py:288`) | GET | `appointments_api.view_appointment` | Sí |
| Citas por rango | `/api/appointments/?from=&to=&status=` (`appointments_api/views.py:20`) | GET | `appointments_api.view_appointment` | Sí |
| Búsqueda de clientes | `/api/clients/?search=` (search: full_name, email, phone) (`clients_api/views.py:15`) | GET | `clients_api.view_client` | Sí |
| Cliente por id | `/api/clients/{id}/` | GET | `clients_api.view_client` | Sí |
| Alertas de stock bajo | `/api/inventory/alerts/low-stock/` (`inventory_api/views.py:126`) | GET | `inventory_api.view_product` | Sí |
| Reporte de empleados | `/api/reports/employees/` (`reports_api/views.py:32`) | GET | `reports_api.view_employee_reports` | Sí |
| Reporte de ventas | `/api/reports/sales/` (`reports_api/views.py:83`) | GET | `reports_api.view_sales_reports` | Sí |
| Dashboard | `/api/reports/dashboard/` (`reports_api/views.py:151`) | GET | `reports_api.view_kpi_dashboard` | Sí |
| KPI | `/api/reports/kpi/` (`reports_api/views.py:480`) | GET | `reports_api.view_kpi_dashboard` | Sí |
| Health check | `/api/healthz/` (`backend/urls.py:19-45`) | GET | público | — |
| Identidad | `/api/auth/verify/` (`auth_api/views.py:1341`) | GET | `IsAuthenticated` | — |
| Tenant actual | `/api/tenants/current/` (`tenants_api/views.py:199`) | GET | `IsAuthenticated` | — |

Formato de respuesta verificado:
- `daily_summary` → `{date, sales_count, total, paid, by_method[], by_type{services,products}}`.
- `reports/dashboard` → `{total_clients, monthly_appointments, monthly_revenue, active_employees}`.
- `reports/employees` → `{total_employees, active_employees, employees_by_specialty[], top_performers[]}`.
- `inventory/alerts/low-stock` → `{count, products[]}`.
- Nota: `Sale` **no almacena ITBIS/propina/subtotal** (solo `total`, `discount`, `paid`); el ITBIS es
  configuración de sucursal (`Setting.tax_percentage`), no se desglosa en la venta.

### 1.6 Servicios reutilizables

No hay un service layer de negocio llamado "service" por dominio; la lógica vive en los ViewSets
con `perform_create` transaccional (POS), mixins de tenant y permission classes. El MCP **no**
reimplementa esa lógica: delega en los endpoints REST existentes (regla de la arquitectura).

### 1.7 Auditoría y observabilidad

- `AuditLog` unificado (`apps/audit_api/models.py:9-136`) + `create_audit_log()` helper
  (`apps/audit_api/utils.py:152-201`). Login/logout auditan con `LoginAudit`/`AccessLog`.
- `AuditLogMiddleware` registrado (settings.py:169). `AuditLoggingMixin` en clients/services/
  inventory/appointments/billing.
- Logging: `LOGGING` en settings.py:622-662; `StructuredLoggingMiddleware` (settings.py:166)
  genera `request_id` por request (JSON, logger `api.requests`).
- Tracing: **Prometheus** (`/metrics`, solo DEBUG) y **Sentry** (traces_sample_rate 0.1). Sin
  OpenTelemetry. El `request_id` **no se propaga** a respuestas ni a AuditLog.
- Error handling: `EXCEPTION_HANDLER` = default de DRF (`settings.py:216`); los middlewares
  devuelven shapes propios (`{error, code}`). Sin formato unificado.

---

## 2. Puntos de integración

| Capacidad | Endpoint/Service real | Método | Permiso | Tenant scoped | MCP tool propuesta |
| --- | --- | --- | --- | --- | --- |
| Ventas de hoy | `/api/pos/summary/daily/` | GET | `pos_api.view_sale` | Sí | `auron_sales_summary` |
| Ventas por rango | `/api/pos/sales/search_sales/` | GET | `pos_api.view_sale` | Sí | `auron_sales_query` |
| Citas de hoy | `/api/appointments/appointments/today/` | GET | `appointments_api.view_appointment` | Sí | `auron_appointments_today` |
| Citas por rango | `/api/appointments/appointments/` | GET | `appointments_api.view_appointment` | Sí | `auron_appointments_query` |
| Buscar cliente | `/api/clients/clients/?search=` | GET | `clients_api.view_client` | Sí | `auron_clients_search` |
| Cliente por id | `/api/clients/clients/{id}/` | GET | `clients_api.view_client` | Sí | `auron_client_get` |
| Stock bajo | `/api/inventory/alerts/low-stock/` | GET | `inventory_api.view_product` | Sí | `auron_inventory_alerts` |
| Rendimiento empleados | `/api/reports/employees/` | GET | `reports_api.view_employee_reports` | Sí | `auron_employee_performance` |
| Servicios | `/api/services/services/` | GET | `services_api.view_service` | Sí | `auron_services_list` |
| Empleados | `/api/employees/employees/` | GET | `employees_api.view_employee` | Sí | `auron_employees_list` |
| Sucursales | `/api/settings/branches/` | GET | `settings_api.view_branch` | Sí | `auron_branches_list` |
| Productos | `/api/inventory/products/` | GET | `inventory_api.view_product` | Sí | `auron_products_list` |
| Dashboard | `/api/pos/dashboard/stats/` | GET | (auth de dashboard) | Sí | `auron_dashboard_stats` |
| Resumen negocio | `/api/reports/dashboard/` | GET | `reports_api.view_kpi_dashboard` | Sí | `auron_business_summary` |
| Salud | `/api/healthz/` | GET | — | — | `auron_system_health` |
| Identidad | `/api/auth/verify/` | GET | `IsAuthenticated` | — | (bootstrap de sesión) |
| Tenant actual | `/api/tenants/current/` | GET | `IsAuthenticated` | — | (bootstrap de sesión) |

---

## 3. Riesgos

### 3.1 Cross-tenant leakage
- El MCP **nunca** debe aceptar `tenant_id` del modelo como autorización. La API de AURON resuelve
  el tenant desde el JWT (middleware) y filtra querysets por `request.tenant`; el MCP debe
  **confiar en ese claim y no sobreescribirlo**. Si el MCP pasara `tenant_id` como query param,
  AURON lo ignoraría para autorización (deriva del JWT), pero conviene no enviarlo para evitar
  dependencia de que "la API lo valide".
- Riesgo residual: si algún endpoint de AURON aceptara un tenant distinto al del token (p.ej.
  superuser con `?tenant=`), un modelo malicioso podría pedir datos de otro tenant **si el token
  MCP pertenece a un superuser**. Mitigación: el MCP prohíbe llamadas sin resolución de tenant del
  propio token y deniega superusers sin tenant (fail-closed) salvo config explícita.

### 3.2 Exposición de PII / datos sensibles
- `clients_api` expone `email`, `phone`, `notes`, `birthday`; `employees_api` expone `user.email`,
  `phone` y perfil. `UserViewSet` y `TenantSerializer` exponen email de usuarios/owner.
- Los reportes (`reports/*`) ya devuelven agregados y nombres — **no** salarios, cédulas ni PIN.
  Verificar que el MCP no añada campos: no añadirá ninguno, solo re-expone el JSON de AURON
  recortado al mínimo necesario por tool.

### 3.3 Exposición de secretos
- `GET /api/system-settings/` enmascara secretos con `"configured"` (settings_api). El MCP no debe
  exponer `settings/`, `system-settings/`, `billing/webhooks`, `admin/*`, ni ninguna ruta de
  administración. No se implementa ninguna tool hacia esos endpoints.

### 3.4 Permisos insuficientes / endpoints amplios
- `daily_summary` solo exige `IsAuthenticated` (sin permiso granular). El MCP debe exigir
  `pos_api.view_sale` a nivel de su propio RBAC para no permitir lectura de ventas a roles que
  AURON sí autorizaría por omisión. Fail-closed en el MCP, permisivo en AURON.
- `GET /api/pos/sales/search_sales/` devuelve ventas completas con `client`, `employee`, pagos.
  Minimizar a `{id, date, total, paid, method, status, client_name, employee_name}`.

### 3.5 Información interna que no debe llegar al modelo
- `Sale` incluye FK a caja, usuario, tenant, `NCFSequence` internos, `discount_reason`,
  `cash_register`, `PayrollPeriod` links. El MCP debe recortar agresivamente.
- Errores de AURON (`{"detail": ...}` o shapes `{error, code}` de middleware) no deben propagarse
  crudos al modelo: el MCP los convierte en payloads seguros y audita el detalle en logs.

### 3.6 Prompt injection / data exfiltration
- `clients_search(query)`: un modelo podría pedir `search=*` para extraer la base completa de
  clientes. Mitigación: límites de búsqueda (mín. 1 carácter, máx resultados), límite de
  resultados (paginación 25 por defecto, `MAX_PAGE_SIZE=100`), y minimización de campos.
- Ninguna tool acepta paths, URLs, SQL ni `tenant_id` arbitrario.

---

## 4. Recomendaciones

### 4.1 Qué reutilizar (sin cambios en AURON)
1. **Identidad**: validar el access token de AURON vía `GET /api/auth/verify/` y resolver el tenant
   vía `GET /api/tenants/current/`. El MCP construye su `Identity` a partir de esos datos **del
   servidor de AURON**, nunca de entrada del modelo.
2. **RBAC**: el MCP deriva los permisos del **rol efectivo** reportado por AURON
   (`get_effective_role_api`) usando la tabla de roles canónicos de AURON como fuente (espejo
   fail-closed). La **autorización real se ejecuta en AURON** (permission classes): si AURON
   deniega (403), el MCP lo audita como `denied` y devuelve error seguro. No se duplica lógica de
   negocio.
3. **Tenant**: el MCP no envía `tenant_id` en las peticiones salvo que AURON lo exija como
   conveniencia; la autorización de tenant la impone AURON desde el JWT. El MCP **sí** guarda el
   `tenant_id` resuelto para auditoría y para su guard interno.
4. **Auditoría**: el MCP registra sus propios eventos (tool, trace_id, user, tenant, duración) en
   JSONL; no reutiliza `AuditLog` de AURON porque este es por-request de la API, no por-tool MCP.
   Queda documentado para M4 re-enviar a un pipeline central.
5. **Health**: `auron_system_health` consulta `/api/healthz/`.

### 4.2 Qué modificar en AURON (NO en esta fase — se justifica su aplazamiento)
- Añadir un endpoint `GET /api/auth/me/` que devuelva `{user_id, email, tenant_id, role,
  permissions[]}` (permisos efectivos del principal) para que el MCP no mantenga un espejo de
  roles→permisos. Aplazado: requiere cambio de API en AURON; el espejo fail-closed + 403 de AURON
  ya garantizan fail-closed en ambos lados.
- Propagar `request_id`/`trace_id` a las respuestas (`X-Request-ID`) y a `AuditLog` para trazar
  MCP→AURON→DB. Aplazado: mejora de observabilidad, no bloqueante.
- (Opcional) Añadir permiso granular a `daily_summary` (hoy `IsAuthenticated`). Aplazado: el MCP ya
  exige `pos_api.view_sale` a nivel propio.

### 4.3 Decisiones de la foundation que se descartan
- `adapters/http.py` anterior enviaba `tenant_id` como query param + header `X-Auron-Tenant`.
  **Se descarta**: la API de AURON deriva el tenant del JWT; no es necesaria y añade fricción.
- `rbac.py` con roles inventados (`CLIENT_ADMIN`, `BRANCH_MANAGER`, `STAFF`, `READ_ONLY_AUDITOR`)
  **se sustituye** por un mapeo contra los roles canónicos reales de AURON.
- `development_context` (identidad desde env vars) **se elimina**: la identidad siempre proviene
  de un token real validado contra AURON. Para desarrollo local se usa un token real de un usuario
  de pruebas (mismo mecanismo que producción, sin atajos).

### 4.4 Arquitectura objetivo del MCP v1
```text
AI Host / Agent
   |  MCP (stdio | streamable-http)
   v
AURON MCP v1  (read-only)
   auth.identity   <- /api/auth/verify + /api/tenants/current
   policy          <- rol efectivo -> tool permissions (espejo fail-closed)
   audit           <- JSONL (trace_id, user, tenant, tool, outcome, duration)
   tools/*         <- semánticas, tipadas, minimizadas
   adapters/http   <- httpx contra la API real de AURON
   errors          <- boundary seguro (nada interno llega al modelo)
   v
AURON REST API  (real)
   |  TenantMiddleware (JWT) -> TenantQuerySetMixin -> TenantPermissionByAction
   v
PostgreSQL
```
Nunca: MCP → PostgreSQL directo; MCP → duplicación de lógica de negocio; LLM → tenant_id.
