"""Registers `late` when the walk reaches `zz/`, after `test_a.py` was imported: its
`@pytest.mark.late` is `PytestUnknownMarkWarning`, an error under `filterwarnings = error`."""


def pytest_configure(config):
    config.addinivalue_line("markers", "late: registered by a later conftest")
