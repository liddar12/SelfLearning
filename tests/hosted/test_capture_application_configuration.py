"""Explicit v2 configuration checks with fake HTTP, never hosted readiness."""
import pytest

from services.web import capture_configuration as config
from services.web.nfl_capture_applications import CaptureApplicationReceiver
from selflearn_core.adapters.nfl_capture_application_journal import NFLCaptureApplicationJournal
from selflearn_core.store.supabase_store import StoreError
from .test_capture_configuration import ENV, STATUS_PATH, DELIVERY_PATH, requests

V2_PATH = "/rest/v1/rpc/selflearn_journal_read_capture_application_v2"


def missing():
    return {"version": 2, "kind": "nfl.capture_application_page.v2", "found": False,
            "application_id": config._PROBE_ID, "view": "registration", "ledger_watermark": None,
            "registration_id": None, "seal_id": None, "records": [], "total_records": 0, "next_after": None}


def test_explicit_v2_factory_constructs_pure_then_only_three_read_probes(requests):
    requests.responses[V2_PATH] = missing()
    factory, info = config.select_capture_factory({**ENV, "SELFLEARN_CAPTURE_PROTOCOL": "v2"})
    assert requests.calls == []
    assert info["capture_protocol"] == "v2" and info["readiness"] == "configured_unverified"
    with factory() as receiver:
        assert isinstance(receiver, CaptureApplicationReceiver)
        assert isinstance(receiver.journal, NFLCaptureApplicationJournal)
        assert receiver.destination_id == config.DESTINATION_ID and receiver.producer_id == config.PRODUCER_ID
    assert [call[1] for call in requests.calls] == [STATUS_PATH, DELIVERY_PATH, V2_PATH]
    assert all(call[0] == "POST" and "read" in call[1] for call in requests.calls)
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


@pytest.mark.parametrize("fault", [StoreError(404), TimeoutError("SECRET"), {"version": 1},
    {**missing(), "records": [{}]}, {**missing(), "ledger_watermark": "0"},
    {**missing(), "found": True}, {**missing(), "application_id": "sha256:" + "f" * 64}])
def test_missing_or_malformed_v2_schema_probe_never_yields_a_receiver(requests, fault):
    requests.responses[V2_PATH] = fault
    factory, info = config.select_capture_factory({**ENV, "SELFLEARN_CAPTURE_PROTOCOL": "v2"})
    with pytest.raises(StoreError) as caught:
        with factory(): pytest.fail("Unverified v2 schema yielded a receiver")
    assert caught.value.status == 503 and "SECRET" not in str(caught.value)
    assert info["production_connection_verified"] is False
    assert not any("reserve" in call[1] or "storage" in call[1] for call in requests.calls)
