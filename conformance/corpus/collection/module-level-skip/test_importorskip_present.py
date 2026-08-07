"""The other half of `importorskip`: a module that *is* importable is returned, not skipped.

`json.dumps` is called so the case fails loudly if the return value were ever a stub
rather than the real module -- `importorskip` returning the wrong object would otherwise
be invisible, since the file collects either way.
"""

import pytest

json = pytest.importorskip("json")
dotted = pytest.importorskip("os.path")


def test_returns_the_real_module():
    assert json.dumps({"a": 1}) == '{"a": 1}'


def test_returns_the_leaf_of_a_dotted_name():
    assert dotted.basename("/a/b/c.txt") == "c.txt"
