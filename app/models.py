from app.database import get_db_connection
import datetime
import hashlib
import sqlite3

class Cliente:
    @staticmethod
    def cadastrar(nome, cpf, telefone, data_nascimento):
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO clientes (nome, cpf, telefone, data_nascimento) VALUES (?, ?, ?, ?)",
                (nome, cpf, telefone, data_nascimento)
            )
            conn.commit()
            return cursor.lastrowid
        except Exception:
            return None
        finally:
            conn.close()

    @staticmethod
    def buscar_por_cpf(cpf):
        conn = get_db_connection()
        row = conn.execute("SELECT * FROM clientes WHERE cpf = ?", (cpf,)).fetchone()
        conn.close()
        return row

    @staticmethod
    def obter_por_id(cliente_id):
        conn = get_db_connection()
        row = conn.execute("SELECT * FROM clientes WHERE id = ?", (cliente_id,)).fetchone()
        conn.close()
        return row


class Profissional:
    @staticmethod
    def listar_ativos():
        conn = get_db_connection()
        rows = conn.execute("SELECT * FROM profissionais WHERE status = 'Ativo'").fetchall()
        conn.close()
        return rows


class Servico:
    @staticmethod
    def obter_por_id(servico_id):
        conn = get_db_connection()
        row = conn.execute("SELECT * FROM servicos WHERE id = ?", (servico_id,)).fetchone()
        conn.close()
        return row


class Agendamento:
    @staticmethod
    def verificar_conflito_sala(data_hora_inicio, data_hora_fim_previsto):
        conn = get_db_connection()
        conflito = conn.execute("""
            SELECT id FROM agendamentos 
            WHERE status NOT IN ('Cancelado')
            AND (
                (data_hora_inicio <= ? AND data_hora_fim_previsto > ?) OR
                (data_hora_inicio < ? AND data_hora_fim_previsto >= ?) OR
                (? <= data_hora_inicio AND ? > data_hora_inicio)
            )
        """, (data_hora_inicio, data_hora_inicio, data_hora_fim_previsto, data_hora_fim_previsto, data_hora_inicio, data_hora_fim_previsto)).fetchone()
        conn.close()
        return conflito is not None

    @staticmethod
    def criar(cliente_id, profissional_id, servico_id, data_hora_inicio, observacoes=None):
        servico = Servico.obter_por_id(servico_id)
        if not servico: return None
        
        inicio = datetime.datetime.strptime(data_hora_inicio, "%Y-%m-%d %H:%M:%S")
        fim = inicio + datetime.timedelta(minutes=servico['duracao_minutos'])
        data_hora_fim_previsto = fim.strftime("%Y-%m-%d %H:%M:%S")

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO agendamentos (cliente_id, profissional_id, servico_id, data_hora_inicio, data_hora_fim_previsto, status, observacoes)
            VALUES (?, ?, ?, ?, ?, 'Agendado', ?)
        """, (cliente_id, profissional_id, servico_id, data_hora_inicio, data_hora_fim_previsto, observacoes))
        
        agendamento_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return agendamento_id

    @staticmethod
    def atualizar_status(agendamento_id, novo_status):
        conn = get_db_connection()
        conn.execute("UPDATE agendamentos SET status = ? WHERE id = ?", (novo_status, agendamento_id))
        conn.commit()
        conn.close()

    @staticmethod
    def mover_por_atraso(agendamento_id, nova_data_hora_inicio):
        conn = get_db_connection()
        agendamento = conn.execute("SELECT * FROM agendamentos WHERE id = ?", (agendamento_id,)).fetchone()
        servico = conn.execute("SELECT duracao_minutos FROM servicos WHERE id = ?", (agendamento['servico_id'],)).fetchone()
        
        inicio = datetime.datetime.strptime(nova_data_hora_inicio, "%Y-%m-%d %H:%M:%S")
        fim = inicio + datetime.timedelta(minutes=servico['duracao_minutos'])
        nova_data_hora_fim = fim.strftime("%Y-%m-%d %H:%M:%S")

        conn.execute("""
            UPDATE agendamentos 
            SET data_hora_inicio = ?, data_hora_fim_previsto = ?, status = 'Atrasado'
            WHERE id = ?
        """, (nova_data_hora_inicio, nova_data_hora_fim, agendamento_id))
        conn.commit()
        conn.close()


class AnamneseTermo:
    @staticmethod
    def gerar_token_temporario(cliente_id, token, minutos_validade=30):
        limite = datetime.datetime.now() + datetime.timedelta(minutes=minutos_validade)
        expiracao = limite.strftime("%Y-%m-%d %H:%M:%S")
        
        conn = get_db_connection()
        conn.execute("""
            INSERT INTO anamneses_termos (cliente_id, token_temporario, data_expiracao_token, origem_preenchimento)
            VALUES (?, ?, ?, 'Cliente')
        """, (cliente_id, token, expiracao))
        conn.commit()
        conn.close()

    @staticmethod
    def salvar_assinatura_eletronica(cliente_id, texto_termo, img1, img2, img3, origem, funcionario=None, ip=None):
        data_hora_atual = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        string_semente = f"{cliente_id}_{texto_termo}_{img1[:50]}_{img2[:50]}_{img3[:50]}_{data_hora_atual}"
        hash_sha256 = hashlib.sha256(string_semente.encode('utf-8')).hexdigest()

        conn = get_db_connection()
        conn.execute("""
            INSERT INTO anamneses_termos (
                cliente_id, texto_termo_aplicated, assinatura_img_1, assinatura_img_2, assinatura_img_3,
                hash_validacao, origem_preenchimento, usuario_funcionario, ip_dispositivo, data_hora_assinatura
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (cliente_id, texto_termo, img1, img2, img3, hash_sha256, origem, funcionario, ip, data_hora_atual))
        conn.commit()
        conn.close()
        return hash_sha256


