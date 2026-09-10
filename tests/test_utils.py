import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import normalize, contains_term, extract_price, extract_product_name


class TestUtils(unittest.TestCase):
    def test_normalize_removes_accents_and_lowercase(self):
        self.assertEqual(normalize("Pré-Treino Whey!"), "pre-treino whey!")

    def test_contains_term_all_words(self):
        self.assertTrue(contains_term("whey protein bar", "whey protein"))

    def test_contains_term_regex_gap(self):
        self.assertTrue(contains_term("whey bar protein", "whey protein"))

    def test_contains_term_fuzzy_disabled_by_default(self):
        self.assertFalse(contains_term("whey proteina", "whey protein"))

    def test_extract_price_r_dollar_comma(self):
        self.assertEqual(extract_price("Whey R$ 99,90"), "99,90")

    def test_extract_price_plain_comma(self):
        self.assertEqual(extract_price("Kit 149,90"), "149,90")

    def test_extract_product_name_keyword(self):
        self.assertEqual(extract_product_name("whey protein 1kg\nR$ 99,90"), "whey protein 1kg")

    def test_extract_product_name_first_line_fallback(self):
        self.assertEqual(extract_product_name("Oferta especial\nR$ 49,90"), "Oferta especial")


if __name__ == "__main__":
    unittest.main()