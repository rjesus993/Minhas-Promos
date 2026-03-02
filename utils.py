# utils.py
import unicodedata
import re
import difflib
from typing import List, Optional

def normalize(text: str) -> str:
    """Normaliza texto para comparação"""
    if not text:
        return ""
    text = text.lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join([c for c in text if not unicodedata.combining(c)])
    return text

def contains_term(text: str, term: str, use_fuzzy: bool = False) -> bool:
    """Verifica se o termo está contido no texto"""
    text_norm = normalize(text)
    term_norm = normalize(term)
    
    # Busca por regex
    try:
        if re.search(re.escape(term_norm), text_norm):
            return True
    except re.error:
        pass
    
    # Busca aproximada com difflib
    if use_fuzzy:
        ratio = difflib.SequenceMatcher(None, term_norm, text_norm).ratio()
        return ratio > 0.8
    
    return False

def save_terms_to_db(terms: List[str]) -> bool:
    """Salva termos no banco de dados"""
    try:
        from db import save_terms
        save_terms(terms)
        return True
    except Exception as e:
        print(f"Erro ao salvar termos no banco: {e}")
        return False

def load_terms_from_db() -> List[str]:
    """Carrega termos do banco de dados"""
    try:
        from db import load_terms
        return load_terms()
    except Exception as e:
        print(f"Erro ao carregar termos do banco: {e}")
        return []