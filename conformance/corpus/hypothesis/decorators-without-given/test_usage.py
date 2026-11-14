"""`@example`, `@seed` and `@settings` without `@given`, and a collected `@composite`, are
usage errors the plugin raises in the test (`InvalidArgument`); a plain test is untouched."""

from hypothesis import example, seed, settings, strategies as st


@settings(max_examples=5)
def test_settings_alone():
    pass


@example(1)
def test_example_alone(x=1):
    pass


@seed(3)
def test_seed_alone():
    pass


@st.composite
def test_composite_strategy(draw):
    return draw(st.integers())


def test_plain():
    pass
