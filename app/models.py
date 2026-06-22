from app.database import get_db_connection
import datetime
import hashlib

class Cliente:
    @staticmethod
    def cadastrar(nome, cpf, telefone, data_nascimento):
        """Cadastra um novo cliente no sistema local."""
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
        """Busca um cliente específico usando o CPF para confirmação de presença."""
        conn = get_db_connection()
        row = conn.execute("SELECT * FROM clientes WHERE cpf = ?", (cpf,)).fetchone()
        conn.close()
        return row

    @staticmethod
    def obter_por_id(cliente_id):
        """Retorna os dados cadastrais de um cliente pelo ID."""
        conn = get_db_connection()
        row = conn.execute("SELECT * FROM clientes WHERE id = ?", (cliente_id,)).fetchone()
        conn.close()
        return row


class Profissional:
    @staticmethod
    def listar_ativos():
        """Retorna todos os profissionais ativos para montar as colunas da agenda."""
        conn = get_db_connection()
        rows = conn.execute("SELECT * FROM profissionais WHERE status = 'Ativo'").fetchall()
        conn.close()
        return rows


class Servico:
    @staticmethod
    def obter_por_id(servico_id):
        """Retorna os dados de um serviço, incluindo tempo de duração e preço padrão."""
        conn = get_db_connection()
        row = conn.execute("SELECT * FROM servicos WHERE id = ?", (servico_id,)).fetchone()
        conn.close()
        return row


class Agendamento:
    @staticmethod
    def verificar_conflito_sala(data_hora_inicio, data_hora_fim_previsto):
        """
        Regra da Sala Única: Verifica se já existe qualquer atendimento em andamento
        no período solicitado, independentemente do profissional.
        """
        conn = get_db_connection()
        # Procura por sobreposição de horários
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
        """Gera um agendamento calculando automaticamente o horário de término."""
        servico = Servico.get_by_id(servico_id)
        if not ...: pass # Proteção contra serviço inexistente
        
        # Converte string para datetime para calcular a duração padrão do serviço
        inicio = datetime.datetime.strptime(data_hora_inicio, "%Y-%m-%d %H:%M:%S")
        fim = inicio + datetime.timedelta(minutes=servico['duracao_minutos'])
        data_hora_fim_previsto = fim.strftime("%Y-%m-%d %H:%M:%S")

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO agendamentos (cliente_id, profesional_id, servico_id, data_hora_inicio, data_hora_fim_previsto, status, observacoes)
            VALUES (?, ?, ?, ?, ?, 'Agendado', ?)
        """, (cliente_id, profissional_id, servico_id, data_hora_inicio, data_hora_fim_previsto, observacoes))
        
        agendamento_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return agendamento_id

    @staticmethod
    def atualizar_status(agendamento_id, novo_status):
        """Atualiza o estado do atendimento (Ex: Confirmado, Presente, Em Atendimento, Concluído)."""
        conn = get_db_connection()
        conn.execute("UPDATE agendamentos SET status = ? WHERE id = ?", (novo_status, agendamento_id))
        conn.commit()
        conn.close()

    @staticmethod
    def mover_por_atraso(agendamento_id, nova_data_hora_inicio):
        """Movimentação Unilateral: Desloca apenas o cliente atrasado na linha do tempo."""
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
        """Cria o registro inicial do token temporário com prazo de expiração para o WhatsApp."""
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
        """
        Salva as 3 rubricas e gera o Hash SHA-256 combinando os dados primários do
        termo para conferência de segurança jurídica antiviolação.
        """
        data_hora_atual = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Construção da semente do Hash com dados do termo e das assinaturas
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
        """Registra a referência do arquivo de imagem salvo no diretório local smell_fotos."""
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
        """Registra a baixa financeira do atendimento diretamente no fluxo de caixa diário."""
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
        """Enfileira uma mensagem automática para disparo em background pelo navegador fantasma."""
        conn = get_db_connection()
        conn.execute("""
            INSERT INTO fila_whatsapp (numero_destino, mensagem, status)
            VALUES (?, ?, 'Pendente')
        """, (numero, mensagem))
        conn.commit()
        conn.close()