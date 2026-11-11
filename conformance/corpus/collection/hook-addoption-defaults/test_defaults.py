def test_defaults_by_action(pytestconfig):
    assert pytestconfig.getoption("--flag") is False
    assert pytestconfig.getoption("--noflag") is True
    assert pytestconfig.getoption("--plain") is None


def test_a_string_default_goes_through_type(pytestconfig):
    assert pytestconfig.getoption("--level") == 3


def test_dest_and_option_namespace(pytestconfig):
    assert pytestconfig.getoption("--db-url") == "sqlite://"
    assert pytestconfig.getoption("url") == "sqlite://"
    assert pytestconfig.option.url == "sqlite://" and pytestconfig.option.grouped == 7
