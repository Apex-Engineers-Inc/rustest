"""A test module that uses pytest's own ``pytester``: only pytest can run it.

rustest runs this file under one pytest subprocess before its own run (B-lite), and the rest of
the session natively; the ids, the order and the tally are pytest's.
"""

pytest_plugins = ["pytester"]


def test_an_inner_session_passes(pytester):
    pytester.makepyfile(test_inner="def test_x():\n    pass\n")
    result = pytester.runpytest("-p", "no:cacheprovider")
    result.assert_outcomes(passed=1)


def test_a_plain_test_beside_it_fails():
    assert False
