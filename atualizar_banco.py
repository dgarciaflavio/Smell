import sqlite3
import os

def atualizar_banco_dados():
    # Aponte para o caminho correto do banco de dados do cliente
    db_path = os.path.join('app', 'dados.db') 
    
    if not os.path.exists(db_path):
        print("Banco de dados não encontrado. Certifique-se de que o caminho está correto.")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    novas_colunas_usuarios = [
        ("cpf", "TEXT UNIQUE"),
        ("primeiro_acesso", "BOOLEAN DEFAULT 1"),
        ("nivel_perfil", "TEXT DEFAULT 'vendedor'"),
        ("telefone", "TEXT")
    ]

    print("Iniciando atualização do banco de dados...")

    # Atualizando tabela de Usuários (Ignora erro se a coluna já existir)
    for coluna, tipo in novas_colunas_usuarios:
        try:
            cursor.execute(f"ALTER TABLE usuarios ADD COLUMN {coluna} {tipo}")
            print(f"Coluna '{coluna}' adicionada com sucesso na tabela usuarios.")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e).lower():
                print(f"Coluna '{coluna}' já existe. Ignorando.")
            else:
                print(f"Erro ao adicionar '{coluna}': {e}")

    # Criando tabela de Histórico de Comissões (para prever variação de % e assinaturas)
    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS historico_comissoes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id INTEGER,
                data_pagamento DATETIME DEFAULT CURRENT_TIMESTAMP,
                valor_pago REAL,
                percentual_aplicado REAL,
                hash_assinatura TEXT,
                FOREIGN KEY(usuario_id) REFERENCES usuarios(id)
            )
        ''')
        print("Tabela 'historico_comissoes' verificada/criada com sucesso.")
    except Exception as e:
        print(f"Erro ao criar tabela de comissões: {e}")

    conn.commit()
    conn.close()
    print("Atualização do banco concluída com sucesso! Nenhum dado foi perdido.")

if __name__ == "__main__":
    atualizar_banco_dados()