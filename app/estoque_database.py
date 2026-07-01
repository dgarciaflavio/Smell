import sqlite3
import os

DB_NAME = "smell_estoque.db"

def get_base_dir():
    import sys
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

def get_estoque_db_connection():
    db_path = os.path.join(get_base_dir(), DB_NAME)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.row_factory = sqlite3.Row
    return conn

def init_estoque_db():
    conn = get_estoque_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS categorias (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT UNIQUE NOT NULL
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS produtos (
        codigo INTEGER PRIMARY KEY,
        descricao TEXT NOT NULL,
        categoria_id INTEGER,
        valor_unitario REAL NOT NULL DEFAULT 0.0,
        quantidade INTEGER NOT NULL DEFAULT 0,
        status TEXT DEFAULT 'Ativo' CHECK (status IN ('Ativo', 'Inativo')),
        foto TEXT,
        FOREIGN KEY (categoria_id) REFERENCES categorias (id)
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS historico_estoque (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        produto_codigo INTEGER NOT NULL,
        tipo TEXT NOT NULL CHECK (tipo IN ('Entrada', 'Saída', 'Venda', 'Ajuste')),
        quantidade_movimentada INTEGER NOT NULL,
        quantidade_saldo INTEGER NOT NULL,
        observacoes TEXT,
        data_hora TEXT DEFAULT (datetime('now', 'localtime')),
        FOREIGN KEY (produto_codigo) REFERENCES produtos (codigo)
    );
    """)

    conn.commit()
    conn.close()
    print(f"Banco de dados de estoque '{DB_NAME}' inicializado com sucesso.")

if __name__ == "__main__":
    init_estoque_db()