"""`pytestmark` holding a mark as a *value*, in every shape pytest accepts.

`_pytest/mark/structures.py::get_unpacked_marks` (l. 407-437) reads the attribute, unpacks
it only if it is a `list`, and requires every entry to be a `Mark` -- so the object a
`pytest.mark.<name>` expression evaluates to has to answer `.name`/`.args`/`.kwargs` even
when it was never called. A `mark.<name>` implemented as a plain *method* does not: it is a
bound method, and rustest refused the entire file with

    malformed pytestmark entry on '<module>': <bound method MarkGenerator.asyncio ...>

`asyncio` was the last standard mark still in method form after #136/#137 converted
`skipif`/`xfail`/`usefixtures`. Four Apex Member Designer modules write
`pytestmark = pytest.mark.asyncio`, and since a collection error raises `Interrupted`
before the first item, those four files stopped all 6 132 of that suite's tests from
running (Phase 3 Task 4 report, section 4.6).
"""

import pytest

pytestmark = [pytest.mark.xfail, pytest.mark.usefixtures("seed")]


@pytest.fixture
def seed():
    return 1


def test_module_marks_apply():
    assert False


class TestBox:
    pytestmark = pytest.mark.skipif

    def test_class_mark_applies(self):
        assert False
