"""Live isolation check against the real AURON API.

Run (not part of the offline pytest suite):

    AURON_API_BASE_URL=http://localhost:8002 .venv/bin/python scripts/live_isolation.py

Proves AURON enforces tenant isolation on its own, independent of what this
server sends:
  1. a valid session resolves to ITS OWN tenant only;
  2. tenant override headers/params on a valid token are ignored (response is
     still scoped to the token's tenant, never another tenant's data);
  3. a tampered access_token is rejected (401/403).

Requires two tenants seeded in the upstream API. Update CREDS_A / CREDS_B to
your local fixtures.
"""
import asyncio

import httpx2 as httpx

API = "http://localhost:8002"

# tenant "salon-dos" (empty fixture) and tenant "salon-uno" (has data)
CREDS_A = {"email": "caja2@salon-dos.test", "password": "Caja2Dos2026!", "tenant_subdomain": "salon-dos"}
CREDS_B = {"email": "caja@salon-uno.test", "password": "Caja2026!", "tenant_subdomain": "salon-uno"}
# A endpoint with known cross-tenant data asymmetry:
#   salon-uno: 2 products; salon-dos: 0 products.
PROBE = "/api/inventory/products/"


async def login(client, creds):
    r = await client.post("/api/auth/login/", json=creds)
    assert r.status_code == 200, f"login {creds['email']} -> {r.status_code}"
    return [{"name": c.name, "value": c.value} for c in client.cookies.jar]


def apply_cookies(client, cookies):
    client.cookies.clear()
    for c in cookies:
        client.cookies.set(c["name"], c["value"])


async def main() -> None:
    async with httpx.AsyncClient(base_url=API, timeout=30.0) as client:
        a_cookies = await login(client, CREDS_A)  # salon-dos
        b_cookies = await login(client, CREDS_B)  # salon-uno

        # 1) Baseline: each session sees only its own tenant's data.
        apply_cookies(client, a_cookies)
        a = (await client.get(PROBE)).json()
        apply_cookies(client, b_cookies)
        b = (await client.get(PROBE)).json()
        print(f"[1] salon-dos products={a.get('count')}; salon-uno products={b.get('count')}")
        assert a.get("count") == 0 and b.get("count") == 2, "fixture asymmetry missing"

        # 2) Mismatch: valid salon-dos token but claiming salon-uno.
        apply_cookies(client, a_cookies)
        r = await client.get(
            PROBE,
            headers={"X-AURON-Tenant": "1", "X-Tenant-Subdomain": "salon-uno"},
            params={"tenant_id": 1, "tenant_subdomain": "salon-uno"},
        )
        body = r.json()
        print(f"[2] mismatch claim -> HTTP {r.status_code}; products={body.get('count')}")
        assert r.status_code in (200, 401, 403)
        assert body.get("count") == 0, "CROSS-TENANT LEAK: salon-uno data returned"

        # 3) Tampered access_token is rejected.
        forged = [{"name": "access_token", "value": "forged.garbage.token"}]
        forged += [c for c in a_cookies if c["name"] != "access_token"]
        apply_cookies(client, forged)
        r = await client.get(PROBE)
        print(f"[3] forged access_token -> HTTP {r.status_code}")
        assert r.status_code in (401, 403), "forged token must be rejected"

        print("ISOLATION OK")


if __name__ == "__main__":
    asyncio.run(main())