class EvolucaoFoto:
    @staticmethod
    def vincular_foto(cliente_id, agendamento_id, caminho_relativo, observacoes=None):
        conn = get_db_connection()
        conn.execute("""
            INSERT INTO evolucao_fotos (cliente_id, agendamento_id, caminho_arquivo, observacoes)
            VALUES (?, ?, ?, ?)
        """, (cliente_id, agendamento_id, caminho_relativo, observacoes))
        conn.commit()
        conn.close()


class FluxoCaixa:
    @staticmethod
    def registrar_entrada(agendamento_id, valor, forma_pagamento, observacoes=None):
        conn = get_db_connection()
        conn.execute("""
            INSERT INTO fluxo_caixa (agendamento_id, tipo, valor, forma_pagamento, observacoes)
            VALUES (?, 'Entrada', ?, ?, ?)
        """, (agendamento_id, valor, forma_pagamento, observacoes))
        conn.commit()
        conn.close()


class FilaWhatsapp:
    @staticmethod
    def adicionar_a_fila(numero, mensagem):
        conn = get_db_connection()
        conn.execute("""
            INSERT INTO fila_whatsapp (numero_destino, mensagem, status)
            VALUES (?, ?, 'Pendente')
        """, (numero, mensagem))
        conn.commit()
        conn.close()


# --- NOVOS MODELOS: GESTÃO DE USUÁRIOS E COMISSÕES ---

class Usuario:
    @staticmethod
    def hash_senha(senha):
        return hashlib.sha256(senha.encode('utf-8')).hexdigest()

    @staticmethod
    def autenticar(cpf, senha):
        conn = get_db_connection()
        senha_criptografada = Usuario.hash_senha(senha)
        user = conn.execute("SELECT * FROM usuarios WHERE cpf = ? AND senha_hash = ? AND status = 'Ativo'", (cpf, senha_criptografada)).fetchone()
        conn.close()
        return user

    @staticmethod
    def criar_usuario(nome, cpf, senha, telefone, nivel_perfil, comissao_percentual=0.0):
        conn = get_db_connection()
        try:
            senha_criptografada = Usuario.hash_senha(senha)
            conn.execute("""
                INSERT INTO usuarios (nome, cpf, senha_hash, telefone, nivel_perfil, comissao_percentual, primeiro_acesso)
                VALUES (?, ?, ?, ?, ?, ?, 1)
            """, (nome, cpf, senha_criptografada, telefone, nivel_perfil, comissao_percentual))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
        finally:
            conn.close()

    @staticmethod
    def atualizar_senha(usuario_id, nova_senha):
        conn = get_db_connection()
        senha_criptografada = Usuario.hash_senha(nova_senha)
        conn.execute("UPDATE usuarios SET senha_hash = ?, primeiro_acesso = 0 WHERE id = ?", (senha_criptografada, usuario_id))
        conn.commit()
        conn.close()
        
    @staticmethod
    def contar_admins():
        conn = get_db_connection()
        qtd = conn.execute("SELECT COUNT(id) as total FROM usuarios WHERE nivel_perfil = 'Admin'").fetchone()['total']
        conn.close()
        return qtd


class Comissao:
    @staticmethod
    def gerar_hash_pagamento(usuario_id, valor_pago, percentual):
        data_hora = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        string_base = f"USR:{usuario_id}|VAL:{valor_pago}|PERC:{percentual}|DAT:{data_hora}"
        return hashlib.sha256(string_base.encode('utf-8')).hexdigest(), data_hora

    @staticmethod
    def registrar_pagamento(usuario_id, valor_total_vendas, percentual_aplicado):
        """Calcula o valor da comissão e registra o pagamento gerando o hash."""
        valor_pago = (valor_total_vendas * percentual_aplicado) / 100
        hash_assinatura, data_hora = Comissao.gerar_hash_pagamento(usuario_id, valor_pago, percentual_aplicado)
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO historico_comissoes (usuario_id, data_pagamento, valor_pago, percentual_aplicado, hash_assinatura)
            VALUES (?, ?, ?, ?, ?)
        """, (usuario_id, data_hora, valor_pago, percentual_aplicado, hash_assinatura))
        
        id_comissao = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return {
            "id": id_comissao,
            "valor_pago": valor_pago,
            "hash_assinatura": hash_assinatura,
            "data": data_hora
        }