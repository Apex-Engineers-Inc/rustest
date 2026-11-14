class TestBox:
    def test_method(self):
        assert True


class Helper:  # not collected: name doesn't match Test*
    def test_ignored(self):
        raise AssertionError("must not run")


class TestWithInit:  # pytest skips classes with __init__
    def __init__(self):
        pass

    def test_ignored(self):
        raise AssertionError("must not run")
