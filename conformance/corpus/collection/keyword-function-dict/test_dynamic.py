import functools


def deco(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)

    return wrapper


@deco
def test_wrapped():
    pass


def test_attribute():
    pass


test_attribute.custom_flag = True


def test_other():
    pass
