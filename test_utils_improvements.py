from utils import contains_term, extract_price, normalize


def test_termo_curto_exige_palavra_inteira():
    assert contains_term("TV 50 polegadas 4K", "tv")
    assert not contains_term("oferta outvalue hoje", "tv")
    assert not contains_term("novo console com ssdd", "ssd")


def test_infixo_nao_casa_mas_sufixo_sim():
    assert not contains_term("telefone celular barato", "fone")
    assert contains_term("fones bluetooth em oferta", "fone")
    assert contains_term("fone-de-ouvido jbl", "fone")


def test_acentos_e_varias_palavras():
    assert contains_term("Pré-treino Insanity 300g", "pre-treino insanity")
    assert contains_term("Camisa Polo Azul", "camisa azul")
    assert not contains_term("Camisa Polo Azul", "camisa verde")


def test_termo_vazio_nao_casa_com_tudo():
    assert not contains_term("qualquer coisa", "")
    assert not contains_term("qualquer coisa", "   ")


def test_preco_com_milhar():
    assert extract_price("iPhone por R$ 1.299,90 no pix") == "r$1.299,90"
    assert extract_price("Notebook R$ 4.599") == "r$4.599"
    assert extract_price("Fone R$ 199,90") == "r$199,90"


def test_preco_prefere_o_valor_depois_de_por():
    assert extract_price("De R$ 299,00 por R$ 199,00 hoje") == "r$199,00"
    assert extract_price("de R$ 299 por apenas R$ 149") == "r$149"


def test_preco_sem_rs_e_sem_preco():
    assert extract_price("so 49,90 hoje") == "49,90"
    assert extract_price("sem preco aqui") is None
    assert extract_price("") is None
    assert extract_price(None) is None
