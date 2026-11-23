import pytest

from hook_log import LOG


def test_uses_resource(resource):
    LOG.append("body")


@pytest.mark.skip(reason="never reaches the plain hook")
def test_skipped(resource):
    pass


class TestInClass:
    def test_method(self):
        LOG.append("method-body")


def test_zz_the_log_is_pytests():
    assert LOG == EXPECTED


EXPECTED: list[object] = [
    ("logstart", "test_uses_resource", "test_uses_resource"),
    ("setup-tryfirst", "test_uses_resource"),
    ("setup", "test_uses_resource", False),
    "resource-up",
    ("setup-trylast", "test_uses_resource", True),
    ("call", "test_uses_resource", True),
    "body",
    ("call-trylast", "test_uses_resource"),
    ("teardown", "test_uses_resource"),
    "resource-down",
    ("logfinish", "test_uses_resource"),
    ("logstart", "test_skipped", "test_skipped"),
    ("setup-tryfirst", "test_skipped"),
    ("teardown", "test_skipped"),
    ("logfinish", "test_skipped"),
    ("logstart", "test_method", "TestInClass.test_method"),
    ("setup-tryfirst", "test_method"),
    ("setup", "test_method", False),
    ("setup-trylast", "test_method", False),
    ("call", "test_method", False),
    "method-body",
    ("call-trylast", "test_method"),
    ("teardown", "test_method"),
    ("logfinish", "test_method"),
    ("logstart", "test_zz_the_log_is_pytests", "test_zz_the_log_is_pytests"),
    ("setup-tryfirst", "test_zz_the_log_is_pytests"),
    ("setup", "test_zz_the_log_is_pytests", False),
    ("setup-trylast", "test_zz_the_log_is_pytests", False),
    ("call", "test_zz_the_log_is_pytests", False),
]
