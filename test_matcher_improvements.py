from matcher import smart_match, tokenize, get_word_variations, clear_term_cache


def m(msg, term):
    return smart_match(msg, term)["matched"]


def test_pontuacao_nao_quebra_match():
    assert tokenize("Camisa, azul! iphone-15") == ["camisa", "azul", "iphone", "15"]
    assert m("Camiseta, azul!", "camisa azul")


def test_plural_e_singular():
    assert "fones" in get_word_variations("fone")
    assert m("promocao fones bluetooth", "fone bluetooth")


def test_modelos_com_digitos_nao_casam_por_fuzzy():
    # ryzen5600 vs ryzen5700 tem ratio >= 88, mas é outro produto
    assert not m("ryzen5700 cooler", "ryzen5600 cooler")
    assert not m("galaxys24 capa", "galaxys23 capa")


def test_palavra_ausente_continua_bloqueando():
    assert not m("camisa Reserva", "camisa Renner")


def test_fuzzy_de_palavra_longa_ainda_funciona():
    assert m("notebooks gamer", "notebook gamer")


def test_msg_tokens_pre_calculados_dao_mesmo_resultado():
    msg = "Camiseta polo azul R$ 49,90"
    toks = tokenize(msg)
    for term in ("camisa polo", "camisa verde", "polo azul"):
        assert smart_match(msg, term) == smart_match(msg, term, msg_tokens=toks)


def test_clear_cache():
    smart_match("a b", "a b")
    clear_term_cache()
