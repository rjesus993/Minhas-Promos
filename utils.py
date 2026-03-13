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
    Verifica se o termo aparece no texto.
    Pode usar regex ou comparação aproximada (fuzzy).
    """
    text_norm = normalize(text)
    term_norm = normalize(term)

    # Busca direta com regex
    try:
        if re.search(re.escape(term_norm), text_norm):
            return True
    except re.error:
        pass

    # Busca aproximada
    if use_fuzzy:
        ratio = difflib.SequenceMatcher(None, term_norm, text_norm).ratio()
        return ratio > 0.8

    return False


def save_terms_to_db(terms: List[str]) -> bool:
    """
    Salva lista de termos no banco (Supabase).
    """
    try:
        from db import save_terms
        save_terms(terms)
        return True
    except Exception as e:
        print(f"Erro ao salvar termos no banco: {e}")
        return False


def load_terms_from_db() -> List[str]:
    """
    Carrega lista de termos do banco (Supabase).
    """
    try:
        from db import load_terms
        return load_terms()
    except Exception as e:
        print(f"Erro ao carregar termos do banco: {e}")
        return []