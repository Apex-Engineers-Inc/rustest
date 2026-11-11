def test_the_attribute_set_in_configure(request):
    assert request.config.backend == "builtin"


def test_a_declared_option_ignores_the_call_site_default(pytestconfig):
    assert pytestconfig.getoption("--backend", default="other") is None


def test_an_ini_declaration_default(pytestconfig):
    assert pytestconfig.getini("flavour") == "plain"
