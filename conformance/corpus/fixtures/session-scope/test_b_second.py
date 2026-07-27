def test_second(visits):
    visits.append("second")
    # Only true if this file's `visits` is the *same* object `test_a_first.py` appended
    # to -- i.e. only if session scope spans the run rather than one worker.
    assert visits == ["first", "second"]
