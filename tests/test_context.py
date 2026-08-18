from auron_mcp.auth import AuronIdentity
from auron_mcp.context import new_context


def identity():
    return AuronIdentity(
        user_id="u1",
        email="u1@auron.test",
        tenant_id="t1",
        role="CLIENT_ADMIN",
        permissions=frozenset({"pos_api.view_sale"}),
    )


def test_new_context_holds_the_identity():
    ctx = new_context(identity())
    assert ctx.identity.user_id == "u1"
    assert ctx.identity.tenant_id == "t1"


def test_new_context_generates_a_fresh_trace_id_each_call():
    assert new_context(identity()).trace_id != new_context(identity()).trace_id


def test_trace_id_can_be_provided_explicitly():
    assert new_context(identity(), trace_id="trace-fixed").trace_id == "trace-fixed"
