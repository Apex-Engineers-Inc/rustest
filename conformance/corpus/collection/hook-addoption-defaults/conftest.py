"""Option defaults by `action`, `type`, `dest`; read as `getoption` (flag or dest) and `option.x`."""


def pytest_addoption(parser):
    parser.addoption("--flag", action="store_true")
    parser.addoption("--noflag", action="store_false")
    parser.addoption("--plain")
    parser.addoption("--level", type=int, default="3")
    parser.addoption("--db-url", dest="url", default="sqlite://")
    parser.getgroup("mine", "My options").addoption("--grouped", default=7)
