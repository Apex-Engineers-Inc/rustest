def test_first(visits):
    visits.append("first")
    assert visits == ["first"]
