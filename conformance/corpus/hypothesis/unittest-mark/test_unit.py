import unittest

from hypothesis import given, settings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, rule


class Counter(RuleBasedStateMachine):
    @rule()
    def step(self):
        pass


Counter.TestCase.settings = settings(max_examples=2, stateful_step_count=2)
TestCounter = Counter.TestCase


class TestCase(unittest.TestCase):
    @given(st.integers())
    def test_given(self, x):
        pass

    def test_plain(self):
        pass
