"""``_pytest.mark.structures``: the mark classes rustest uses, under pytest's names."""

from rustest._hookshim import Mark
from rustest.decorators import MarkDecorator, MarkGenerator, ParameterSet

__all__ = ["Mark", "MarkDecorator", "MarkGenerator", "ParameterSet"]
