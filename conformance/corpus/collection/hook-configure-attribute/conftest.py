"""networkx's pattern: `pytest_configure` stores an attribute on `config` that fixtures read
later through `request.config`; an option's call-site default never overrides a declared one."""


def pytest_addoption(parser):
    parser.addoption("--backend", action="store", default=None)
    parser.addini("flavour", "a flavour", default="plain")


def pytest_configure(config):
    config.backend = config.getoption("--backend") or "builtin"
