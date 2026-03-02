# db.py
import sqlite3
import os
from typing import List

DB_FILE = "terms.db"

def init_db():
    """Inicializa o banco de dados"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Cria tabela de termos
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS terms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            term TEXT UNIQUE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Cria tabela de histórico
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS term_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            term TEXT NOT NULL,
            action TEXT NOT NULL,  -- 'added', 'removed'
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()

def save_terms(terms: List[str]) -> bool:
    """Salva lista de termos no banco"""
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        # Remove todos os termos atuais
        cursor.execute("DELETE FROM terms")
        
        # Insere novos termos
        for term in terms:
            cursor.execute("INSERT OR IGNORE INTO terms (term) VALUES (?)", (term,))
        
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Erro ao salvar termos: {e}")
        return False

def load_terms() -> List[str]:
    """Carrega lista de termos do banco"""
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        cursor.execute("SELECT term FROM terms ORDER BY created_at")
        rows = cursor.fetchall()
        conn.close()
        
        return [row[0] for row in rows]
    except Exception as e:
        print(f"Erro ao carregar termos: {e}")
        return []

def add_term(term: str) -> bool:
    """Adiciona um termo ao banco"""
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        cursor.execute("INSERT OR IGNORE INTO terms (term) VALUES (?)", (term,))
        cursor.execute("INSERT INTO term_history (term, action) VALUES (?, 'added')", (term,))
        
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Erro ao adicionar termo: {e}")
        return False

def remove_term(term: str) -> bool:
    """Remove um termo do banco"""
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        cursor.execute("DELETE FROM terms WHERE term = ?", (term,))
        cursor.execute("INSERT INTO term_history (term, action) VALUES (?, 'removed')", (term,))
        
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Erro ao remover termo: {e}")
        return False

def get_term_history() -> List[tuple]:
    """Obtém histórico de termos"""
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        cursor.execute("SELECT term, action, timestamp FROM term_history ORDER BY timestamp DESC LIMIT 50")
        rows = cursor.fetchall()
        conn.close()
        
        return rows
    except Exception as e:
        print(f"Erro ao obter histórico: {e}")
        return []