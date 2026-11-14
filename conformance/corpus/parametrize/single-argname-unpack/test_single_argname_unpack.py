"""A length-1 sequence under a single argname is ONE value, not an unpacked value.

Port target: `_pytest/mark/structures.py::ParameterSet._for_parametrize` (l. 188-227) and
the `force_tuple` it gets from `_parse_parametrize_args` (l. 165-177) --
`len(argnames) == 1` **and only when argnames is a str**.

MECHANISM M2 of the Phase 4 Task 1b sweep, and its priority item, because it is *usually
silent*: rustest decided unpacking by comparing lengths, so `[[42]]` bound `value = 42`
where pytest binds `value = [42]`. The test still runs and usually still passes, having
tested something other than what its author wrote. Sighted five times across four
independent suites (Member Designer, marshmallow, werkzeug, attrs x2); in attrs'
`test_setattr` the only observable effect was a changed **node id**, with both cases
passing on both runners.

Every assertion below is on the *identity of the bound value*, not on a derived property,
because that is the thing that was wrong. The node ids are graded too, by both v2 gates.
"""

import pytest

#: werkzeug's `test_range_validates_ranges` shape, reduced: four argvalue sets under one
#: name, three of length 1 and one of length 2. Under the old rule the three length-1 sets
#: were unpacked and the length-2 one was not -- four predictions, four hits, in the sweep.
_RANGES = [(0,), (0, 1), (5,), (7,)]


@pytest.mark.parametrize("rng", _RANGES)
def test_length_one_tuple_stays_a_tuple(rng):
    assert isinstance(rng, tuple)
    assert rng in _RANGES


@pytest.mark.parametrize("value", [[42], [7, 8], []])
def test_length_one_list_stays_a_list(value):
    assert isinstance(value, list)


@pytest.mark.parametrize("value", [pytest.param([5]), pytest.param([6, 7], id="pair")])
def test_pytest_param_wrapping_is_unchanged(value):
    """`extract_from` returns an existing ParameterSet as it stands (l. 153-154)."""
    assert isinstance(value, list)


@pytest.mark.parametrize(("solo",), [(9,), (10,)])
def test_a_sequence_argnames_never_forces_a_tuple(solo):
    """`("solo",)` is a Sequence, so `force_tuple` is False and `(9,)` IS the value set."""
    assert solo in (9, 10)


@pytest.mark.parametrize("value", [{"a": 1}, {}])
def test_a_mapping_under_one_name_is_one_value(value):
    assert isinstance(value, dict)


@pytest.mark.parametrize("value", ["ab", "c"])
def test_a_string_under_one_name_is_one_value(value):
    """A str is a Sequence too; `force_tuple` is what stops it being split into chars."""
    assert value in ("ab", "c")


@pytest.mark.parametrize("a,b", [(1, 2), [3, 4]])
def test_two_names_still_unpack(a, b):
    assert b == a + 1


@pytest.mark.parametrize("pair", [(1, 2)])
@pytest.mark.parametrize("single", [(3,)])
def test_stacked_decorators_each_decide_alone(pair, single):
    assert pair == (1, 2)
    assert single == (3,)
