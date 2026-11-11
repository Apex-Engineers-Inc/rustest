"""A hook that touches something the hook runtime does not provide, inside an `except
Exception` that would swallow an ordinary error. The runtime's refusal is a BaseException and
is recorded before it is raised, so the session still goes to pytest, which answers it."""


def pytest_configure(config):
    try:
        config.pluginmanager.register(object(), "rustest-shim-gap-probe")
    except Exception:
        pass
