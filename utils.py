# utils.py
import unicodedata
import re
import difflib
from typing import List


def normalize(text: str) -> str:
    if not text:
        return ""
    text = text.lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text


def contains_term(text: str, term: str, use_fuzzy: bool = False) -> bool:
    text_norm = normalize(text)
    term_norm = normalize(term)
    palavras = term_norm.split()

    if all(p in text_norm for p in palavras):
        return True

    try:
        pattern = r".*".join(map(re.escape, palavras))
        if re.search(pattern, text_norm):
            return True
    except re.error:
        pass

    if use_fuzzy:
        ratio = difflib.SequenceMatcher(None, term_norm, text_norm).ratio()
        return ratio > 0.8

    return False


def extract_price(text: str):
    pattern = r"(r\$ ?\d{1,4}(?:[\.,]\d{2})?)|\b\d{1,4}(?:[\.,]\d{2})\b"
    match = re.search(pattern, text.lower())
    if match:
        return match.group(0).replace(" ", "")
    return None


def extract_product_name(text: str):
    linhas = text.split("\n")

    keywords = [
        "whey", "creatina", "kit", "pré-treino", "pre treino",
        "insanity", "growth", "protein", "suplemento", "combo",
        "bcaa", "glutamina", "albumina", "hipercalórico"
    ]

    for linha in linhas:
        ln = linha.lower()
        if any(k in ln for k in keywords):
            return linha.strip()

    for linha in linhas:
        if linha.strip():
            return linha.strip()

    return "Não identificado"


def save_terms_to_db(terms: List[str]) -> bool:
    try:
        from db import save_terms
        save_terms(terms)
        return True
    except Exception as e:
        print(f"Erro ao salvar termos no banco: {e}")
        return False


def load_terms_from_db() -> List[str]:
    try:
        from db import load_terms
        return load_terms()
    except Exception as e:
        print(f"Erro ao carregar termos do banco: {e}")
        return []