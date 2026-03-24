# utils.py
import unicodedata
import re
import difflib
from typing import List


def normalize(text: str) -> str:
    """
    Normaliza texto removendo acentos, deixando minúsculo
    e facilitando comparações.
    """
    if not text:
        return ""
    text = text.lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text


def contains_term(text: str, term: str, use_fuzzy: bool = False) -> bool:
    """
    Verifica se TODAS as palavras do termo aparecem no texto,
    mesmo separadas e em qualquer ordem.

    Exemplo:
    termo = "cupom amazon"
    texto = "resgate o cupom do anúncio... link da amazon"
    → True
    """

    text_norm = normalize(text)
    term_norm = normalize(term)

    # Divide o termo em palavras
    palavras = term_norm.split()

    # Verifica se TODAS as palavras aparecem no texto
    if all(p in text_norm for p in palavras):
        return True

    # Regex opcional (para permitir variações maiores)
    try:
        pattern = r".*".join(map(re.escape, palavras))
        if re.search(pattern, text_norm):
            return True
    except re.error:
        pass

    # Fuzzy opcional
    if use_fuzzy:
        ratio = difflib.SequenceMatcher(None, term_norm, text_norm).ratio()
        return ratio > 0.8

    return False

import re

def extract_price(text: str):
    """
    Detecta preços no formato brasileiro:
    R$ 129,90
    129,90
    129
    """
    pattern = r"(r\$ ?\d{1,4}(?:[\.,]\d{2})?)|\b\d{1,4}(?:[\.,]\d{2})\b"
    match = re.search(pattern, text.lower())
    if match:
        return match.group(0).replace(" ", "")
    return None

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