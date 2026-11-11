"""Under `filterwarnings = error`, an exception raised where Python cannot raise it -- a
`__del__`, or the `ResourceWarning` an unclosed file gives when it is collected -- fails the
test whose phase it happened in. pytest's unraisableexception plugin turns each into a
`PytestUnraisableExceptionWarning` at the end of the phase, and the `error` filter makes that
warning the phase's exception."""


class Leaky:
    def __del__(self):
        raise RuntimeError("boom in __del__")


def test_unraisable_exception():
    Leaky()


def test_unclosed_file():
    open(__file__)  # noqa: SIM115 - the leak is the point


def test_clean():
    pass
