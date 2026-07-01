import sqlite3
import os

def atualizar_banco_dados():
    # Caminho corrigido para o banco de dados real utilizado pelo sistema
    db_path = 'smell_clinic_spa.db' 
    
    if not os.path.exists(db_path):
        print(f"Banco de dados '{db_path}' não encontrado no diretório atual.")
        print("Certifique-se de executar este script na mesma pasta do banco.")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    print("Iniciando atualização estrutural do banco de dados do cliente...")

    # 1. Garantir que a tabela usuarios base existe antes de fazer os ALTER TABLE
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS usuarios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL
    )''')

    # 2. Novas colunas mapeadas para a tabela de Usuários (Comissões e Senhas)
    novas_colunas_usuarios = [
        ("cpf", "TEXT UNIQUE"),
        ("senha_hash", "TEXT"),
        ("primeiro_acesso", "INTEGER DEFAULT 1"),
        ("nivel_perfil", "TEXT DEFAULT 'Vendedor'"),
        ("telefone", "TEXT"),
        ("comissao_percentual", "REAL DEFAULT 0.0"),
        ("status", "TEXT DEFAULT 'Ativo'")
    ]

    for coluna, tipo in novas_colunas_usuarios:
        try:
            cursor.execute(f"ALTER TABLE usuarios ADD COLUMN {coluna} {tipo}")
            print(f"Coluna '{coluna}' adicionada com sucesso na tabela usuarios.")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e).lower():
                print(f"Coluna '{coluna}' já existe em usuarios. Ignorando.")
            else:
                print(f"Erro ao adicionar '{coluna}': {e}")

    # 3. Atualizações na tabela de Anamneses (Novas Assinaturas)
    novas_colunas_anamneses = [
        ("assinatura_profissional_base64", "TEXT"),
        ("assinatura_testemunha_base64", "TEXT")
    ]
    
    for coluna, tipo in novas_colunas_anamneses:
        try:
            cursor.execute(f"ALTER TABLE anamneses ADD COLUMN {coluna} {tipo}")
            print(f"Coluna '{coluna}' adicionada com sucesso na tabela anamneses.")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e).lower():
                print(f"Coluna '{coluna}' já existe em anamneses. Ignorando.")

    # 4. Atualizações na tabela de Configurações (Rotinas de Backup)
    novas_colunas_config = [
        ("pasta_backup", "TEXT"),
        ("ultimo_backup_data", "TEXT")
    ]

    for coluna, tipo in novas_colunas_config:
        try:
            cursor.execute(f"ALTER TABLE configuracoes_clinica ADD COLUMN {coluna} {tipo}")
            print(f"Coluna '{coluna}' adicionada com sucesso na tabela configuracoes_clinica.")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e).lower():
                pass

    # 5. Atualizações na tabela de Clientes (Novos dados do Prontuário)
    novas_colunas_clientes = [
        ("instagram", "TEXT"),
        ("profissao", "TEXT")
    ]

    for coluna, tipo in novas_colunas_clientes:
        try:
            cursor.execute(f"ALTER TABLE clientes ADD COLUMN {coluna} {tipo}")
            print(f"Coluna '{coluna}' adicionada com sucesso na tabela clientes.")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e).lower():
                pass

    # 6. Atualizações na tabela de Agendamentos (Lembretes Automáticos)
    novas_colunas_agendamentos = [
        ("lembrete_48h_enviado", "INTEGER DEFAULT 0"),
        ("lembrete_2h_enviado", "INTEGER DEFAULT 0"),
        ("avaliacao_enviada", "INTEGER DEFAULT 0")
    ]

    for coluna, tipo in novas_colunas_agendamentos:
        try:
            cursor.execute(f"ALTER TABLE agendamentos ADD COLUMN {coluna} {tipo}")
            print(f"Coluna '{coluna}' adicionada com sucesso na tabela agendamentos.")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e).lower():
                pass

    # 7. Criando tabelas inteiramente novas necessárias para as novas funcionalidades
    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS historico_comissoes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id INTEGER NOT NULL,
                data_pagamento TEXT DEFAULT (datetime('now', 'localtime')),
                valor_pago REAL NOT NULL,
                percentual_aplicado REAL NOT NULL,
                hash_assinatura TEXT UNIQUE NOT NULL,
                status_pagamento TEXT DEFAULT 'Pago',
                FOREIGN KEY (usuario_id) REFERENCES usuarios (id)
            )
        ''')
        print("Tabela 'historico_comissoes' verificada/criada com sucesso.")
    except Exception as e:
        print(f"Erro ao criar tabela de comissões: {e}")

    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS fila_whatsapp_midia (
                id INTEGER PRIMARY KEY AUTOINCREMENT, 
                numero_destino TEXT, 
                caminho_arquivo TEXT, 
                legenda TEXT, 
                status TEXT, 
                data_criacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        print("Tabela 'fila_whatsapp_midia' verificada/criada com sucesso.")
    except Exception as e:
        print(f"Erro ao criar tabela fila_whatsapp_midia: {e}")

    conn.commit()
    conn.close()
    print("===============================================================")
    print("Atualização do banco concluída com sucesso! Nenhum dado foi perdido.")
    print("===============================================================")

if __name__ == "__main__":
    atualizar_banco_dados()