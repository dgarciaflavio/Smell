import sqlite3
import os

DB_NAME = "smell_clinic_spa.db"

def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS clientes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL,
        cpf TEXT UNIQUE NOT NULL,
        telefone TEXT NOT NULL,
        data_nascimento TEXT NOT NULL,
        instagram TEXT,
        profissao TEXT,
        data_cadastro TEXT DEFAULT (datetime('now', 'localtime'))
    );
    """)

    try:
        cursor.execute("ALTER TABLE clientes ADD COLUMN instagram TEXT;")
    except sqlite3.OperationalError:
        pass
        
    try:
        cursor.execute("ALTER TABLE clientes ADD COLUMN profissao TEXT;")
    except sqlite3.OperationalError:
        pass

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS profissionais (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL,
        especialidade TEXT,
        status TEXT DEFAULT 'Ativo' CHECK (status IN ('Ativo', 'Inativo'))
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS servicos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL,
        duracao_minutos INTEGER NOT NULL,
        preco_padrao REAL NOT NULL
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS agendamentos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cliente_id INTEGER NOT NULL,
        profissional_id INTEGER NOT NULL,
        servico_id INTEGER NOT NULL,
        data_hora_inicio TEXT NOT NULL,
        data_hora_fim_previsto TEXT NOT NULL,
        status TEXT DEFAULT 'Agendado' CHECK (
            status IN ('Agendado', 'Confirmado', 'Presente', 'Em Atendimento', 'Concluído', 'Cancelado', 'Atrasado')
        ),
        observacoes TEXT,
        lembrete_48h_enviado INTEGER DEFAULT 0,
        lembrete_2h_enviado INTEGER DEFAULT 0,
        avaliacao_enviada INTEGER DEFAULT 0,
        FOREIGN KEY (cliente_id) REFERENCES clientes (id),
        FOREIGN KEY (profissional_id) REFERENCES profissionais (id),
        FOREIGN KEY (servico_id) REFERENCES servicos (id)
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS anamneses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cliente_id INTEGER NOT NULL,
        profissional_nome TEXT NOT NULL,
        tipo TEXT NOT NULL,
        dados_json TEXT NOT NULL,
        termo_assinado TEXT NOT NULL,
        assinatura_base64 TEXT NOT NULL,
        assinatura_profissional_base64 TEXT,
        assinatura_testemunha_base64 TEXT,
        data_preenchimento TEXT DEFAULT (datetime('now', 'localtime')),
        FOREIGN KEY (cliente_id) REFERENCES clientes (id)
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS indicacoes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cliente_id INTEGER NOT NULL,
        profissional_nome TEXT NOT NULL,
        profissional_especialidade TEXT,
        observacoes_internas TEXT,
        indicacoes_cliente TEXT,
        data_registro TEXT DEFAULT (datetime('now', 'localtime')),
        FOREIGN KEY (cliente_id) REFERENCES clientes (id)
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS evolucao_fotos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cliente_id INTEGER NOT NULL,
        agendamento_id INTEGER,
        caminho_arquivo TEXT NOT NULL,
        data_hora_foto TEXT DEFAULT (datetime('now', 'localtime')),
        observacoes TEXT,
        FOREIGN KEY (cliente_id) REFERENCES clientes (id),
        FOREIGN KEY (agendamento_id) REFERENCES agendamentos (id)
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS fluxo_caixa (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        agendamento_id INTEGER,
        tipo TEXT NOT NULL CHECK (tipo IN ('Entrada', 'Saída')),
        valor REAL NOT NULL,
        forma_pagamento TEXT CHECK (forma_pagamento IN ('Pix', 'Cartão de Crédito', 'Cartão de Débito', 'Dinheiro', 'Transferência Bancária')),
        data_hora_lancamento TEXT DEFAULT (datetime('now', 'localtime')),
        observacoes TEXT,
        FOREIGN KEY (agendamento_id) REFERENCES agendamentos (id)
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS fila_whatsapp (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        numero_destino TEXT NOT NULL,
        mensagem TEXT NOT NULL,
        status TEXT DEFAULT 'Pendente' CHECK (status IN ('Pendente', 'Enviado', 'Erro')),
        data_tentativa TEXT
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS configuracoes_clinica (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        hora_abertura INTEGER DEFAULT 8,
        hora_fechamento INTEGER DEFAULT 20,
        pasta_backup TEXT,
        ultimo_backup_data TEXT
    );
    """)

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS pacotes_combos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT,
        tipo TEXT,
        servicos_ids TEXT,
        observacoes TEXT,
        valor_base REAL,
        porcentagem_desconto REAL,
        valor_final REAL,
        ativo INTEGER DEFAULT 1
    );
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS anamneses_termos (
        id INTEGER PRIMARY KEY AUTOINCREMENT, 
        cliente_id INTEGER, 
        token_temporario TEXT, 
        data_expiracao_token TEXT, 
        origem_preenchimento TEXT
    );
    ''')

    # NOVAS TABELAS: VITRINE/PDV E GESTÃO FINANCEIRA
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS vendas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cliente_id INTEGER,
        vendedor_id INTEGER,
        valor_total REAL NOT NULL,
        forma_pagamento TEXT,
        data_hora TEXT DEFAULT (datetime('now', 'localtime'))
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS vendas_itens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        venda_id INTEGER NOT NULL,
        produto_codigo INTEGER NOT NULL,
        descricao TEXT NOT NULL,
        quantidade INTEGER NOT NULL,
        valor_unitario REAL NOT NULL,
        total_item REAL NOT NULL,
        FOREIGN KEY (venda_id) REFERENCES vendas (id)
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS contas_pagar_receber (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tipo TEXT NOT NULL CHECK (tipo IN ('Pagar', 'Receber')),
        descricao TEXT NOT NULL,
        valor REAL NOT NULL,
        data_vencimento TEXT NOT NULL,
        data_pagamento TEXT,
        status TEXT DEFAULT 'Pendente' CHECK (status IN ('Pendente', 'Pago', 'Recebido')),
        forma_pagamento TEXT
    );
    """)

    try:
        cursor.execute("ALTER TABLE configuracoes_clinica ADD COLUMN pasta_backup TEXT;")
    except sqlite3.OperationalError:
        pass

    try:
        cursor.execute("ALTER TABLE configuracoes_clinica ADD COLUMN ultimo_backup_data TEXT;")
    except sqlite3.OperationalError:
        pass
    
    count = cursor.execute("SELECT COUNT(*) FROM configuracoes_clinica").fetchone()[0]
    if count == 0:
        cursor.execute("INSERT INTO configuracoes_clinica (hora_abertura, hora_fechamento) VALUES (8, 20)")

    conn.commit()
    conn.close()
    print(f"Banco de dados '{DB_NAME}' inicializado com sucesso.")

if __name__ == "__main__":
    init_db()