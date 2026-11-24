"""Explicit v2 configuration checks with fake HTTP, never hosted readiness."""
import sys
from unittest.mock import patch

import pytest

from services.web import capture_configuration as config
from services.web.nfl_capture_applications import CaptureApplicationReceiver
from selflearn_core.adapters.nfl_capture_application_journal import NFLCaptureApplicationJournal
from selflearn_core.store.supabase_store import StoreError
from selflearn_core.evidence_chunks import make_chunk
from selflearn_core.probability_receipts import fingerprint
from .test_capture_configuration import ENV, STATUS_PATH, DELIVERY_PATH, QUOTA_PATH, requests

V2_PATH = "/rest/v1/rpc/selflearn_journal_read_capture_application_v2"
APPLICATION_ID = fingerprint("actual_requested_application")


def missing():
    return {"version": 2, "kind": "nfl.capture_application_page.v2", "found": False,
            "application_id": APPLICATION_ID, "view": "registration", "ledger_watermark": None,
            "registration_id": None, "seal_id": None, "records": [], "total_records": 0, "next_after": None}


def test_explicit_v2_factory_opens_without_network_or_unrelated_legacy_probes(requests):
    requests.responses[V2_PATH] = missing()
    factory, info = config.select_capture_factory({**ENV, "SELFLEARN_CAPTURE_PROTOCOL": "v2"})
    assert requests.calls == []
    assert info["capture_protocol"] == "v2" and info["readiness"] == "configured_unverified"
    with factory() as receiver:
        assert isinstance(receiver, CaptureApplicationReceiver)
        assert isinstance(receiver.journal, NFLCaptureApplicationJournal)
        assert receiver.destination_id == config.DESTINATION_ID and receiver.producer_id == config.PRODUCER_ID
    assert requests.calls == []
    assert info["working_learning_loop"] is info["production_connection_verified"] is False
    assert info["model_adoption"] == "unverified"


@pytest.mark.parametrize("protocol", [None, False, True, 1, [], {}, "", "V2", " v2 ", "v3", "SECRET"])
def test_invalid_explicit_protocol_has_no_v1_fallback_or_io(requests, protocol):
    factory, info = config.select_capture_factory({**ENV, "SELFLEARN_CAPTURE_PROTOCOL": protocol})
    assert not info["configuration_valid"] and info["readiness"] == "invalid_configuration"
    with pytest.raises(StoreError) as caught:
        with factory(): pytest.fail("Invalid protocol opened resources")
    assert caught.value.status == 503 and "SECRET" not in str(caught.value)
    assert requests.calls == []


def test_disabled_backend_ignores_protocol_and_never_auto_enables(requests):
    for protocol in ("v2", "SECRET", None):
        factory, info = config.select_capture_factory({**ENV, "SELFLEARN_CAPTURE_BACKEND": "disabled",
                                                      "SELFLEARN_CAPTURE_PROTOCOL": protocol})
        assert factory is None and info["readiness"] == "disabled"
    assert requests.calls == []


@pytest.mark.parametrize("fault", [StoreError(401), StoreError(403), StoreError(404), TimeoutError("SECRET"), {"version": 1},
    {**missing(), "records": [{}]}, {**missing(), "ledger_watermark": "0"},
    {**missing(), "found": True}, {**missing(), "application_id": "sha256:" + "f" * 64}])
def test_missing_or_malformed_concrete_v2_read_never_becomes_missing_or_success(requests, fault):
    requests.responses[V2_PATH] = fault
    factory, info = config.select_capture_factory({**ENV, "SELFLEARN_CAPTURE_PROTOCOL": "v2"})
    with pytest.raises(StoreError) as caught:
        with factory() as receiver:
            assert requests.calls == []
            receiver.application(APPLICATION_ID)
    assert caught.value.status in (401, 403, 404, 502, 503) and "SECRET" not in str(caught.value)
    assert info["production_connection_verified"] is False
    assert not any("reserve" in call[1] or "storage" in call[1] for call in requests.calls)
    assert [call[1] for call in requests.calls] == [V2_PATH]
    assert requests.calls[0][2]["data"]["p_application_id"] == APPLICATION_ID


def test_v2_original_read_is_independent_of_legacy_status_outage(requests):
    requests.responses[STATUS_PATH] = StoreError(503)
    requests.responses[DELIVERY_PATH] = StoreError(503)
    requests.responses[V2_PATH] = missing()
    factory, info = config.select_capture_factory({**ENV, "SELFLEARN_CAPTURE_PROTOCOL": "v2"})
    for _ in range(2):
        with factory() as receiver:
            assert receiver.application(APPLICATION_ID) is None
    assert [call[1] for call in requests.calls] == [V2_PATH, V2_PATH]
    assert info["readiness"] == "configured_unverified"


def test_v2_missing_quota_still_prevents_private_storage_write(requests):
    requests.responses[QUOTA_PATH] = StoreError(404)
    factory, _ = config.select_capture_factory({**ENV, "SELFLEARN_CAPTURE_PROTOCOL": "v2"})
    with factory() as receiver, pytest.raises(StoreError) as error:
        receiver.stage(make_chunk(b"not acknowledged"))
    assert error.value.status == 503
    assert [call[1] for call in requests.calls] == [QUOTA_PATH]


