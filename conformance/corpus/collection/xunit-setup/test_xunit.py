"""xunit-style setup/teardown, which pytest injects as autouse fixtures.

`_pytest/python.py` registers four of them -- `_register_setup_module_fixture` and
`_register_setup_function_fixture` (l. 554-555, before `parsefactories`), and
`_register_setup_class_fixture`/`_register_setup_method_fixture` (l. 769-770, likewise).
None of the four was called by the v2 worker, so a class that builds its state in
`setup_method` reported `AttributeError: ... has no attribute 'dy'` -- 8 tests on the
acceptance target.

Registration order is load-bearing and pinned by `test_ordering.py` next door: the xunit
fixtures go in *before* the module's own, so `setup_function` runs ahead of a user's autouse
fixture rather than after it.
"""

EVENTS = []


def setup_module(module):
    EVENTS.append(("setup_module", module.__name__))


def teardown_module():
    """The no-argument form is legal too -- `_call_with_optional_argument` counts params."""
    EVENTS.append(("teardown_module",))


def setup_function(function):
    EVENTS.append(("setup_function", function.__name__))


def teardown_function(function):
    EVENTS.append(("teardown_function", function.__name__))


def test_module_and_function_hooks_ran():
    assert EVENTS[0] == ("setup_module", "test_xunit")
    assert EVENTS[-1] == ("setup_function", "test_module_and_function_hooks_ran")


class TestBox:
    @classmethod
    def setup_class(cls):
        EVENTS.append(("setup_class", cls.__name__))

    @classmethod
    def teardown_class(cls):
        EVENTS.append(("teardown_class", cls.__name__))

    def setup_method(self, method):
        self.value = 7
        EVENTS.append(("setup_method", method.__name__))

    def teardown_method(self, method):
        EVENTS.append(("teardown_method", method.__name__))

    def test_state_from_setup_method(self):
        assert self.value == 7

    def test_setup_function_stands_down_inside_a_class(self):
        assert ("setup_function", "test_setup_function_stands_down_inside_a_class") not in EVENTS


def test_z_the_whole_event_log():
    assert EVENTS == [
        ("setup_module", "test_xunit"),
        ("setup_function", "test_module_and_function_hooks_ran"),
        ("teardown_function", "test_module_and_function_hooks_ran"),
        ("setup_class", "TestBox"),
        ("setup_method", "test_state_from_setup_method"),
        ("teardown_method", "test_state_from_setup_method"),
        ("setup_method", "test_setup_function_stands_down_inside_a_class"),
        ("teardown_method", "test_setup_function_stands_down_inside_a_class"),
        ("teardown_class", "TestBox"),
        ("setup_function", "test_z_the_whole_event_log"),
    ]
