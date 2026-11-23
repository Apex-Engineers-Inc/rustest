import os


def test_sessionstart_ran_before_any_test():
    assert os.environ.get("RUSTEST_CORPUS_SESSIONSTART") == "ran"


def test_it_is_still_set_in_a_second_test():
    assert os.environ["RUSTEST_CORPUS_SESSIONSTART"] == "ran"
