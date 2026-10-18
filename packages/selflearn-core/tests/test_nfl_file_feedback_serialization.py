"""One bounded encoding per private row, without weakening snapshot guards."""
import copy
import json
from unittest.mock import patch

import pytest

from selflearn_core.adapters import nfl_file_feedback as F


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


def snapshot(value):
    return F._snapshot(value, keys={"meta", "rows"}, list_limits=(("rows", 10),),
                       snapshot_profile=F.PARTITION_PROFILE)


@pytest.mark.parametrize("row", [None, True, False, 0, -3, 1.25, -0.0, "", "plain",
                                 [], [1, None], {"nested": [True, "value"]}])
def test_scalar_and_container_rows_keep_exact_canonical_bytes(row):
    source = {"meta": row, "rows": [row]}
    before = copy.deepcopy(source)
    result = snapshot(source)
    assert result == source == before
    assert encoded(result) == encoded(before)
    assert result is not source and result["rows"] is not source["rows"]
    if type(row) in (list, dict):
        assert result["meta"] is not row
        assert result["rows"][0] is not row
        assert result["meta"] is not result["rows"][0]


@pytest.mark.parametrize("value", ["\u2603", "\U0001f600", "\ud800", "\udfff",
                                   '"\\\n\t\r\b\f\x00', {"\ud800": "\U0001f600\\\""}])
@pytest.mark.parametrize("depth", [1, 2])
def test_wrapped_byte_count_preserves_unicode_escaping_and_per_record_limit(value, depth):
    wrapped = [value] if depth == 1 else [[value]]
    expected = encoded(wrapped)
    with patch.object(F, "MAX_BYTES", len(expected)):
        cloned, size = F._plain_with_size(wrapped)
        assert F.plain(wrapped) == wrapped
    assert cloned == wrapped and cloned is not wrapped
    assert size == len(expected)
    assert size - 2 * depth == len(encoded(value))
    # The row itself fits, but its original depth-preserving wrappers do not.
    # Their byte budget must remain enforced before aggregate charging.
    assert len(encoded(value)) <= size - 1
    with patch.object(F, "MAX_BYTES", size - 1):
        with pytest.raises(ValueError, match="file_feedback_work_limit"):
            F._plain_with_size(wrapped)
        with pytest.raises(ValueError, match="file_feedback_work_limit"):
            F.plain(wrapped)


@pytest.mark.parametrize("meta,rows", [(None, []), ("\ud800", ["\U0001f600", "\\\n"]),
                                       ({"key": []}, [None, True, -0.0, {"x": [1]}])])
def test_complete_document_budget_is_exact_for_both_record_depths(meta, rows):
    source = {"meta": meta, "rows": rows}
    size = len(encoded(source))
    with patch.object(F, "MAX_BYTES", size):
        assert encoded(snapshot(source)) == encoded(source)
    with patch.object(F, "MAX_BYTES", size - 1):
        with pytest.raises(ValueError, match="file_feedback_work_limit"):
            snapshot(source)


def test_aliases_are_detached_and_charged_per_occurrence_without_cross_call_cache():
    shared = {"inside": ["\u2603", {"key": "value"}]}
    source = {"meta": shared, "rows": [shared, shared]}
    size = len(encoded(source))
    with patch.object(F, "MAX_BYTES", size):
        first = snapshot(source)
    assert first["meta"] is not first["rows"][0]
    assert first["rows"][0] is not first["rows"][1]
    first["rows"][0]["inside"][1]["key"] = "changed output"
    assert source["meta"]["inside"][1]["key"] == "value"
    assert first["meta"]["inside"][1]["key"] == "value"
    assert first["rows"][1]["inside"][1]["key"] == "value"
    assert len(encoded({"meta": shared, "rows": [shared]})) < size - 1
    with patch.object(F, "MAX_BYTES", size - 1):
        with pytest.raises(ValueError, match="file_feedback_work_limit"):
            snapshot(source)
    shared["inside"][1]["key"] = "changed input"
    second = snapshot(source)
    assert second["rows"][0]["inside"][1]["key"] == "changed input"
    assert first["rows"][1]["inside"][1]["key"] == "value"


