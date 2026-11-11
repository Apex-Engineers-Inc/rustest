"""The hook runtime is on (a later conftest defines a hook), but `zz/conftest.py` is
imported only when the walk reaches `zz/`: `test_a.py` is imported before it, so the
`sys.path` entry it adds is not there yet and `import mylib_late` fails, as in pytest."""
