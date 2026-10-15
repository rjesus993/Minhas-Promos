# tests/test_matcher.py
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from matcher import smart_match, normalize_text, tokenize, clear_term_cache

TERMO = "camisa Reserva"


def _assert_match(msg, termo=TERMO):
    r = smart_match(msg, termo)
    assert r["matched"], f"Esperado MATCH: msg={msg!r} r={r}"
    return r


def _assert_no_match(msg, termo=TERMO):
    r = smart_match(msg, termo)
    assert not r["matched"], f"Esperado NO_MATCH: msg={msg!r} r={r}"
    return r


def setup_function(_):
    clear_term_cache()


# 1. Exato
def test_exact_match():
    _assert_match("Camisa Reserva masculina em promoção")


# 2. Variação camisa→camiseta
def test_variation_camisa_camiseta():
    r = _assert_match("Camiseta Reserva por R$ 89,90")
    assert r["match_type"] == "variation"
    assert r["score"] >= 90


# 3. Marca exata
def test_brand_exact():
    _assert_match("camiseta Reserva masculina R$ 99")


# 4. Marca diferente → NO MATCH
def test_brand_different_no_match():
    _assert_no_match("Camiseta Nike por R$ 89,90")


# 5. Pontuação / case
def test_punctuation_and_case():
    _assert_match("🔥 CAMISETA!!! RESERVA!!!")


# 6. Texto longo com vários produtos
def test_long_message():
    msg = (
        "🔥 OFERTAS DO DIA\n\n"
        "Nike Air Max R$ 299\n"
        "Samsung Galaxy R$ 999\n"
        "Camiseta Reserva R$ 89\n"
        "Tênis Adidas R$ 199\n"
    )
    _assert_match(msg)


# 7. Emojis e pontuação
def test_emoji_and_punct():
    _assert_match("💥💥 camiseta-reserva 💥💥")


# 8. Case insensitivity
def test_case_insensitive():
    _assert_match("CAMISA RESERVA")
    _assert_match("camisa reserva")
    _assert_match("Camisa Reserva")


# 9. Acentos
def test_accents():
    # termo com acento
    r = smart_match("tenis reserva em promocao", "tênis reserva")
    assert r["matched"], r


# 10. Palavras curtas — sem fuzzy perigoso
def test_short_words_no_fuzzy():
    # termo "tv", msg "tv" → exato
    assert smart_match("tv samsung", "tv").get("matched") is True
    # termo "tv", msg "ta" → NÃO deve casar fuzzy
    assert smart_match("ta samsung", "tv").get("matched") is False
    # termo "de", msg "da" → stopword, não deve casar como fuzzy
    assert smart_match("da samsung", "de").get("matched") is False


# 11. Falsos positivos
def test_false_positives():
    _assert_no_match("Camiseta Nike")
    _assert_no_match("Camisa Renner")
    _assert_no_match("Reserva de hotel")
    _assert_no_match("Promoção de televisão Samsung")


# 12. Múltiplos termos — cada termo é avaliado independente
def test_multiple_terms_isolated():
    a = smart_match("Camiseta Reserva por R$ 89", "camisa Reserva")
    b = smart_match("Camiseta Reserva por R$ 89", "camisa Nike")
    assert a["matched"] is True
    assert b["matched"] is False


# 13. Missing word rejeita (caso F)
def test_missing_word_rejects():
    _assert_no_match("camisa Renner")


# 14. Normalização
def test_normalize():
    assert normalize_text("CAMISA!!! RESERVA!!!") == normalize_text("camisa reserva")
    assert tokenize("camisa-reserva") == ["camisa", "reserva"]


# 15. Fuzzy conservador — erro de digitação pequeno
def test_fuzzy_typo():
    r = smart_match("camiseta reservaa", TERMO)  # 'reservaa' com typo
    # reservaa tem 8 chars, ratio com 'reserva' ~ 92 → deve passar fuzzy
    assert r["matched"] is True
    assert any(d["type"] in ("fuzzy", "exact", "variation") for d in r["details"])