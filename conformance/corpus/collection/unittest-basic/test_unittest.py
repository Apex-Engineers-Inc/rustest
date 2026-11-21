import unittest


class TestLegacy(unittest.TestCase):
    def test_addition(self):
        self.assertEqual(1 + 1, 2)

    def test_failure(self):
        self.assertEqual(1, 2)
