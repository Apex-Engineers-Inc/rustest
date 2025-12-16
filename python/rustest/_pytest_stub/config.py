"""
Stub for _pytest.config

MIGRATION GUIDE:
Instead of:
    from _pytest.config import Config

    def pytest_configure(config: Config):
        config.addinivalue_line(...)

Use the pytestconfig fixture:
    def test_example(pytestconfig):
        pytestconfig.getoption("verbose")
        pytestconfig.getini("markers")

Or use type hints without importing:
    from typing import TYPE_CHECKING
    if TYPE_CHECKING:
        from _pytest.config import Config
"""

import warnings


warnings.warn(
    "Importing from _pytest.config is not recommended. "
    "Use the 'pytestconfig' fixture instead. "
    "This stub is provided for type hints only.",
    DeprecationWarning,
    stacklevel=2,
)


class Config:
    """
    Stub Config class - primarily for type hints.

    For actual config access, use the 'pytestconfig' fixture:

        def test_example(pytestconfig):
            verbose = pytestconfig.getoption("verbose")
            markers = pytestconfig.getini("markers")

    This class exists for type annotations in pytest hooks:

        def pytest_configure(config: Config):  # Type hint only
            config.addinivalue_line("markers", "...")
    """

    def getoption(self, name: str, default=None):
        """
        Get a command-line option value.

        In rustest, use the pytestconfig fixture instead:
            def test_example(pytestconfig):
                pytestconfig.getoption("verbose")
        """
        raise NotImplementedError(
            "Config.getoption() not supported. Use the 'pytestconfig' fixture instead."
        )

    def getini(self, name: str):
        """Get a configuration value from pytest.ini / setup.cfg / pyproject.toml"""
        raise NotImplementedError(
            "Config.getini() not supported. Use the 'pytestconfig' fixture instead."
        )

    def addinivalue_line(self, name: str, line: str):
        """
        Add a line to an ini-value.

        Often used in pytest_configure hooks. In rustest, this is silently
        ignored since rustest doesn't use pytest's plugin system.
        """
        # Silently ignore - often used in pytest_configure hooks
        pass

    # Type hint stubs
    @property
    def rootdir(self): ...

    @property
    def inifile(self): ...

    @property
    def option(self): ...

    @property
    def pluginmanager(self): ...