@pytest.mark.parametrize("depth", [1, 2])
def test_original_complete_root_depth_boundary_is_retained(depth):
    item = None
    for _ in range(32 - depth):
        item = [item]
    source = {"meta": item if depth == 1 else None,
              "rows": [item] if depth == 2 else []}
    assert snapshot(source) == source
    if depth == 1:
        source["meta"] = [item]
    else:
        source["rows"] = [[item]]
    with pytest.raises(ValueError, match="file_feedback_work_limit"):
        snapshot(source)


def test_expanded_alias_nodes_still_hit_original_per_record_budget_before_encoding():
    # Small aliased input expands beyond 2M nodes while remaining below depth32.
    # Node accounting must visit every occurrence, not memoize shared identity.
    item = [0] * 32
    for _ in range(16):
        item = [item, item]
    with patch.object(F, "_encoded", side_effect=AssertionError("encoded before node refusal")):
        with pytest.raises(ValueError, match="file_feedback_work_limit"):
            F._plain_with_size([[item]])


def test_strict_types_nonfinite_values_integer_bounds_and_cycles_are_unchanged():
    class DictSubclass(dict):
        pass

    class ListSubclass(list):
        pass

    class StringSubclass(str):
        pass

    cycle = []
    cycle.append(cycle)
    invalid = [float("nan"), float("inf"), -float("inf"), 1 << 4096,
               (1,), {1: "bad key"}, object(), DictSubclass(), ListSubclass(),
               StringSubclass("value"), cycle]
    for item in invalid:
        for operation in (F.plain, F._plain_with_size):
            with pytest.raises(ValueError):
                operation(item)
        with pytest.raises(ValueError):
            snapshot({"meta": None, "rows": [item]})
    largest_valid_integer = (1 << 4096) - 1
    assert F.plain(largest_valid_integer) == largest_valid_integer


def test_root_shapes_and_collection_bounds_precede_any_private_or_public_clone():
    invalid = [[], {"meta": None}, {"meta": None, "rows": ()},
               {"meta": None, "rows": [], "extra": 0},
               {"meta": None, "rows": [None] * 11}, {1: None, "rows": []}]
    for source in invalid:
        with patch.object(F, "plain", side_effect=AssertionError("cloned invalid root")), \
                patch.object(F, "_plain_with_size", side_effect=AssertionError("cloned invalid root")):
            with pytest.raises(ValueError):
                snapshot(source)


def test_each_record_is_canonically_encoded_once_not_iterated_again_for_size():
    source = {"meta": {"clock": "now"}, "rows": [None, "\ud800", {"v": [1, 2]}]}
    one_shot_calls = []
    original_encoder = json.JSONEncoder

    class RecordingEncoder(original_encoder):
        def iterencode(self, value, _one_shot=False):
            one_shot_calls.append(_one_shot)
            return super().iterencode(value, _one_shot=_one_shot)

    with patch.object(F, "_encoded", wraps=F._encoded) as encode, \
            patch.object(F.json, "JSONEncoder", RecordingEncoder):
        result = snapshot(source)
    record_count = len(source["rows"]) + 1
    assert result == source
    assert encode.call_count == record_count + len(source)  # Each root key once, too.
    records = [call.args[0] for call in encode.call_args_list if type(call.args[0]) is list]
    assert records == [[source["meta"]], *[[[row]] for row in source["rows"]]]
    # JSONEncoder.encode handles string root keys directly; only the wrapped
    # non-string records reach iterencode. Keep their count exact so the old
    # second row encoding cannot pass just because all content bytes agree.
    assert len(one_shot_calls) == record_count
    assert all(one_shot_calls), "a second streaming encode was used only to count a record"
