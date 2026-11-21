"""pytest's xunit hooks are injected onto `unittest.TestCase` subclasses too.

Port target: `_pytest/unittest.py::UnitTestCase.collect` (l. 85-96), which calls THREE
registrars, not one:

    self._register_unittest_setup_method_fixture(cls)   # setup_method / teardown_method
    self._register_unittest_setup_class_fixture(cls)    # setUpClass   / tearDownClass
    self._register_setup_class_fixture()                # setup_class  / teardown_class

MECHANISM M4 of the Phase 4 Task 1b sweep. rustest ported only the middle one, so a
TestCase declaring shared state in `setup_class` never got it. dateutil's
`tests/test_parser.py::ParserTest` is exactly that shape: exactly the 8 tests of ~240 in
the class that read one of the five class attributes failed with `AttributeError`, and
every test that never touched class state passed -- a self-confirming failure set.

The adjacent case already worked: xunit hooks on a PLAIN class are honoured. What was
uncovered is specifically their injection onto TestCase subclasses.
"""

import unittest


class TCWithSetupClass(unittest.TestCase):
    """The dateutil shape: class state assigned by the *pytest* spelling."""

    @classmethod
    def setup_class(cls):
        cls.value = "assigned-by-setup_class"

    @classmethod
    def teardown_class(cls):
        cls.value = None

    def test_sees_class_state(self):
        assert getattr(self, "value", None) == "assigned-by-setup_class"

    def test_sees_class_state_from_a_second_method(self):
        assert type(self).value == "assigned-by-setup_class"


class TCWithSetupMethod(unittest.TestCase):
    """`setup_method(self, method)` -- pytest passes BOTH arguments, always."""

    def setup_method(self, method):
        self.mval = "assigned-by-setup_method"
        self.mname = method.__name__

    def teardown_method(self, method):
        self.mval = None

    def test_sees_instance_state(self):
        assert self.mval == "assigned-by-setup_method"

    def test_receives_the_bound_method(self):
        """`request.function` for a TestCaseFunction is the bound method, not None."""
        assert self.mname == "test_receives_the_bound_method"


class TCWithBothSpellings(unittest.TestCase):
    """Native and xunit hooks coexist: a TestCase gets all four.

    Also the regression guard for the instance: `setUp` and `setup_method` must write to
    the SAME object the body then reads, which is why the instance is built once during
    setup and reused, exactly as pytest caches `Function._instance`.
    """

    @classmethod
    def setUpClass(cls):
        cls.native_class = 1

    @classmethod
    def setup_class(cls):
        cls.xunit_class = 2

    def setUp(self):
        self.native_method = 3

    def setup_method(self, method):
        self.xunit_method = 4

    def test_all_four_ran_on_one_instance(self):
        assert (
            self.native_class,
            self.xunit_class,
            self.native_method,
            self.xunit_method,
        ) == (1, 2, 3, 4)


class TCPlainUnittest(unittest.TestCase):
    """A TestCase with no xunit hooks at all must be byte-identical to before."""

    def setUp(self):
        self.value = 7

    def test_plain(self):
        assert self.value == 7


class PlainClassWithXunit:
    """The adjacent, already-working case, kept as the control."""

    @classmethod
    def setup_class(cls):
        cls.value = "plain"

    def setup_method(self, method):
        self.method_name = method.__name__

    def test_plain_class_still_works(self):
        assert self.value == "plain"
        assert self.method_name == "test_plain_class_still_works"
