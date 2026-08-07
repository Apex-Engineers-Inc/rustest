def test_outer():
    def test_inner():  # not collected: nested
        raise AssertionError("must not run")

    assert True
