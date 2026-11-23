"""One test in a subdirectory whose NAME sorts before every root test file.

pytest descends `sub/` at the position "sub" sorts to among its siblings, so this
id is emitted first -- before `test_a.py`'s -- even though the file itself sorts
last alphabetically by full path.
"""


def test_in_subdir():
    assert True
