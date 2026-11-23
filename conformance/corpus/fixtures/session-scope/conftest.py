"""A **session**-scoped fixture shared by two sibling files.

The list is the fixture: whether the two test files see the *same* list is exactly the
question of whether session scope spans the whole run. A counter would not do -- two
independent counters both start at 0 and every assertion would pass for the wrong
reason -- so the fixture is mutable state that each test appends to and inspects.
"""

import pytest


@pytest.fixture(scope="session")
def visits():
    return []
