"""`pytest_sessionstart` runs once, before collection: state it sets is visible to every test."""

import os


def pytest_sessionstart(session):
    os.environ["RUSTEST_CORPUS_SESSIONSTART"] = "ran"
