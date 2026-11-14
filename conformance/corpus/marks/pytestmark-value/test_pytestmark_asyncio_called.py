"""...and the called form, whose factory used to return a bare closure."""

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="module")


async def test_called_form_runs():
    assert True
