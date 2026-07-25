from conftest import applied


def test_autouse_applied():
    assert len(applied) == 1
