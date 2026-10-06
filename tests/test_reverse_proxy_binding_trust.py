import asyncio
from types import SimpleNamespace

import pytest

from kitt.llm.domain import ProviderAuthError
from kitt.llm.endpoint_security import ProviderEndpointTrustStore
from kitt.reverse_proxy.bindings import bind_instance_to_role
from kitt.reverse_proxy.contracts import ReverseProxyInstance


def instance(host='127.0.0.1', port=3001):
    return ReverseProxyInstance('chatgpt-code','chatgpt','chatgpt-web','https://chatgpt.com/','coding',host,port,123,'running','')


def test_user_selected_service_is_trusted_before_router_binding(monkeypatch, tmp_path):
    monkeypatch.setattr('pathlib.Path.home', lambda: tmp_path)
    policy = ProviderEndpointTrustStore()
    selected = instance()
    with pytest.raises(ProviderAuthError, match='Refusing provider egress'):
        policy.assert_trusted('kitt-reverse-proxy', selected.endpoint)
    calls = []

    async def set_role(role, model, provider, endpoint):
        policy.assert_trusted(provider, endpoint)
        calls.append((role, model, provider, endpoint))

    asyncio.run(bind_instance_to_role(SimpleNamespace(_set_model_role=set_role),'code',selected))
    assert calls == [('principal','chatgpt-web','kitt-reverse-proxy','http://127.0.0.1:3001')]
    assert not policy.is_trusted('kitt-reverse-proxy','http://127.0.0.1:3002')
    assert not policy.is_trusted('openai',selected.endpoint)


def test_invalid_role_does_not_grant_endpoint_trust(monkeypatch, tmp_path):
    monkeypatch.setattr('pathlib.Path.home', lambda: tmp_path)
    with pytest.raises(ValueError, match='Role'):
        asyncio.run(bind_instance_to_role(SimpleNamespace(),'invalid',instance()))
    assert not ProviderEndpointTrustStore().is_trusted('kitt-reverse-proxy',instance().endpoint)


def test_failed_trust_write_does_not_change_router(monkeypatch):
    def fail_trust(*_args):
        raise PermissionError('trust file is not private')

    monkeypatch.setattr(ProviderEndpointTrustStore, 'trust', fail_trust)
    calls = []

    async def set_role(*args):
        calls.append(args)

    with pytest.raises(PermissionError, match='private'):
        asyncio.run(bind_instance_to_role(SimpleNamespace(_set_model_role=set_role),'code',instance()))
    assert calls == []
