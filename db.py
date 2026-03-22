from supabase import create_client
from typing import List
import os

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_URL ou SUPABASE_KEY não configurados!")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def save_terms(terms: List[str]) -> bool:
    try:
        supabase.table("terms").delete().neq("term", "").execute()
        for term in terms:
            supabase.table("terms").insert({"term": term}).execute()
        return True
    except Exception as e:
        print("Erro ao salvar termos:", e)
        return False

def load_terms() -> List[str]:
    try:
        data = supabase.table("terms").select("term").order("created_at").execute()
        return [row["term"] for row in data.data]
    except Exception as e:
        print("Erro ao carregar termos:", e)
        return []

def add_term(term: str) -> bool:
    try:
        supabase.table("terms").insert({"term": term}).execute()
        supabase.table("term_history").insert({"term": term, "action": "added"}).execute()
        return True
    except Exception as e:
        print("Erro ao adicionar termo:", e)
        return False

def remove_term(term: str) -> bool:
    try:
        supabase.table("terms").delete().eq("term", term).execute()
        supabase.table("term_history").insert({"term": term, "action": "removed"}).execute()
        return True
    except Exception as e:
        print("Erro ao remover termo:", e)
        return False

def get_term_history():
    try:
        data = supabase.table("term_history").select("*").order("timestamp", desc=True).limit(50).execute()
        return data.data
    except Exception as e:
        print("Erro ao obter histórico:", e)
        return []