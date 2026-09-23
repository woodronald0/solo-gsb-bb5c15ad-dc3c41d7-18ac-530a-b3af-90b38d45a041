"""End-to-end tests for POST /merge (three-way merge against a base)."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _merge(base, left, right):
    return client.post(
        "/merge", json={"base": base, "left": left, "right": right}
    )


# ---------------------------------------------------------------------------
# Clean merges
# ---------------------------------------------------------------------------


def test_merge_combines_non_conflicting_edits_from_both_sides():
    base = {
        "subject": {"name": "Ada", "age": 36, "city": "London"},
        "keep": True,
        "gone": "x",
    }
    left = {
        "subject": {"name": "Ada", "age": 37, "city": "London"},
        "keep": True,
        "gone": "x",
        "addedByLeft": [1],
    }
    right = {
        "subject": {"name": "Ada Lovelace", "age": 36, "city": "London"},
        "keep": True,
    }

    resp = _merge(base, left, right)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "merged": {
            "subject": {"name": "Ada Lovelace", "age": 37, "city": "London"},
            "keep": True,
            "addedByLeft": [1],
        },
        "conflicts": [],
    }


def test_merge_takes_the_changed_side_wholesale():
    base = {"a": {"x": 1, "y": 2}, "b": [1, 2]}
    left = {"a": {"x": 1, "y": 2}, "b": [1, 2]}
    right = {"a": {"x": 9}, "b": []}

    # Only right changed: the merged document is exactly right.
    resp = _merge(base, left, right)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"merged": right, "conflicts": []}

    # Only left changed: the merged document is exactly left.
    resp = _merge(base, right, left)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"merged": right, "conflicts": []}

    # Neither side changed: the merged document is exactly base.
    resp = _merge(base, base, base)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"merged": base, "conflicts": []}


def test_merge_identical_edits_on_both_sides_are_taken_once():
    base = {"a": 1, "gone": "v", "nested": {"x": 1}}
    left = {"a": 2, "nested": {"x": 1, "y": 2}, "new": {"deep": []}}
    right = {"a": 2, "nested": {"x": 1, "y": 2}, "new": {"deep": []}}

    resp = _merge(base, left, right)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"merged": left, "conflicts": []}


def test_merge_recurses_into_objects_changed_on_both_sides():
    base = {"a": {"x": 1, "y": 1, "z": 1}}
    left = {"a": {"x": 2, "y": 1, "z": 1}}
    right = {"a": {"x": 1, "y": 2, "z": 1}}

    resp = _merge(base, left, right)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "merged": {"a": {"x": 2, "y": 2, "z": 1}},
        "conflicts": [],
    }


def test_merge_empty_documents_and_empty_containers():
    assert _merge({}, {}, {}).json() == {"merged": {}, "conflicts": []}

    # Distinct members added on each side merge cleanly.
    resp = _merge({}, {"a": {}}, {"b": []})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"merged": {"a": {}, "b": []}, "conflicts": []}

    # An empty container replacing a scalar on one side only is taken.
    resp = _merge({"a": 1}, {"a": {}}, {"a": 1})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"merged": {"a": {}}, "conflicts": []}


def test_merge_root_scalars():
    # Only one side changed the root scalar.
    assert _merge(1, 2, 1).json() == {"merged": 2, "conflicts": []}
    # Both sides changed it to the same value.
    assert _merge(1, 2, 2).json() == {"merged": 2, "conflicts": []}
    # A null document is a valid side.
    assert _merge(None, None, None).json() == {"merged": None, "conflicts": []}


# ---------------------------------------------------------------------------
# Value comparison rules
# ---------------------------------------------------------------------------


def test_merge_numbers_with_different_canonical_forms_are_distinct():
    # 1 and 1.0 differ canonically: left alone changed the value.
    resp = _merge({"a": 1}, {"a": 1.0}, {"a": 1})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"merged": {"a": 1.0}, "conflicts": []}

    # Both sides changed to canonically different numbers: conflict.
    resp = _merge({"a": 1}, {"a": 1.0}, {"a": 2})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "merged" not in body
    assert body["conflicts"] == [
        {
            "path": "/a",
            "base": {"present": True, "value": 1},
            "left": {"present": True, "value": 1.0},
            "right": {"present": True, "value": 2},
        }
    ]

    # true and 1 are distinct canonical values as well.
    resp = _merge({"a": True}, {"a": 1}, {"a": True})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"merged": {"a": 1}, "conflicts": []}


def test_merge_distinguishes_missing_member_from_null():
    # Left sets the member to null, right deletes it: both changed the same
    # path differently (null is a value, absence is not).
    resp = _merge({"a": 1}, {"a": None}, {})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "merged" not in body
    assert body["conflicts"] == [
        {
            "path": "/a",
            "base": {"present": True, "value": 1},
            "left": {"present": True, "value": None},
            "right": {"present": False, "value": None},
        }
    ]

    # Deleting a member that was already null is still a change: the side
    # that did not change loses.
    resp = _merge({"a": None}, {}, {"a": None})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"merged": {}, "conflicts": []}

    # Both sides set the member to null: identical edit, no conflict.
    resp = _merge({"a": 1}, {"a": None}, {"a": None})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"merged": {"a": None}, "conflicts": []}


def test_merge_arrays_are_atomic_values():
    # One side changed the array: that side wins wholesale.
    resp = _merge({"a": [1, 2]}, {"a": [1, 2, 3]}, {"a": [1, 2]})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"merged": {"a": [1, 2, 3]}, "conflicts": []}

    # Both sides changed the array identically: taken once.
    resp = _merge({"a": [1]}, {"a": [1, 2]}, {"a": [1, 2]})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"merged": {"a": [1, 2]}, "conflicts": []}

    # Both sides changed the array differently: the whole array conflicts,
    # there is no element-wise merge.
    resp = _merge({"a": [1, 2]}, {"a": [1, 2, 3]}, {"a": [1, 3]})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "merged" not in body
    assert body["conflicts"] == [
        {
            "path": "/a",
            "base": {"present": True, "value": [1, 2]},
            "left": {"present": True, "value": [1, 2, 3]},
            "right": {"present": True, "value": [1, 3]},
        }
    ]


# ---------------------------------------------------------------------------
# Conflicts
# ---------------------------------------------------------------------------


def test_merge_delete_versus_modify_is_a_conflict():
    # Left deletes the member, right modifies it.
    resp = _merge({"a": 1, "b": 2}, {"b": 2}, {"a": 2, "b": 2})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "merged" not in body
    assert body["conflicts"] == [
        {
            "path": "/a",
            "base": {"present": True, "value": 1},
            "left": {"present": False, "value": None},
            "right": {"present": True, "value": 2},
        }
    ]

    # Deleting a whole subtree races a modification inside it: the conflict
    # is reported at the subtree root, not at the nested leaf.
    base = {"a": {"x": 1, "y": 2}, "b": 1}
    left = {"b": 1}
    right = {"a": {"x": 2, "y": 2}, "b": 1}
    resp = _merge(base, left, right)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "merged" not in body
    assert body["conflicts"] == [
        {
            "path": "/a",
            "base": {"present": True, "value": {"x": 1, "y": 2}},
            "left": {"present": False, "value": None},
            "right": {"present": True, "value": {"x": 2, "y": 2}},
        }
    ]


def test_merge_nested_conflicts_are_all_reported_sorted_without_partial_doc():
    base = {
        "a": {"x": 1, "y": 1, "ok": 1},
        "b": {"z": 1},
        "c": 1,
        "d": {"deep": {"k": 1}},
    }
    left = {
        "a": {"x": 2, "y": 1, "ok": 1},
        "b": {"z": 2},
        "c": 2,
        "d": {"deep": {"k": 1}},
    }
    right = {
        "a": {"x": 3, "y": 1, "ok": 2},
        "b": {},
        "c": 3,
        "d": {"deep": {"k": 1, "added": True}},
    }

    resp = _merge(base, left, right)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # No partial merge document is returned when conflicts exist.
    assert "merged" not in body

    # /a/x: both sides changed it differently -> conflict.
    # /b: left modified /b/z while right emptied the object -> conflict.
    # /c: both sides changed it differently -> conflict.
    # /a/ok and /d/deep/added changed on one side only -> no conflict, but
    # they must not leak a partial document either.
    assert body["conflicts"] == [
        {
            "path": "/a/x",
            "base": {"present": True, "value": 1},
            "left": {"present": True, "value": 2},
            "right": {"present": True, "value": 3},
        },
        {
            "path": "/b/z",
            "base": {"present": True, "value": 1},
            "left": {"present": True, "value": 2},
            "right": {"present": False, "value": None},
        },
        {
            "path": "/c",
            "base": {"present": True, "value": 1},
            "left": {"present": True, "value": 2},
            "right": {"present": True, "value": 3},
        },
    ]
    # Conflicts are sorted by RFC 6901 pointer.
    paths = [entry["path"] for entry in body["conflicts"]]
    assert paths == sorted(paths)


def test_merge_both_sides_adding_the_same_new_member_differently_conflicts():
    resp = _merge({}, {"a": 1}, {"a": 2})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "merged" not in body
    assert body["conflicts"] == [
        {
            "path": "/a",
            "base": {"present": False, "value": None},
            "left": {"present": True, "value": 1},
            "right": {"present": True, "value": 2},
        }
    ]

    # New objects added on both sides with different members do not merge
    # recursively: there is no common base object to align them against.
    resp = _merge({}, {"a": {"x": 1}}, {"a": {"y": 2}})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "merged" not in body
    assert body["conflicts"] == [
        {
            "path": "/a",
            "base": {"present": False, "value": None},
            "left": {"present": True, "value": {"x": 1}},
            "right": {"present": True, "value": {"y": 2}},
        }
    ]


def test_merge_type_changes_and_root_conflicts():
    # Object vs scalar at the same path: both sides changed differently.
    resp = _merge({"a": {"x": 1}}, {"a": 1}, {"a": {"x": 2}})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "merged" not in body
    assert body["conflicts"] == [
        {
            "path": "/a",
            "base": {"present": True, "value": {"x": 1}},
            "left": {"present": True, "value": 1},
            "right": {"present": True, "value": {"x": 2}},
        }
    ]

    # Root scalars diverging on both sides conflict at the empty pointer.
    resp = _merge(1, 2, 3)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "merged" not in body
    assert body["conflicts"] == [
        {
            "path": "",
            "base": {"present": True, "value": 1},
            "left": {"present": True, "value": 2},
            "right": {"present": True, "value": 3},
        }
    ]


def test_merge_conflict_paths_use_escaped_rfc6901_pointers():
    base = {"a/b": 1, "tilde~key": 1}
    left = {"a/b": 2, "tilde~key": 1}
    right = {"a/b": 3, "tilde~key": 1}

    resp = _merge(base, left, right)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "merged" not in body
    assert [entry["path"] for entry in body["conflicts"]] == ["/a~1b"]


# ---------------------------------------------------------------------------
# Request validation
# ---------------------------------------------------------------------------


def test_merge_requires_all_three_documents():
    resp = client.post("/merge", json={"left": {}, "right": {}})
    assert resp.status_code == 400
    assert resp.json()["error"]["loc"] == "base"

    resp = client.post("/merge", json={"base": {}, "right": {}})
    assert resp.status_code == 400
    assert resp.json()["error"]["loc"] == "left"

    resp = client.post("/merge", json={"base": {}, "left": {}})
    assert resp.status_code == 400
    assert resp.json()["error"]["loc"] == "right"

    # A present-but-null document is valid (null is a JSON document).
    resp = client.post(
        "/merge", json={"base": None, "left": None, "right": None}
    )
    assert resp.status_code == 200


def test_merge_rejects_non_object_body_and_malformed_json():
    resp = client.post("/merge", json=[{"base": {}}])
    assert resp.status_code == 400
    err = resp.json()["error"]
    assert err["code"] == "INVALID_REQUEST"
    assert err["loc"] == "body"

    resp = client.post("/merge", content=b'{"base": {},', headers={})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_JSON"

    # Duplicate object member names are rejected by the strict parser.
    resp = client.post(
        "/merge",
        content=b'{"base": {"a": 1, "a": 2}, "left": {}, "right": {}}',
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_JSON"


def test_merge_response_shape_is_stable():
    # Success: both keys, conflicts empty.
    body = _merge({"a": 1}, {"a": 2}, {"a": 1}).json()
    assert set(body) == {"merged", "conflicts"}
    assert body["conflicts"] == []

    # Conflict: only the conflicts list, never a partial merge.
    body = _merge({"a": 1}, {"a": 2}, {"a": 3}).json()
    assert set(body) == {"conflicts"}
    assert len(body["conflicts"]) == 1
