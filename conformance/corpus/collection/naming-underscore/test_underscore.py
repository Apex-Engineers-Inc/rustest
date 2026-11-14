def _test_hidden():
    raise AssertionError("must not be collected")


def test_visible():
    assert True
