"""A session-scoped fixture whose teardown raises at the end of the run, with one module:
the last test's error (pytest's `nextitem=None` teardown)."""


def test_first(session_resource):
    pass


def test_last():
    pass
