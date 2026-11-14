"""The file that must still run. Its two tests are the whole point of the case.

A module-level skip is not a collection error, so the session does not abort and these
are collected and executed. If they ever vanish, the skip has been misrouted into
`errors` and every sibling file went with it.
"""


def test_first():
    assert True


def test_second():
    assert 1 + 1 == 2
