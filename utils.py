# utils.py
import difflib
import logging
import re
import unicodedata
from typing import List

logger = logging.getLogger(__name__)


def normalize(text: str) -> str:
    if not text:
        return ""
    text = text.lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text


# Termos curtos (tv, ps5, ssd) exigem palavra inteira; termos longos aceitam
# sufixo (fone -> fones, fone-de-ouvido) mas não infixo (fone != telefone).
_SHORT_TERM_MAX_LEN = 3


def _word_in_text(word: str, text_norm: str) -> bool:
    prefix = r"(?<!\w)" + re.escape(word)
    if len(word) <= _SHORT_TERM_MAX_LEN:
        prefix += r"(?!\w)"
    return re.search(prefix, text_norm) is not None


def contains_term(text: str, term: str, use_fuzzy: bool = False) -> bool:
    text_norm = normalize(text)
    term_norm = normalize(term)
    palavras = term_norm.split()

    # termo vazio não pode casar com tudo (all([]) é True)
    if not palavras:
        return False

    if all(_word_in_text(p, text_norm) for p in palavras):
        return True

    if use_fuzzy:
        ratio = difflib.SequenceMatcher(None, term_norm, text_norm).ratio()
        return ratio > 0.8

    return False


# Número em formato BR: 1.299,90 | 199,90 | 1299 | 49.90
_BRL = r"\d{1,3}(?:\.\d{3})+(?:,\d{2})?|\d+(?:[.,]\d{2})?"
_PRICE_POR_RE = re.compile(rf"\bpor\s+(?:apenas\s+|s[oó]\s+|somente\s+)?r\$\s*({_BRL})")
_PRICE_RS_RE = re.compile(rf"r\$\s*({_BRL})")
_PRICE_BARE_RE = re.compile(r"(?<![\d.,/])(\d{1,3}(?:\.\d{3})*,\d{2}|\d{1,4}\.\d{2})(?![\d/])")


def extract_price(text: str):
    """Extrai o preço da oferta.

    Prefere o valor depois de "por" ("de R$ 299 por R$ 199" -> r$199) e
    entende milhar com ponto ("R$ 1.299,90"). Formato de saída preservado:
    "r$199,90" para valores com R$ e o número puro para valores sem R$.
    """
    t = (text or "").lower()
    m = _PRICE_POR_RE.search(t)
    if m:
        return "r$" + m.group(1)
    m = _PRICE_RS_RE.search(t)
    if m:
        return "r$" + m.group(1)
    m = _PRICE_BARE_RE.search(t)
    if m:
        return m.group(1)
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
        logger.error("Erro ao salvar termos no banco: %s", e)
        return False


def load_terms_from_db() -> List[str]:
    try:
        from db import load_terms
        return load_terms()
    except Exception as e:
        logger.error("Erro ao carregar termos do banco: %s", e)
        return []