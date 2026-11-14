"""`iter_markers` lists the item's own marks (the parameter set's) before the module's:
the first xfail found is the non-strict one, so the pass is XPASS, not a strict failure."""

import pytest

pytestmark = pytest.mark.xfail(reason="module", strict=True)


@pytest.mark.parametrize(
    "x", [pytest.param(1, marks=pytest.mark.xfail(reason="param", strict=False))]
)
def test_p(x):
    pass
