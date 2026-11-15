pytest_plugins = ["pytester"]


def test_inner(pytester):
    pytester.makepyfile(test_nested="def test_x():\n    pass\n")
    pytester.runpytest("-p", "no:cacheprovider").assert_outcomes(passed=1)
