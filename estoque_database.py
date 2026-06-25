import sqlite3
import os

DB_ESTOQUE_NAME = "smell_estoque.db"

def get_estoque_db_connection():
    conn = sqlite3.connect(DB_ESTOQUE_NAME)
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
        FOREIGN KEY (categoria_id) REFERENCES categorias (id)
    );
    """)

    # Nova tabela obrigatória para gerar os Relatórios de Histórico de Consumo e Vendas
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS historico_estoque (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        produto_codigo INTEGER NOT NULL,
        tipo TEXT NOT NULL CHECK (tipo IN ('Venda', 'Entrada', 'Saída', 'Ajuste')),
        quantidade_movimentada INTEGER NOT NULL,
        quantidade_saldo INTEGER NOT NULL,
        data_hora TEXT DEFAULT (datetime('now', 'localtime')),
        observacoes TEXT,
        FOREIGN KEY (produto_codigo) REFERENCES produtos (codigo)
    );
    """)

    conn.commit()
    conn.close()
    print(f"Banco de dados de estoque '{DB_ESTOQUE_NAME}' inicializado com sucesso.")

if __name__ == "__main__":
    init_estoque_db()