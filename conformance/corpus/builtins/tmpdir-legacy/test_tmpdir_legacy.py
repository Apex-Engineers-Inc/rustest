"""`tmpdir` and `tmpdir_factory`: pytest's legacy `py.path.local` flavours of `tmp_path`.

`tmpdir` is `legacy_path(tmp_path)`, so it is the *same directory*; the `py.path` API
(`join`, `write`, `read`, `check`, `strpath`) is what suites written before `pathlib` use.
"""


def test_tmpdir_is_a_py_path_over_the_tmp_path_directory(tmpdir, tmp_path):
    assert str(tmpdir) == str(tmp_path)
    target = tmpdir.join("a.txt")
    target.write("hello")
    assert target.read() == "hello"
    assert (tmp_path / "a.txt").read_text() == "hello"
    assert tmpdir.check(dir=1)
    assert tmpdir.strpath == str(tmp_path)


def test_tmpdir_factory_makes_numbered_py_path_directories(tmpdir_factory):
    first = tmpdir_factory.mktemp("made")
    second = tmpdir_factory.mktemp("made")
    assert first.check(dir=1) and second.check(dir=1)
    assert first != second
    assert tmpdir_factory.getbasetemp().check(dir=1)
