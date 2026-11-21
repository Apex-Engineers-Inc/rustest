from hook_log import LOG


def test_first():
    pass


def test_zz_the_log_is_pytests():
    assert LOG[:4] == [
        "conftest-configure",
        "plugin-configure",
        ("registered", "the-plugin"),
        "conftest-configure-done",
    ]
    assert ("test_first", "passed", [("plugin", True)]) in LOG
