# matcher.py
"""
Camada de Smart Matching para o bot.
Isolado do bot.py — não depende de Telethon, banco, etc.
Reutiliza utils.normalize() quando disponível.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from rapidfuzz import fuzz

try:
    from utils import normalize as _utils_normalize
except Exception:  # fallback mínimo — não substitui utils.normalize
    import unicodedata

    def _utils_normalize(text: str) -> str:
        if not text:
            return ""
        text = unicodedata.normalize("NFKD", text)
        text = "".join(c for c in text if not unicodedata.combining(c))
        return text.lower()


# ============================================================
# PESOS CONFIGURÁVEIS (valores conservadores iniciais)
# ============================================================
EXACT_WORD_SCORE     = 1.00   # palavra idêntica
VARIATION_WORD_SCORE = 0.90   # variação conhecida (ex: camisa→camiseta)
FUZZY_WORD_SCORE     = 0.75   # fuzzy alto, mas nunca equivalente a exato

MIN_SMART_MATCH_SCORE = 75    # limiar global (0-100)

# Fuzzy só entra em palavras com tamanho mínimo
FUZZY_MIN_WORD_LEN = 4
# Ratio mínimo do RapidFuzz para aceitar como match fuzzy
FUZZY_THRESHOLD    = 88

# ============================================================
# VARIAÇÕES CONTROLADAS
# ============================================================
# Estrutura facilmente editável. Mantida mínima por design.
WORD_VARIATIONS: Dict[str, set] = {
    "camisa":   {"camisa", "camiseta"},
    "camiseta": {"camiseta", "camisa"},
}

# Stopwords simples — não exigem correspondência
STOPWORDS = {
    "de", "da", "do", "das", "dos",
    "em", "no", "na", "nos", "nas",
    "para", "por", "com", "e", "o", "a", "os", "as",
    "um", "uma",
}

# ============================================================
# CACHE DE TOKENIZAÇÃO DE TERMOS (performance)
# ============================================================
_TERM_TOKENS_CACHE: Dict[str, List[str]] = {}


# ============================================================
# NORMALIZAÇÃO / TOKENIZAÇÃO
# ============================================================
def normalize_text(text: str) -> str:
    """Reutiliza utils.normalize — não cria implementação conflitante."""
    return _utils_normalize(text or "")


def tokenize(text: str) -> List[str]:
    """Quebra texto em tokens normalizados."""
    n = normalize_text(text)
    return [t for t in re.split(r"\s+", n) if t]


def _get_term_tokens(term: str) -> List[str]:
    cached = _TERM_TOKENS_CACHE.get(term)
    if cached is None:
        cached = tokenize(term)
        _TERM_TOKENS_CACHE[term] = cached
    return cached


def clear_term_cache() -> None:
    """Útil em testes / recarga de termos."""
    _TERM_TOKENS_CACHE.clear()


# ============================================================
# VARIAÇÕES
# ============================================================
def get_word_variations(word: str) -> set:
    word = (word or "").lower()
    if not word:
        return set()
    return set(WORD_VARIATIONS.get(word, {word}))


# ============================================================
# MATCH DE UMA PALAVRA
# ============================================================
def _word_match(term_word: str, msg_tokens: List[str]) -> Tuple[float, str, Optional[str]]:
    """
    Retorna (score, match_type, matched_token).
    match_type ∈ {"exact", "variation", "fuzzy", "missing", "stopword"}
    """
    tw = term_word.lower()

    if tw in STOPWORDS:
        return 0.0, "stopword", None

    # 1) exato
    for tok in msg_tokens:
        if tok == tw:
            return EXACT_WORD_SCORE, "exact", tok

    # 2) variação conhecida
    variations = get_word_variations(tw)
    if variations and variations != {tw}:
        for tok in msg_tokens:
            if tok in variations and tok != tw:
                return VARIATION_WORD_SCORE, "variation", tok

    # 3) fuzzy — conservador
    if len(tw) < FUZZY_MIN_WORD_LEN:
        return 0.0, "missing", None

    best_ratio = 0.0
    best_tok: Optional[str] = None
    for tok in msg_tokens:
        if tok == tw or tok in STOPWORDS:
            continue
        if len(tok) < FUZZY_MIN_WORD_LEN:
            continue
        # evita comparar números com palavras
        if tok.isdigit() or tw.isdigit():
            continue
        ratio = fuzz.ratio(tw, tok)
        if ratio >= FUZZY_THRESHOLD and ratio > best_ratio:
            best_ratio = ratio
            best_tok = tok

    if best_ratio > 0.0:
        # escalado: nunca alcança EXACT
        scaled = FUZZY_WORD_SCORE * (best_ratio / 100.0)
        return scaled, "fuzzy", best_tok

    return 0.0, "missing", None


# ============================================================
# SMART MATCH
# ============================================================
def smart_match(message_text: str, term: str, debug: bool = False) -> dict:
    """
    Compara um termo com a mensagem.
    Retorna dict:
        {
            "matched": bool,
            "score": int (0..100),
            "match_type": "exact"|"variation"|"fuzzy"|"no_match",
            "details": [ {...}, ... ]
        }
    """
    term_tokens = _get_term_tokens(term)
    msg_tokens  = tokenize(message_text)

    if not term_tokens or not msg_tokens:
        return {"matched": False, "score": 0, "match_type": "no_match", "details": []}

    # remove stopwords do termo — se sobrar vazio, mantém os originais
    significant = [t for t in term_tokens if t not in STOPWORDS] or term_tokens

    details: List[dict] = []
    total = 0.0
    max_total = 0.0
    has_strong = False   # teve exact ou variation?

    for tw in significant:
        score, mtype, mtok = _word_match(tw, msg_tokens)
        total += score
        max_total += EXACT_WORD_SCORE
        details.append({
            "term_word": tw,
            "msg_word": mtok,
            "type": mtype,
            "score": round(score, 3),
        })
        if mtype in ("exact", "variation"):
            has_strong = True

    if max_total == 0.0:
        return {"matched": False, "score": 0, "match_type": "no_match", "details": details}

    normalized_score = (total / max_total) * 100.0
    score_int = int(round(normalized_score))

    # --- Proteção contra falsos positivos ---
    # 1) precisa ter pelo menos 1 correspondência exata/variação
    if not has_strong:
        return {"matched": False, "score": score_int,
                "match_type": "no_match", "details": details}

    # 2) NENHUMA palavra significativa pode estar ausente
    #    (evita "camisa Renner" casar com "camisa Reserva")
    if any(d["type"] == "missing" for d in details):
        return {"matched": False, "score": score_int,
                "match_type": "no_match", "details": details}

    matched = score_int >= MIN_SMART_MATCH_SCORE

    if not matched:
        overall = "no_match"
    elif all(d["type"] == "exact" for d in details):
        overall = "exact"
    elif any(d["type"] == "fuzzy" for d in details):
        overall = "fuzzy"
    else:
        overall = "variation"

    return {
        "matched": matched,
        "score": score_int,
        "match_type": overall,
        "details": details,
    }


# ============================================================
# DEBUG FORMAT
# ============================================================
def format_debug(term: str, message: str, result: dict) -> str:
    lines = [
        "[SMART MATCH]",
        f"Termo: {term}",
        f"Mensagem: {message}",
        "",
    ]
    for d in result.get("details", []):
        tw = d["term_word"]
        mw = d["msg_word"] if d["msg_word"] else "—"
        t  = d["type"].upper()
        lines.append(f"{tw} -> {mw} = {t}")
    lines.append("")
    lines.append(f"score = {result.get('score', 0)}")
    lines.append(f"threshold = {MIN_SMART_MATCH_SCORE}")
    lines.append(f"resultado = {'MATCH' if result.get('matched') else 'NO MATCH'}")
    return "\n".join(lines)