@pytest.mark.parametrize("fault", [StoreError(401), StoreError(403), StoreError(404),
                                  TimeoutError("SECRET"), {"version": 1}, {**missing(), "records": [{}]}])
def test_concrete_v2_status_errors_remain_sanitized_503_at_http_boundary(requests, fault):
    from .test_capture_application_api import api_client, headers, ENV as AUTH_ENV, BASE
    requests.responses[V2_PATH] = fault
    env = {**ENV, **AUTH_ENV, "SELFLEARN_CAPTURE_PROTOCOL": "v2"}
    with api_client(env=env) as client:
        response = client.get(BASE + "/" + APPLICATION_ID, headers=headers())
    assert response.status_code == 503
    assert "SECRET" not in response.text and "offline.supabase" not in response.text
    assert "durable\":true" not in response.text
    assert [call[1] for call in requests.calls] == [V2_PATH]


def test_v2_registration_still_checks_the_actual_legacy_original_before_raw_or_writes(requests):
    from .test_capture_application_api import api_client, headers, ENV as AUTH_ENV, BASE, intent, V
    value = V.make_request(intent(destination=config.DESTINATION_ID)["capture_request"],
                           game_scope=V.CaptureGameScope(2026, 6, "g6").document())
    requests.responses[V2_PATH] = {**missing(), "application_id": value["application_id"]}
    requests.responses[DELIVERY_PATH] = StoreError(404)
    with api_client(env={**ENV, **AUTH_ENV, "SELFLEARN_CAPTURE_PROTOCOL": "v2"}) as client:
        response = client.post(BASE, json=value, headers=headers())
    assert response.status_code == 503
    assert [call[1] for call in requests.calls] == [V2_PATH, DELIVERY_PATH]
    assert requests.calls[1][2]["data"]["p_delivery_id"] == value["capture_request"]["delivery_id"]
    assert requests.calls[1][2]["data"]["p_delivery_id"] != config._PROBE_ID


@pytest.mark.parametrize("fault,expected", [(None, 404), (StoreError(404), 503), ({"version": 2}, 503)])
def test_v2_factory_inherited_v1_reads_validate_the_actual_requested_delivery(requests, fault, expected):
    from .test_capture_application_api import api_client, headers, ENV as AUTH_ENV
    delivery_id = fingerprint("inherited_actual_delivery")
    if fault is not None:
        requests.responses[DELIVERY_PATH] = fault
    with api_client(env={**ENV, **AUTH_ENV, "SELFLEARN_CAPTURE_PROTOCOL": "v2"}) as client:
        response = client.get("/api/v1/nfl/capture-deliveries/" + delivery_id, headers=headers())
    assert response.status_code == expected
    assert [call[1] for call in requests.calls] == [DELIVERY_PATH]
    assert requests.calls[0][2]["data"]["p_delivery_id"] == delivery_id


def test_large_scoped_status_uses_only_its_pinned_application_ledger(requests):
    # Self-consistent in-memory report for 2,996 members. This tests the real
    # factory/port/HTTP read path, not SQL durability or outcome acceptance.
    from .test_capture_application_api import api_client, headers, ENV as AUTH_ENV, BASE, CORE_TESTS, V
    from selflearn_core.adapters.nfl_capture_delivery_journal import make_capture_delivery_request
    sys.path.insert(0, str(CORE_TESTS))
    try:
        from test_nfl_capture_application_v2 import context
        from test_nfl_capture_application_port_faults import Pages
    finally:
        sys.path.pop(0)
    bound, _journal, _envelope, _objects = context(sizes=(1498, 1498))
    original = bound["request"]["capture_request"]
    captured = make_capture_delivery_request(original["envelope"], original["raw_pointers"],
        destination_id=config.DESTINATION_ID, producer_id=config.PRODUCER_ID)
    bound["request"] = V.make_request(captured, game_scope=V.CaptureGameScope(2026, 6, "g6").document())
    seal = V.prepare_seal(bound)
    with patch("test_nfl_capture_application_port_faults.sealed", return_value=(bound, seal)):
        pages = Pages()
    requests.responses[STATUS_PATH] = StoreError(503)
    requests.responses[DELIVERY_PATH] = StoreError(503)
    requests.responses[V2_PATH] = lambda method, path, **kwargs: pages.request(method, path, data=kwargs["data"])
    with api_client(env={**ENV, **AUTH_ENV, "SELFLEARN_CAPTURE_PROTOCOL": "v2"}) as client:
        response = client.get(BASE + "/" + seal["application_id"], headers=headers())
    assert response.status_code == 200, response.text
    assert response.json()["records"] == [pages.status]
    assert pages.terminal["counts"]["members"] == 2996
    assert len(requests.calls) == 5
    assert all(call[1] == V2_PATH for call in requests.calls)
    assert all(call[2]["data"]["p_application_id"] == seal["application_id"] for call in requests.calls)
    assert [call[2]["data"]["p_ledger_watermark"] for call in requests.calls] == [None] + [pages.fence] * 4
