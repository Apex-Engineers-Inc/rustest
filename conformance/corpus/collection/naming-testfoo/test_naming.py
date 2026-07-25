def test_proper():
    assert True


def testfoo():  # pytest does NOT collect this (python_functions = "test_*")
    assert True
