from flask import Blueprint, render_template, request, jsonify, current_app, send_from_directory, send_file, render_template_string
from app.database import get_db_connection
from app.estoque_database import get_estoque_db_connection
import os
import shutil
import datetime
import sys
import sqlite3
import json
import hashlib
import re
import threading
import time
import random
import pandas as pd
import io
import subprocess
import secrets

main_bp = Blueprint('main', __name__)

# Variável para rastrear o processo do bot do WhatsApp
bot_process = None

# Variável global para rastrear a barra de progresso do backup
status_backup_global = {"em_andamento": False, "mensagem": "", "progresso": 0}

# Controle de rotinas em background para não duplicar no Flask
BACKGROUND_TASKS_STARTED = False

def get_db_absoluto(root_path):
    """Conexão direta para ser usada pelas threads em background (evita erros de contexto do Flask)"""
    db_path = os.path.join(root_path, '..', 'smell_clinic_spa.db')
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def garantir_colunas_notificacao(root_path):
    """Garante que as colunas de controle de notificação existam no banco de dados"""
    conn = get_db_absoluto(root_path)
    try:
        conn.execute("ALTER TABLE agendamentos ADD COLUMN lembrete_48h_enviado INTEGER DEFAULT 0")
    except sqlite3.OperationalError: pass
    try:
        conn.execute("ALTER TABLE agendamentos ADD COLUMN lembrete_2h_enviado INTEGER DEFAULT 0")
    except sqlite3.OperationalError: pass
    try:
        conn.execute("ALTER TABLE agendamentos ADD COLUMN avaliacao_enviada INTEGER DEFAULT 0")
    except sqlite3.OperationalError: pass
    conn.commit()
    conn.close()

def processar_notificacoes_inteligentes(root_path):
    """Lógica principal de envio de lembretes e avaliações"""
    conn = get_db_absoluto(root_path)
    try:
        agora = datetime.datetime.now()
        
        # Pega horários de funcionamento
        config = conn.execute("SELECT hora_abertura, hora_fechamento FROM configuracoes_clinica LIMIT 1").fetchone()
        abertura = int(config['hora_abertura']) if config and config['hora_abertura'] else 8
        fechamento = int(config['hora_fechamento']) if config and config['hora_fechamento'] else 20
        
        # ==========================================================
        # 1 e 2: LEMBRETES DE AGENDAMENTOS FUTUROS (48h e 2h)
        # ==========================================================
        agendamentos_futuros = conn.execute("""
            SELECT a.id, a.data_hora_inicio, a.lembrete_48h_enviado, a.lembrete_2h_enviado, 
                   c.nome, c.telefone 
            FROM agendamentos a 
            JOIN clientes c ON a.cliente_id = c.id 
            WHERE a.status NOT IN ('Cancelado', 'Concluído')
        """).fetchall()
        
        for ag in agendamentos_futuros:
            try:
                dt_inicio = datetime.datetime.strptime(ag['data_hora_inicio'], "%Y-%m-%d %H:%M:%S")
                
                # Regra 1: Lembrete 48h (Enviado às 10h da manhã)
                if ag['lembrete_48h_enviado'] == 0:
                    diferenca_dias = (dt_inicio.date() - agora.date()).days
                    if diferenca_dias == 2 and agora.hour >= 10:
                        msg_48h = f"Olá, {ag['nome']}! Passando para lembrar do seu agendamento na Smell CLINIC | SPA em dois dias, {dt_inicio.strftime('%d/%m/%Y')} às {dt_inicio.strftime('%H:%M')}."
                        conn.execute("INSERT INTO fila_whatsapp (numero_destino, mensagem, status) VALUES (?, ?, 'Pendente')", (ag['telefone'], msg_48h))
                        conn.execute("UPDATE agendamentos SET lembrete_48h_enviado = 1 WHERE id = ?", (ag['id'],))
                        
                # Regra 2: Lembrete 2h (ou fechamento do dia anterior)
                if ag['lembrete_2h_enviado'] == 0:
                    hora_agendamento = dt_inicio.hour
                    
                    if (hora_agendamento - 2) <= abertura:
                        # Se for <= abertura, envia 1h antes de fechar no dia anterior
                        dt_alvo = datetime.datetime(dt_inicio.year, dt_inicio.month, dt_inicio.day, fechamento - 1, 0, 0) - datetime.timedelta(days=1)
                    else:
                        # Padrão: 2 horas antes no mesmo dia
                        dt_alvo = dt_inicio - datetime.timedelta(hours=2)
                        
                    if agora >= dt_alvo:
                        msg_2h = f"Olá, {ag['nome']}! Seu agendamento na Smell CLINIC | SPA é logo mais, às {dt_inicio.strftime('%H:%M')}. Estamos te esperando com muito carinho!"
                        conn.execute("INSERT INTO fila_whatsapp (numero_destino, mensagem, status) VALUES (?, ?, 'Pendente')", (ag['telefone'], msg_2h))
                        conn.execute("UPDATE agendamentos SET lembrete_2h_enviado = 1 WHERE id = ?", (ag['id'],))
            except Exception as e:
                print(f"Erro ao processar lembrete para ID {ag['id']}: {e}")

        # ==========================================================
        # 3. AVALIAÇÃO PÓS-ATENDIMENTO (Dia Seguinte)
        # ==========================================================
        ontem = agora.date() - datetime.timedelta(days=1)
        concluidos_ontem = conn.execute("""
            SELECT a.id, a.cliente_id, a.data_hora_inicio, c.nome, c.telefone 
            FROM agendamentos a 
            JOIN clientes c ON a.cliente_id = c.id 
            WHERE a.status = 'Concluído' 
            AND a.avaliacao_enviada = 0 
            AND date(a.data_hora_inicio) = ?
        """, (ontem.strftime('%Y-%m-%d'),)).fetchall()
        
        # Substitua este link curto caso você crie o seu no bit.ly
        link_avaliacao = "https://bit.ly/4gc6M3D" 
        
        for ag in concluidos_ontem:
            # Verifica se o cliente tem mais agendamentos futuros (Trata a regra do Combo/Sessão)
            futuros = conn.execute("""
                SELECT COUNT(id) as total 
                FROM agendamentos 
                WHERE cliente_id = ? AND data_hora_inicio > ? AND status != 'Cancelado'
            """, (ag['cliente_id'], ag['data_hora_inicio'])).fetchone()
            
            # Se for 0, significa que foi o último ou único atendimento
            if futuros['total'] == 0:
                msg_aval = f"Olá, {ag['nome']}! Agradecemos por escolher a Smell CLINIC | SPA. Sua opinião é muito importante para nós! Poderia avaliar nosso atendimento e deixar um comentário? É rapidinho: {link_avaliacao}"
                conn.execute("INSERT INTO fila_whatsapp (numero_destino, mensagem, status) VALUES (?, ?, 'Pendente')", (ag['telefone'], msg_aval))
                
            # De qualquer forma, marca a flag como enviada para não processar este ID amanhã novamente
            conn.execute("UPDATE agendamentos SET avaliacao_enviada = 1 WHERE id = ?", (ag['id'],))
            
        conn.commit()
    except Exception as e:
        print(f"Erro no motor inteligente de notificações: {e}")
    finally:
        conn.close()

def loop_notificacoes_background(root_path):
    """Loop infinito que roda em paralelo ao Flask sem travá-lo"""
    while True:
        processar_notificacoes_inteligentes(root_path)
        time.sleep(60) # Verifica as regras a cada 1 minuto

@main_bp.before_app_request
def iniciar_motores_background():
    """Inicia a thread inteligente no momento que a aplicação recebe a primeira requisição"""
    global BACKGROUND_TASKS_STARTED
    if not BACKGROUND_TASKS_STARTED:
        BACKGROUND_TASKS_STARTED = True
        root_path = current_app.root_path
        garantir_colunas_notificacao(root_path)
        
        # Cria a thread invisível de notificações
        thread_notif = threading.Thread(target=loop_notificacoes_background, args=(root_path,), daemon=True)
        thread_notif.start()
        print("[SISTEMA] Motor Inteligente de Lembretes e Avaliações Iniciado com Sucesso.")

def rotina_de_backup_fantasma(pasta_destino, root_path):
    global status_backup_global
    status_backup_global["em_andamento"] = True
    status_backup_global["mensagem"] = "Iniciando cópia de segurança..."
    status_backup_global["progresso"] = 10

    try:
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        pasta_backup_final = os.path.join(pasta_destino, f"Backup_Smell_{timestamp}")
        os.makedirs(pasta_backup_final, exist_ok=True)

        status_backup_global["progresso"] = 30
        status_backup_global["mensagem"] = "Salvando banco de dados..."
        time.sleep(1) 
        
        db_path = os.path.join(root_path, '..', 'smell_clinic_spa.db')
        if os.path.exists(db_path):
            shutil.copy2(db_path, pasta_backup_final)
            
        db_estoque_path = os.path.join(root_path, '..', 'smell_estoque.db')
        if os.path.exists(db_estoque_path):
            shutil.copy2(db_estoque_path, pasta_backup_final)

        status_backup_global["progresso"] = 60
        status_backup_global["mensagem"] = "Salvando evoluções e fotos..."
        fotos_path = os.path.join(root_path, '..', 'smell_fotos')
        if os.path.exists(fotos_path):
            shutil.copytree(fotos_path, os.path.join(pasta_backup_final, 'smell_fotos'))

        status_backup_global["progresso"] = 80
        status_backup_global["mensagem"] = "Aplicando retenção de 7 dias..."
        
        backups_existentes = []
        for item in os.listdir(pasta_destino):
            if item.startswith("Backup_Smell_"):
                caminho_completo = os.path.join(pasta_destino, item)
                if os.path.isdir(caminho_completo):
                    backups_existentes.append(caminho_completo)

        backups_existentes.sort()
        while len(backups_existentes) > 7:
            backup_antigo = backups_existentes.pop(0)
            shutil.rmtree(backup_antigo)

        db_absoluto = os.path.join(root_path, '..', 'smell_clinic_spa.db')
        conn = sqlite3.connect(db_absoluto)
        hoje = datetime.datetime.now().strftime("%Y-%m-%d")
        conn.execute("UPDATE configuracoes_clinica SET ultimo_backup_data = ? WHERE id = 1", (hoje,))
        conn.commit()
        conn.close()

        status_backup_global["progresso"] = 100
        status_backup_global["mensagem"] = "Backup Diário Concluído!"
    except Exception as e:
        status_backup_global["mensagem"] = f"Erro no Backup: {str(e)}"
    finally:
        time.sleep(3) 
        status_backup_global["em_andamento"] = False

def calcular_idade(data_nascimento_str):
    try:
        nasc = datetime.datetime.strptime(data_nascimento_str, '%Y-%m-%d')
        hoje = datetime.datetime.today()
        return hoje.year - nasc.year - ((hoje.month, hoje.day) < (nasc.month, nasc.day))
    except:
        return ""

def formatar_telefone(telefone_cru):
    if not telefone_cru:
        return ""
    numeros = re.sub(r'\D', '', telefone_cru)
    if len(numeros) == 8 or len(numeros) == 9: return '5521' + numeros  
    elif len(numeros) == 10 or len(numeros) == 11: return '55' + numeros    
    else: return numeros           

def formatar_data_br(data_str):
    if not data_str:
        return ""
    try:
        if len(data_str) >= 19:
            dt = datetime.datetime.strptime(data_str[:19], '%Y-%m-%d %H:%M:%S')
            return dt.strftime('%d/%m/%Y %H:%M')
        elif len(data_str) >= 10:
            dt = datetime.datetime.strptime(data_str[:10], '%Y-%m-%d')
            return dt.strftime('%d/%m/%Y')
        return data_str
    except Exception:
        return data_str

@main_bp.route('/api/sistema/status-backup', methods=['GET'])
def sistema_status_backup():
    conn = get_db_connection()
    config = conn.execute("SELECT pasta_backup, ultimo_backup_data FROM configuracoes_clinica LIMIT 1").fetchone()
    conn.close()
    
    hoje = datetime.datetime.now().strftime("%Y-%m-%d")
    precisa_backup = False
    pasta_configurada = False
    
    if config and config['pasta_backup']:
        pasta_configurada = True
        if config['ultimo_backup_data'] != hoje:
            precisa_backup = True
            
    return jsonify({
        "pasta_configurada": pasta_configurada,
        "precisa_backup": precisa_backup,
        "pasta_backup": config['pasta_backup'] if config else None
    })

@main_bp.route('/api/backup/iniciar-fantasma', methods=['POST'])
def iniciar_backup_fantasma():
    global status_backup_global
    if status_backup_global["em_andamento"]:
        return jsonify({"status": "já em andamento"})
        
    conn = get_db_connection()
    config = conn.execute("SELECT pasta_backup FROM configuracoes_clinica LIMIT 1").fetchone()
    conn.close()
    
    if config and config['pasta_backup'] and os.path.exists(config['pasta_backup']):
        thread = threading.Thread(target=rotina_de_backup_fantasma, args=(config['pasta_backup'], current_app.root_path))
        thread.start()
        return jsonify({"status": "iniciado"})
        
    return jsonify({"status": "erro", "mensagem": "Pasta de backup inválida ou não encontrada."})

@main_bp.route('/api/backup/progresso', methods=['GET'])
def progresso_backup():
    global status_backup_global
    return jsonify(status_backup_global)

@main_bp.route('/api/backup/salvar-pasta', methods=['POST'])
def salvar_pasta_backup():
    pasta = request.form.get('pasta_backup')
    conn = get_db_connection()
    conn.execute("UPDATE configuracoes_clinica SET pasta_backup = ? WHERE id = 1", (pasta,))
    conn.commit()
    conn.close()
    return jsonify({"mensagem": "Diretório de segurança salvo com sucesso!"})

@main_bp.route('/', endpoint='agenda')
def agenda():
    data_hoje = datetime.datetime.now().strftime('%Y-%m-%d')
    data_busca = request.args.get('data')
    if not data_busca: data_busca = data_hoje
    conn = get_db_connection()
    profissionais = conn.execute("SELECT * FROM profissionais WHERE status = 'Ativo'").fetchall()
    servicos_lista = conn.execute("SELECT * FROM servicos ORDER BY nome ASC").fetchall()
    config = conn.execute("SELECT * FROM configuracoes_clinica LIMIT 1").fetchone()
    abertura = config['hora_abertura'] if config else 8
    fechamento = config['hora_fechamento'] if config else 20
    
    try:
        agendamentos = conn.execute("""
            SELECT a.*, c.nome as cliente_nome, 
            COALESCE(s.nome, 'Procedimento/Pacote') as servico_nome, 
            COALESCE(s.duracao_minutos, 60) as duracao_minutos
            FROM agendamentos a 
            JOIN clientes c ON a.cliente_id = c.id 
            LEFT JOIN servicos s ON a.servico_id = s.id 
            WHERE a.data_hora_inicio LIKE ? AND a.status != 'Cancelado'
        """, (f"{data_busca}%",)).fetchall()
    except sqlite3.OperationalError:
        agendamentos = conn.execute("SELECT a.*, c.nome as cliente_nome, s.nome as servico_nome, s.duracao_minutos FROM agendamentos a JOIN clientes c ON a.cliente_id = c.id LEFT JOIN servicos s ON a.servico_id = s.id WHERE a.data_hora_inicio LIKE ? AND a.status != 'Cancelado'", (f"{data_busca}%",)).fetchall()

    agendamentos_json = [dict(row) for row in agendamentos]
    conn.close()
    return render_template('agenda.html', profissionais=profissionais, servicos=servicos_lista, data_hoje=data_busca, abertura=abertura, fechamento=fechamento, agendamentos=agendamentos_json)

@main_bp.route('/clientes', endpoint='clientes')
def clientes():
    cpf_busca = request.args.get('cpf', '').strip()
    conn = get_db_connection()
    if cpf_busca: clientes_lista = conn.execute("SELECT * FROM clientes WHERE cpf = ?", (cpf_busca,)).fetchall()
    else: clientes_lista = conn.execute("SELECT * FROM clientes ORDER BY id DESC").fetchall()
    conn.close()
    return render_template('clientes.html', clientes=clientes_lista, cpf_busca=cpf_busca)

@main_bp.route('/cliente/<int:cliente_id>/prontuario', endpoint='prontuario')
def prontuario(cliente_id):
    conn = get_db_connection()
    cliente = conn.execute("SELECT * FROM clientes WHERE id = ?", (cliente_id,)).fetchone()
    if not cliente:
        conn.close()
        return "Cliente não encontrado", 404
        
    cliente_dict = dict(cliente)
    cliente_dict['idade'] = calcular_idade(cliente_dict['data_nascimento'])
    
    profissionais = conn.execute("SELECT * FROM profissionais WHERE status = 'Ativo'").fetchall()
    
    anamneses_db = conn.execute("SELECT * FROM anamneses WHERE cliente_id = ? ORDER BY id DESC", (cliente_id,)).fetchall()
    anamneses_historico = []
    for a in anamneses_db:
        ad = dict(a)
        ad['data_preenchimento_fmt'] = formatar_data_br(ad['data_preenchimento'])
        anamneses_historico.append(ad)

    indicacoes_db = conn.execute("SELECT * FROM indicacoes WHERE cliente_id = ? ORDER BY id DESC", (cliente_id,)).fetchall()
    indicacoes_historico = []
    for ind in indicacoes_db:
        idict = dict(ind)
        idict['data_registro_fmt'] = formatar_data_br(idict.get('data_registro', ''))
        indicacoes_historico.append(idict)
        
    fotos_db = conn.execute("SELECT * FROM evolucao_fotos WHERE cliente_id = ? ORDER BY data_hora_foto DESC", (cliente_id,)).fetchall()
    fotos_historico = []
    for f in fotos_db:
        fdict = dict(f)
        fdict['data_hora_foto_fmt'] = formatar_data_br(fdict.get('data_hora_foto', ''))
        fotos_historico.append(fdict)
        
    conn.close()
    return render_template('prontuario.html', cliente=cliente_dict, anamneses=anamneses_historico, indicacoes=indicacoes_historico, profissionais=profissionais, fotos=fotos_historico)

@main_bp.route('/fotos/<path:filename>', endpoint='serve_fotos')
def serve_fotos(filename):
    fotos_dir = os.path.abspath(os.path.join(current_app.root_path, '..', 'smell_fotos'))
    return send_from_directory(fotos_dir, filename)

@main_bp.route('/api/evolucao/foto', methods=['POST'])
def salvar_foto():
    cliente_id = request.form.get('cliente_id')
    observacoes = request.form.get('observacoes', '')
    foto = request.files.get('foto')
    data_retroativa = request.form.get('data_retroativa')

    if not foto or foto.filename == '': return jsonify({"mensagem": "Nenhuma foto selecionada.", "erro": True})
    
    conn = get_db_connection()
    
    if data_retroativa:
        dt_str = data_retroativa
        if len(dt_str) == 10: dt_str += " 12:00:00"
        elif 'T' in dt_str: dt_str = dt_str.replace('T', ' ') + ':00'
        
        hoje = datetime.datetime.now()
        try:
            dt_obj = datetime.datetime.strptime(dt_str, '%Y-%m-%d %H:%M:%S')
            if dt_obj > hoje:
                conn.close()
                return jsonify({"mensagem": "ERRO: Não é permitido lançar datas futuras.", "erro": True})
            data_retroativa = dt_str
        except Exception:
            pass
            
    cliente = conn.execute("SELECT nome, cpf FROM clientes WHERE id = ?", (cliente_id,)).fetchone()
    if not cliente:
        conn.close()
        return jsonify({"mensagem": "Cliente não localizado no banco de dados.", "erro": True})
    
    cpf_limpo = re.sub(r'\D', '', cliente['cpf'])
    nome_limpo = re.sub(r'[\\/*?:"<>|]', '', cliente['nome'].strip()).replace(' ', '_')
    nome_pasta = f"{cpf_limpo}_{nome_limpo}"
    fotos_dir = os.path.abspath(os.path.join(current_app.root_path, '..', 'smell_fotos', nome_pasta))
    os.makedirs(fotos_dir, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    ext = foto.filename.split('.')[-1] if '.' in foto.filename else 'jpg'
    filename = f"evolucao_{timestamp}.{ext}"
    filepath = os.path.join(fotos_dir, filename)
    foto.save(filepath)
    db_path = f"{nome_pasta}/{filename}"
    
    if data_retroativa:
        conn.execute("INSERT INTO evolucao_fotos (cliente_id, caminho_arquivo, observacoes, data_hora_foto) VALUES (?, ?, ?, ?)", (cliente_id, db_path, observacoes, data_retroativa))
    else:
        conn.execute("INSERT INTO evolucao_fotos (cliente_id, caminho_arquivo, observacoes) VALUES (?, ?, ?)", (cliente_id, db_path, observacoes))
    
    conn.commit()
    conn.close()
    return jsonify({"mensagem": "Foto salva com sucesso na galeria do paciente!", "erro": False})

@main_bp.route('/api/evolucao/foto/excluir', methods=['POST'])
def excluir_foto():
    foto_id = request.form.get('foto_id')
    conn = get_db_connection()
    foto = conn.execute("SELECT * FROM evolucao_fotos WHERE id = ?", (foto_id,)).fetchone()
    if foto:
        fotos_dir = os.path.abspath(os.path.join(current_app.root_path, '..', 'smell_fotos'))
        caminho_completo = os.path.join(fotos_dir, foto['caminho_arquivo'].replace('/', os.sep))
        if os.path.exists(caminho_completo):
            try: os.remove(caminho_completo)
            except Exception as e: pass 
        conn.execute("DELETE FROM evolucao_fotos WHERE id = ?", (foto_id,))
        conn.commit()
        msg = "Foto e registro excluídos permanentemente!"
    else:
        msg = "Registro não encontrado no banco de dados."
    conn.close()
    return jsonify({"mensagem": msg})

@main_bp.route('/api/indicacao/salvar', methods=['POST'])
def salvar_indicacao():
    cliente_id = request.form.get('cliente_id')
    profissional_id = request.form.get('profissional_id')
    observacoes_internas = request.form.get('observacoes_internas')
    indicacoes_cliente = request.form.get('indicacoes_cliente')
    data_retroativa = request.form.get('data_retroativa')

    conn = get_db_connection()
    
    if data_retroativa:
        dt_str = data_retroativa
        if len(dt_str) == 10: dt_str += " 12:00:00"
        elif 'T' in dt_str: dt_str = dt_str.replace('T', ' ') + ':00'
        
        hoje = datetime.datetime.now()
        try:
            dt_obj = datetime.datetime.strptime(dt_str, '%Y-%m-%d %H:%M:%S')
            if dt_obj > hoje:
                conn.close()
                return jsonify({"mensagem": "ERRO: Não é permitido lançar datas futuras.", "erro": True})
            data_retroativa = dt_str
        except Exception:
            pass

    prof = conn.execute("SELECT nome, especialidade FROM profissionais WHERE id = ?", (profissional_id,)).fetchone()
    if prof:
        if data_retroativa:
            conn.execute("INSERT INTO indicacoes (cliente_id, profissional_nome, profissional_especialidade, observacoes_internas, indicacoes_cliente, data_registro) VALUES (?, ?, ?, ?, ?, ?)", (cliente_id, prof['nome'], prof['especialidade'], observacoes_internas, indicacoes_cliente, data_retroativa))
        else:
            conn.execute("INSERT INTO indicacoes (cliente_id, profissional_nome, profissional_especialidade, observacoes_internas, indicacoes_cliente) VALUES (?, ?, ?, ?, ?)", (cliente_id, prof['nome'], prof['especialidade'], observacoes_internas, indicacoes_cliente))
        conn.commit()
        msg = "Evolução e Indicações salvas com sucesso!"
    else:
        msg = "Profissional não encontrado."
    conn.close()
    return jsonify({"mensagem": msg})

@main_bp.route('/cliente/<int:cliente_id>/indicacao/<int:indicacao_id>/imprimir', endpoint='imprimir_indicacao')
def imprimir_indicacao(cliente_id, indicacao_id):
    conn = get_db_connection()
    cliente = conn.execute("SELECT * FROM clientes WHERE id = ?", (cliente_id,)).fetchone()
    indicacao = conn.execute("SELECT * FROM indicacoes WHERE id = ?", (indicacao_id,)).fetchone()
    conn.close()
    if not cliente or not indicacao: return "Documento não encontrado.", 404
    
    ind_dict = dict(indicacao)
    ind_dict['data_registro'] = formatar_data_br(ind_dict['data_registro'])
    
    return render_template('imprimir_indicacao.html', cliente=dict(cliente), indicacao=ind_dict)

@main_bp.route('/cliente/<int:cliente_id>/anamnese/facial', endpoint='anamnese_facial')
def anamnese_facial(cliente_id):
    conn = get_db_connection()
    cliente = conn.execute("SELECT * FROM clientes WHERE id = ?", (cliente_id,)).fetchone()
    profissionais = conn.execute("SELECT * FROM profissionais WHERE status = 'Ativo'").fetchall()
    conn.close()
    cliente_dict = dict(cliente)
    cliente_dict['idade'] = calcular_idade(cliente_dict['data_nascimento'])
    return render_template('anamnese_facial.html', cliente=cliente_dict, profissionais=profissionais, modo_cliente=False, modo_avaliacao=False)

@main_bp.route('/cliente/<int:cliente_id>/anamnese/corporal', endpoint='anamnese_corporal')
def anamnese_corporal(cliente_id):
    conn = get_db_connection()
    cliente = conn.execute("SELECT * FROM clientes WHERE id = ?", (cliente_id,)).fetchone()
    profissionais = conn.execute("SELECT * FROM profissionais WHERE status = 'Ativo'").fetchall()
    conn.close()
    cliente_dict = dict(cliente)
    cliente_dict['idade'] = calcular_idade(cliente_dict['data_nascimento'])
    return render_template('anamnese_corporal.html', cliente=cliente_dict, profissionais=profissionais, modo_cliente=False, modo_avaliacao=False)

@main_bp.route('/cliente/<int:cliente_id>/anamnese/pes', endpoint='anamnese_pes')
def anamnese_pes(cliente_id):
    conn = get_db_connection()
    cliente = conn.execute("SELECT * FROM clientes WHERE id = ?", (cliente_id,)).fetchone()
    profissionais = conn.execute("SELECT * FROM profissionais WHERE status = 'Ativo'").fetchall()
    conn.close()
    cliente_dict = dict(cliente)
    cliente_dict['idade'] = calcular_idade(cliente_dict['data_nascimento'])
    return render_template('anamnese_pes.html', cliente=cliente_dict, profissionais=profissionais, modo_cliente=False, modo_avaliacao=False)

@main_bp.route('/cliente/<int:cliente_id>/anamnese/facial/<int:anamnese_id>/avaliar', endpoint='avaliar_anamnese_facial')
def avaliar_anamnese_facial(cliente_id, anamnese_id):
    conn = get_db_connection()
    cliente = conn.execute("SELECT * FROM clientes WHERE id = ?", (cliente_id,)).fetchone()
    profissionais = conn.execute("SELECT * FROM profissionais WHERE status = 'Ativo'").fetchall()
    anamnese = conn.execute("SELECT * FROM anamneses WHERE id = ?", (anamnese_id,)).fetchone()
    conn.close()
    if not cliente or not anamnese: return "Documento não encontrado.", 404
    cliente_dict = dict(cliente)
    cliente_dict['idade'] = calcular_idade(cliente_dict['data_nascimento'])
    return render_template('anamnese_facial.html', cliente=cliente_dict, profissionais=profissionais, modo_cliente=False, modo_avaliacao=True, anamnese_obj=dict(anamnese))

@main_bp.route('/cliente/<int:cliente_id>/anamnese/corporal/<int:anamnese_id>/avaliar', endpoint='avaliar_anamnese_corporal')
def avaliar_anamnese_corporal(cliente_id, anamnese_id):
    conn = get_db_connection()
    cliente = conn.execute("SELECT * FROM clientes WHERE id = ?", (cliente_id,)).fetchone()
    profissionais = conn.execute("SELECT * FROM profissionais WHERE status = 'Ativo'").fetchall()
    anamnese = conn.execute("SELECT * FROM anamneses WHERE id = ?", (anamnese_id,)).fetchone()
    conn.close()
    if not cliente or not anamnese: return "Documento não encontrado.", 404
    cliente_dict = dict(cliente)
    cliente_dict['idade'] = calcular_idade(cliente_dict['data_nascimento'])
    return render_template('anamnese_corporal.html', cliente=cliente_dict, profissionais=profissionais, modo_cliente=False, modo_avaliacao=True, anamnese_obj=dict(anamnese))

@main_bp.route('/cliente/<int:cliente_id>/anamnese/pes/<int:anamnese_id>/avaliar', endpoint='avaliar_anamnese_pes')
def avaliar_anamnese_pes(cliente_id, anamnese_id):
    conn = get_db_connection()
    cliente = conn.execute("SELECT * FROM clientes WHERE id = ?", (cliente_id,)).fetchone()
    profissionais = conn.execute("SELECT * FROM profissionais WHERE status = 'Ativo'").fetchall()
    anamnese = conn.execute("SELECT * FROM anamneses WHERE id = ?", (anamnese_id,)).fetchone()
    conn.close()
    if not cliente or not anamnese: return "Documento não encontrado.", 404
    cliente_dict = dict(cliente)
    cliente_dict['idade'] = calcular_idade(cliente_dict['data_nascimento'])
    return render_template('anamnese_pes.html', cliente=cliente_dict, profissionais=profissionais, modo_cliente=False, modo_avaliacao=True, anamnese_obj=dict(anamnese))

@main_bp.route('/totem/cliente/<int:cliente_id>/facial', endpoint='totem_facial')
def totem_facial(cliente_id):
    conn = get_db_connection()
    cliente = conn.execute("SELECT * FROM clientes WHERE id = ?", (cliente_id,)).fetchone()
    profissionais = conn.execute("SELECT * FROM profissionais WHERE status = 'Ativo'").fetchall()
    conn.close()
    cliente_dict = dict(cliente)
    cliente_dict['idade'] = calcular_idade(cliente_dict['data_nascimento'])
    return render_template('anamnese_facial.html', cliente=cliente_dict, profissionais=profissionais, modo_cliente=True, modo_avaliacao=False)

@main_bp.route('/totem/cliente/<int:cliente_id>/corporal', endpoint='totem_corporal')
def totem_corporal(cliente_id):
    conn = get_db_connection()
    cliente = conn.execute("SELECT * FROM clientes WHERE id = ?", (cliente_id,)).fetchone()
    profissionais = conn.execute("SELECT * FROM profissionais WHERE status = 'Ativo'").fetchall()
    conn.close()
    cliente_dict = dict(cliente)
    cliente_dict['idade'] = calcular_idade(cliente_dict['data_nascimento'])
    return render_template('anamnese_corporal.html', cliente=cliente_dict, profissionais=profissionais, modo_cliente=True, modo_avaliacao=False)

@main_bp.route('/totem/cliente/<int:cliente_id>/pes', endpoint='totem_pes')
def totem_pes(cliente_id):
    conn = get_db_connection()
    cliente = conn.execute("SELECT * FROM clientes WHERE id = ?", (cliente_id,)).fetchone()
    profissionais = conn.execute("SELECT * FROM profissionais WHERE status = 'Ativo'").fetchall()
    conn.close()
    cliente_dict = dict(cliente)
    cliente_dict['idade'] = calcular_idade(cliente_dict['data_nascimento'])
    return render_template('anamnese_pes.html', cliente=cliente_dict, profissionais=profissionais, modo_cliente=True, modo_avaliacao=False)

@main_bp.route('/totem/sucesso', endpoint='totem_sucesso')
def totem_sucesso():
    return render_template('totem_sucesso.html', modo_cliente=True)

# ==============================================================
# NOVA ROTA DO TÚNEL REMOTO DE PREENCHIMENTO DE ANAMNESE
# ==============================================================
@main_bp.route('/remoto/token/<token>', endpoint='acesso_remoto')
def acesso_remoto(token):
    conn = get_db_connection()
    agora = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        registro = conn.execute("SELECT * FROM anamneses_termos WHERE token_temporario = ? AND data_expiracao_token > ?", (token, agora)).fetchone()
    except sqlite3.OperationalError:
        registro = None

    if not registro:
        conn.close()
        return render_template_string("""
            <html><head><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Link Expirado</title></head>
            <body style="font-family: sans-serif; text-align: center; padding: 40px; background: #f8d7da; color: #721c24;">
                <h2>⚠️ Link Expirado ou Inválido</h2>
                <p>Este link de acesso expirou por segurança ou já foi utilizado. Por favor, solicite um novo na recepção ou utilize o tablet da clínica.</p>
            </body></html>
        """)

    cliente = conn.execute("SELECT * FROM clientes WHERE id = ?", (registro['cliente_id'],)).fetchone()
    conn.close()

    # Menu de seleção direto do link do WhatsApp
    return render_template_string("""
        <html><head><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Ficha de Anamnese</title>
        <style>
            body { font-family: sans-serif; text-align: center; padding: 20px; background: #f4f4f4; }
            .container { background: white; padding: 30px; border-radius: 12px; max-width: 400px; margin: auto; box-shadow: 0 4px 10px rgba(0,0,0,0.1); }
            .btn { display: block; width: 100%; padding: 15px; margin: 15px 0; background: #077626; color: white; text-decoration: none; border-radius: 8px; font-size: 18px; font-weight: bold; border: none;}
            .btn:hover { background: #055a1d; }
        </style>
        </head><body>
            <div class="container">
                <img src="/static/img/LogoSmell.png" style="max-width: 150px; margin-bottom: 20px;" alt="Smell Clinic">
                <h2 style="color: #333;">Olá, {{ cliente['nome'] }}!</h2>
                <p style="color: #666; margin-bottom: 30px;">Selecione a ficha que deseja preencher hoje:</p>
                <a href="/totem/cliente/{{ cliente['id'] }}/facial?token={{ token }}" class="btn">💆‍♀️ Anamnese Facial</a>
                <a href="/totem/cliente/{{ cliente['id'] }}/corporal?token={{ token }}" class="btn">🧍‍♀️ Anamnese Corporal</a>
                <a href="/totem/cliente/{{ cliente['id'] }}/pes?token={{ token }}" class="btn">👣 Anamnese dos Pés</a>
            </div>
        </body></html>
    """, cliente=dict(cliente), token=token)

@main_bp.route('/api/anamnese/salvar', methods=['POST'])
def salvar_anamnese():
    cliente_id = request.form.get('cliente_id')
    profissional_nome = request.form.get('profissional_nome')
    tipo = request.form.get('tipo')
    dados_json = request.form.get('dados_json')
    termo_assinado = request.form.get('termo_assinado')
    
    # As três assinaturas agora são recebidas
    assinatura_cliente = request.form.get('assinatura_cliente_base64')
    assinatura_profissional = request.form.get('assinatura_profissional_base64')
    assinatura_testemunha = request.form.get('assinatura_testemunha_base64')
    
    data_retroativa = request.form.get('data_retroativa')
    token = request.form.get('token') # Usado no túnel remoto
    
    conn = get_db_connection()
    
    # Garante que as novas colunas existam no banco sem precisar apagar a tabela antiga
    try:
        conn.execute("ALTER TABLE anamneses ADD COLUMN assinatura_profissional_base64 TEXT")
    except sqlite3.OperationalError: pass
    try:
        conn.execute("ALTER TABLE anamneses ADD COLUMN assinatura_testemunha_base64 TEXT")
    except sqlite3.OperationalError: pass
    
    if data_retroativa:
        dt_str = data_retroativa
        if len(dt_str) == 10: dt_str += " 12:00:00"
        elif 'T' in dt_str: dt_str = dt_str.replace('T', ' ') + ':00'
        
        hoje = datetime.datetime.now()
        try:
            dt_obj = datetime.datetime.strptime(dt_str, '%Y-%m-%d %H:%M:%S')
            if dt_obj > hoje:
                conn.close()
                return jsonify({"mensagem": "ERRO: Não é permitido lançar datas futuras em fichas.", "erro": True})
            data_retroativa = dt_str
        except Exception:
            pass

    if data_retroativa:
        conn.execute("""
            INSERT INTO anamneses (cliente_id, profissional_nome, tipo, dados_json, termo_assinado, assinatura_base64, assinatura_profissional_base64, assinatura_testemunha_base64, data_preenchimento) 
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (cliente_id, profissional_nome, tipo, dados_json, termo_assinado, assinatura_cliente, assinatura_profissional, assinatura_testemunha, data_retroativa))
    else:
        conn.execute("""
            INSERT INTO anamneses (cliente_id, profissional_nome, tipo, dados_json, termo_assinado, assinatura_base64, assinatura_profissional_base64, assinatura_testemunha_base64) 
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (cliente_id, profissional_nome, tipo, dados_json, termo_assinado, assinatura_cliente, assinatura_profissional, assinatura_testemunha))
    
    # Se o cliente usou o link remoto, destrói o token imediatamente para segurança
    if token:
        try:
            conn.execute("UPDATE anamneses_termos SET data_expiracao_token = 'UTILIZADO' WHERE token_temporario = ?", (token,))
        except sqlite3.OperationalError: pass
        
    conn.commit()
    conn.close()
    return jsonify({"mensagem": f"Anamnese {tipo} salva com sucesso!", "erro": False})

@main_bp.route('/api/anamnese/atualizar', methods=['POST'])
def atualizar_anamnese():
    anamnese_id = request.form.get('anamnese_id')
    profissional_nome = request.form.get('profissional_nome')
    dados_json_novo = request.form.get('dados_json')
    conn = get_db_connection()
    conn.execute("UPDATE anamneses SET profissional_nome = ?, dados_json = ? WHERE id = ?", (profissional_nome, dados_json_novo, anamnese_id))
    conn.commit()
    conn.close()
    return jsonify({"mensagem": "Avaliação técnica anexada e prontuário atualizado com sucesso!"})

@main_bp.route('/cliente/<int:cliente_id>/anamnese/<int:anamnese_id>/visualizar', endpoint='visualizar_anamnese')
def visualizar_anamnese(cliente_id, anamnese_id):
    conn = get_db_connection()
    cliente = conn.execute("SELECT * FROM clientes WHERE id = ?", (cliente_id,)).fetchone()
    anamnese = conn.execute("SELECT * FROM anamneses WHERE id = ?", (anamnese_id,)).fetchone()
    conn.close()
    if not cliente or not anamnese: return "Documento não encontrado.", 404
    cliente_dict = dict(cliente)
    cliente_dict['idade'] = calcular_idade(cliente_dict['data_nascimento'])
    anamnese_dict = dict(anamnese)
    dados_crus = json.loads(anamnese_dict['dados_json'])
    dados_limpos = {}
    for chave, valor in dados_crus.items():
        chave_formatada = chave.replace('_', ' ').title()
        if isinstance(valor, list): dados_limpos[chave_formatada] = ", ".join(valor)
        else: dados_limpos[chave_formatada] = str(valor)
    texto_para_hash = f"SMELL_CLINIC|{anamnese_dict['id']}|{cliente_dict['cpf']}|{anamnese_dict['data_preenchimento']}|{anamnese_dict['dados_json']}"
    hash_seguranca = hashlib.sha256(texto_para_hash.encode('utf-8')).hexdigest().upper()
    return render_template('visualizar_anamnese.html', cliente=cliente_dict, anamnese=anamnese_dict, dados=dados_limpos, hash_seguranca=hash_seguranca)

@main_bp.route('/servicos', endpoint='servicos')
def servicos():
    conn = get_db_connection()
    servicos_lista = conn.execute("SELECT * FROM servicos ORDER BY nome ASC").fetchall()
    conn.close()
    return render_template('servicos.html', servicos=servicos_lista)

@main_bp.route('/financeiro', endpoint='financeiro')
def financeiro():
    hoje = datetime.datetime.now().strftime('%Y-%m-%d')
    conn = get_db_connection()
    movimentacoes = conn.execute("SELECT * FROM fluxo_caixa WHERE date(data_hora_lancamento) = ? ORDER BY data_hora_lancamento DESC", (hoje,)).fetchall()
    entradas = sum(m['valor'] for m in movimentacoes if m['tipo'] == 'Entrada')
    saidas = sum(m['valor'] for m in movimentacoes if m['tipo'] == 'Saída')
    saldo = entradas - saidas
    conn.close()

    conn_est = get_estoque_db_connection()
    produtos = conn_est.execute("SELECT codigo, descricao, valor_unitario, quantidade FROM produtos WHERE status = 'Ativo' AND quantidade > 0 ORDER BY descricao ASC").fetchall()
    produtos_json = [dict(p) for p in produtos]
    conn_est.close()

    return render_template('financeiro.html', movimentacoes=movimentacoes, entradas=entradas, saidas=saidas, saldo=saldo, produtos=produtos_json)

@main_bp.route('/financeiro/venda', methods=['POST'])
def nova_venda():
    codigo_produto = request.form.get('codigo_produto')
    quantidade = int(request.form.get('quantidade', 1))
    forma_pagamento = request.form.get('forma_pagamento')

    conn_est = get_estoque_db_connection()
    produto = conn_est.execute("SELECT descricao, valor_unitario, quantidade FROM produtos WHERE codigo = ?", (codigo_produto,)).fetchone()
    
    if not produto:
        conn_est.close()
        return jsonify({"mensagem": "Produto não encontrado.", "erro": True})
    
    if produto['quantidade'] < quantidade:
        conn_est.close()
        return jsonify({"mensagem": f"Estoque insuficiente! Saldo atual do item: {produto['quantidade']} unidade(s).", "erro": True})

    nova_qtde = produto['quantidade'] - quantidade
    conn_est.execute("UPDATE produtos SET quantidade = ? WHERE codigo = ?", (nova_qtde, codigo_produto))
    
    conn_est.execute("INSERT INTO historico_estoque (produto_codigo, tipo, quantidade_movimentada, quantidade_saldo, observacoes) VALUES (?, 'Venda', ?, ?, ?)", (codigo_produto, quantidade, nova_qtde, "Venda Direta / Caixa"))
    conn_est.commit()
    conn_est.close()

    valor_total = produto['valor_unitario'] * quantidade
    observacoes = f"Venda de Produto: {quantidade}x {produto['descricao']}"
    
    conn = get_db_connection()
    conn.execute("INSERT INTO fluxo_caixa (tipo, valor, forma_pagamento, observacoes) VALUES ('Entrada', ?, ?, ?)", (valor_total, forma_pagamento, observacoes))
    conn.commit()
    conn.close()

    return jsonify({"mensagem": "Venda realizada com sucesso! Estoque e caixa foram atualizados.", "erro": False})

@main_bp.route('/configuracoes', endpoint='configuracoes')
def configuracoes():
    conn = get_db_connection()
    profissionais = conn.execute("SELECT * FROM profissionais ORDER BY nome ASC").fetchall()
    config = conn.execute("SELECT * FROM configuracoes_clinica LIMIT 1").fetchone()
    conn.close()
    return render_template('configuracoes.html', profissionais=profissionais, config=config)

@main_bp.route('/configuracoes/horario', methods=['POST'])
def salvar_horario():
    abertura = request.form.get('hora_abertura')
    fechamento = request.form.get('hora_fechamento')
    conn = get_db_connection()
    conn.execute("UPDATE configuracoes_clinica SET hora_abertura = ?, hora_fechamento = ? WHERE id = 1", (abertura, fechamento))
    conn.commit()
    conn.close()
    return jsonify({"mensagem": "Horário de funcionamento atualizado com sucesso!"})

@main_bp.route('/agendamento/novo', methods=['POST'])
def novo_agendamento():
    cliente_input = request.form.get('cliente_nome_cpf', '').strip()
    profissional_id = request.form.get('profissional_id')
    servico_id = request.form.get('servico_id')
    
    datas_horas = request.form.getlist('data_hora_inicio[]')
    
    if not datas_horas:
        dt_unica = request.form.get('data_hora_inicio')
        if dt_unica:
            datas_horas = [dt_unica]
            
    if not datas_horas:
        return jsonify({"mensagem": "Nenhuma data de agendamento informada.", "erro": True})

    conn = get_db_connection()
    cliente = conn.execute("SELECT * FROM clientes WHERE cpf = ? OR nome LIKE ?", (cliente_input, f"%{cliente_input}%")).fetchone()
    
    if not cliente:
        conn.close()
        return jsonify({"mensagem": "Cliente não localizado. Cadastre o cliente primeiro!", "erro": True})
        
    servico = None
    duracao_minutos = 60 
    nome_procedimento = "Procedimento ou Sessão"
    
    if servico_id:
        servico = conn.execute("SELECT * FROM servicos WHERE id = ?", (servico_id,)).fetchone()
        if servico:
            duracao_minutos = servico['duracao_minutos']
            nome_procedimento = servico['nome']

    for dt_str in datas_horas:
        if 'T' in dt_str: dt_str = dt_str.replace('T', ' ') + ':00'
        
        inicio_dt = datetime.datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
        fim_dt = inicio_dt + datetime.timedelta(minutes=duracao_minutos)
        data_hora_fim = fim_dt.strftime("%Y-%m-%d %H:%M:%S")
        
        conflito = conn.execute("""
            SELECT c.nome as cliente_nome, COALESCE(s.nome, 'Procedimento') as servico_nome 
            FROM agendamentos a 
            LEFT JOIN clientes c ON a.cliente_id = c.id 
            LEFT JOIN servicos s ON a.servico_id = s.id 
            WHERE a.status NOT IN ('Cancelado') AND a.data_hora_inicio < ? AND a.data_hora_fim_previsto > ?
        """, (data_hora_fim, dt_str)).fetchone()
        
        if conflito:
            conn.close()
            return jsonify({"mensagem": f"⚠️ CONFLITO DE HORÁRIO!\nJá existe um agendamento para '{conflito['cliente_nome']}' na sala no dia e hora: {dt_str}.", "erro": True})

    for dt_str in datas_horas:
        if 'T' in dt_str: dt_str = dt_str.replace('T', ' ') + ':00'
        
        inicio_dt = datetime.datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
        fim_dt = inicio_dt + datetime.timedelta(minutes=duracao_minutos)
        data_hora_fim = fim_dt.strftime("%Y-%m-%d %H:%M:%S")
        
        conn.execute("""
            INSERT INTO agendamentos (cliente_id, profissional_id, servico_id, data_hora_inicio, data_hora_fim_previsto, status) 
            VALUES (?, ?, ?, ?, ?, 'Agendado')
        """, (cliente['id'], profissional_id, servico_id, dt_str, data_hora_fim))
        
        msg_agenda = f"Olá, {cliente['nome']}! Seu agendamento de {nome_procedimento} foi confirmado para o dia {inicio_dt.strftime('%d/%m/%Y')} às {inicio_dt.strftime('%H:%M')} na Smell CLINIC | SPA."
        conn.execute("INSERT INTO fila_whatsapp (numero_destino, mensagem) VALUES (?, ?)", (cliente['telefone'], msg_agenda))
        
    conn.commit()
    conn.close()
    
    texto_sucesso = "Horário salvo com sucesso!" if len(datas_horas) == 1 else f"{len(datas_horas)} horários salvos com sucesso (Combo/Pacote)!"
    return jsonify({"mensagem": f"{texto_sucesso} O lembrete já foi enviado ao WhatsApp do cliente.", "erro": False})

@main_bp.route('/agendamento/atualizar', methods=['POST'])
def atualizar_agendamento():
    ag_id = request.form.get('agendamento_id')
    novo_status = request.form.get('status')
    forma_pagamento = request.form.get('forma_pagamento')
    enviar_link = request.form.get('enviar_link_anamnese') == 'on'
    
    conn = get_db_connection()
    if novo_status == 'Concluído':
        agendamento = conn.execute("SELECT a.id, s.preco_padrao, s.nome FROM agendamentos a JOIN servicos s ON a.servico_id = s.id WHERE a.id = ?", (ag_id,)).fetchone()
        if agendamento:
            lancamento_existente = conn.execute("SELECT id FROM fluxo_caixa WHERE agendamento_id = ?", (ag_id,)).fetchone()
            if not lancamento_existente:
                pagamento_final = forma_pagamento if forma_pagamento else 'Pix'
                conn.execute("INSERT INTO fluxo_caixa (agendamento_id, tipo, valor, forma_pagamento, observacoes) VALUES (?, 'Entrada', ?, ?, ?)", (ag_id, agendamento['preco_padrao'], pagamento_final, f"Pagamento - Serviço: {agendamento['nome']}"))

    # LÓGICA DO TÚNEL DE ACESSO REMOTO PARA O CLIENTE
    if enviar_link:
        ag = conn.execute("SELECT a.*, c.nome as cliente_nome, c.telefone FROM agendamentos a JOIN clientes c ON a.cliente_id = c.id WHERE a.id = ?", (ag_id,)).fetchone()
        if ag:
            token = secrets.token_urlsafe(16)
            expiracao = (datetime.datetime.now() + datetime.timedelta(minutes=60)).strftime("%Y-%m-%d %H:%M:%S")
            try:
                conn.execute("INSERT INTO anamneses_termos (cliente_id, token_temporario, data_expiracao_token, origem_preenchimento) VALUES (?, ?, ?, 'Link Remoto')", (ag['cliente_id'], token, expiracao))
            except sqlite3.OperationalError:
                # Cria a tabela de apoio dinamicamente caso não exista
                conn.execute('''CREATE TABLE IF NOT EXISTS anamneses_termos (id INTEGER PRIMARY KEY AUTOINCREMENT, cliente_id INTEGER, token_temporario TEXT, data_expiracao_token TEXT, origem_preenchimento TEXT)''')
                conn.execute("INSERT INTO anamneses_termos (cliente_id, token_temporario, data_expiracao_token, origem_preenchimento) VALUES (?, ?, ?, 'Link Remoto')", (ag['cliente_id'], token, expiracao))

            link_remoto = f"{request.host_url}remoto/token/{token}"
            msg = f"Olá, {ag['cliente_nome']}! Para agilizar seu atendimento, por favor, preencha sua ficha de anamnese clicando no link a seguir (válido por 60 min): {link_remoto}"
            conn.execute("INSERT INTO fila_whatsapp (numero_destino, mensagem, status) VALUES (?, ?, 'Pendente')", (ag['telefone'], msg))

    conn.execute("UPDATE agendamentos SET status = ? WHERE id = ?", (novo_status, ag_id))
    conn.commit()
    conn.close()
    
    msg_retorno = f"Status atualizado para '{novo_status}'."
    if enviar_link:
        msg_retorno += " Link de acesso remoto enviado ao WhatsApp do cliente!"
        
    return jsonify({"mensagem": msg_retorno})

@main_bp.route('/agendamento/remarcar', methods=['POST'])
def remarcar_agendamento():
    ag_id = request.form.get('agendamento_id')
    nova_data = request.form.get('nova_data')
    novo_horario = request.form.get('novo_horario')
    
    if not ag_id or not nova_data or not novo_horario:
        return jsonify({"mensagem": "Dados incompletos para remarcação.", "erro": True})
    
    nova_data_hora = f"{nova_data} {novo_horario}:00"
    
    conn = get_db_connection()
    agendamento = conn.execute("SELECT * FROM agendamentos WHERE id = ?", (ag_id,)).fetchone()
    if not agendamento:
        conn.close()
        return jsonify({"mensagem": "Agendamento não encontrado no sistema.", "erro": True})
        
    cliente = conn.execute("SELECT * FROM clientes WHERE id = ?", (agendamento['cliente_id'],)).fetchone()
    
    if agendamento['servico_id']:
        servico = conn.execute("SELECT duracao_minutos, nome FROM servicos WHERE id = ?", (agendamento['servico_id'],)).fetchone()
        duracao = servico['duracao_minutos']
        nome_proc = servico['nome']
    else:
        inicio_original = datetime.datetime.strptime(agendamento['data_hora_inicio'], "%Y-%m-%d %H:%M:%S")
        fim_original = datetime.datetime.strptime(agendamento['data_hora_fim_previsto'], "%Y-%m-%d %H:%M:%S")
        duracao = int((fim_original - inicio_original).total_seconds() / 60)
        nome_proc = 'Procedimento'

    fim_dt = datetime.datetime.strptime(nova_data_hora, "%Y-%m-%d %H:%M:%S") + datetime.timedelta(minutes=duracao)
    nova_data_hora_fim = fim_dt.strftime("%Y-%m-%d %H:%M:%S")
    
    conflito = conn.execute("""
        SELECT c.nome as cliente_nome, COALESCE(s.nome, 'Procedimento') as servico_nome 
        FROM agendamentos a 
        LEFT JOIN clientes c ON a.cliente_id = c.id 
        LEFT JOIN servicos s ON a.servico_id = s.id 
        WHERE a.status NOT IN ('Cancelado') AND a.id != ? 
        AND a.data_hora_inicio < ? AND a.data_hora_fim_previsto > ?
    """, (ag_id, nova_data_hora_fim, nova_data_hora)).fetchone()
    
    if conflito:
        conn.close()
        return jsonify({"mensagem": f"⚠️ CONFLITO DE HORÁRIO!\nJá existe um agendamento para '{conflito['cliente_nome']}' neste período da sala.", "erro": True})
        
    conn.execute("UPDATE agendamentos SET data_hora_inicio = ?, data_hora_fim_previsto = ?, status = 'Agendado' WHERE id = ?", (nova_data_hora, nova_data_hora_fim, ag_id))
    
    nova_dt_obj = datetime.datetime.strptime(nova_data_hora, '%Y-%m-%d %H:%M:%S')
    msg = f"Olá, {cliente['nome']}! Seu agendamento de {nome_proc} foi REMARCADO com sucesso para o dia {nova_dt_obj.strftime('%d/%m/%Y')} às {nova_dt_obj.strftime('%H:%M')} na Smell CLINIC | SPA."
    conn.execute("INSERT INTO fila_whatsapp (numero_destino, mensagem) VALUES (?, ?)", (cliente['telefone'], msg))
    
    conn.commit()
    conn.close()
    
    return jsonify({"mensagem": "Agendamento remarcado com sucesso! Lembrete enviado ao WhatsApp.", "erro": False})

@main_bp.route('/combo/novo', methods=['POST'])
def novo_combo():
    nome_combo = request.form.get('nome_combo')
    servicos = request.form.getlist('combo_servicos') 
    observacoes = request.form.get('observacoes', '')
    valor_base = request.form.get('valor_base', '0').replace('R$ ', '')
    porcentagem_desconto = request.form.get('porcentagem_desconto', '0')
    valor_final = request.form.get('valor_final', '0').replace('R$ ', '')

    try:
        conn = get_db_connection()
        conn.execute('''CREATE TABLE IF NOT EXISTS pacotes_combos (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            nome TEXT,
                            tipo TEXT,
                            servicos_ids TEXT,
                            observacoes TEXT,
                            valor_base REAL,
                            porcentagem_desconto REAL,
                            valor_final REAL,
                            ativo INTEGER DEFAULT 1
                        )''')
        
        conn.execute("""
            INSERT INTO pacotes_combos 
            (nome, tipo, servicos_ids, observacoes, valor_base, porcentagem_desconto, valor_final) 
            VALUES (?, 'Combo', ?, ?, ?, ?, ?)
        """, (nome_combo, ",".join(servicos), observacoes, float(valor_base), float(porcentagem_desconto), float(valor_final)))
        
        conn.commit()
        conn.close()
        return jsonify({"mensagem": "Combo cadastrado com sucesso! Ele já pode ser agendado.", "erro": False})
    except Exception as e:
        return jsonify({"mensagem": f"Erro interno ao salvar combo: {str(e)}", "erro": True})

@main_bp.route('/pacote/novo', methods=['POST'])
def novo_pacote():
    nome_pacote = request.form.get('nome_pacote')
    servico_id = request.form.get('servico_id')
    quantidade_sessoes = request.form.get('quantidade_sessoes')
    valor_base = request.form.get('valor_base', '0').replace('R$ ', '')

    try:
        conn = get_db_connection()
        conn.execute('''CREATE TABLE IF NOT EXISTS pacotes_combos (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            nome TEXT,
                            tipo TEXT,
                            servicos_ids TEXT,
                            observacoes TEXT,
                            valor_base REAL,
                            porcentagem_desconto REAL,
                            valor_final REAL,
                            ativo INTEGER DEFAULT 1
                        )''')
        
        conn.execute("""
            INSERT INTO pacotes_combos 
            (nome, tipo, servicos_ids, observacoes, valor_base, porcentagem_desconto, valor_final) 
            VALUES (?, 'Pacote', ?, ?, ?, 0, ?)
        """, (nome_pacote, servico_id, f"{quantidade_sessoes} sessões", float(valor_base), float(valor_base)))
        
        conn.commit()
        conn.close()
        return jsonify({"mensagem": "Pacote Estratégico cadastrado com sucesso! Disponível para agendamentos.", "erro": False})
    except Exception as e:
        return jsonify({"mensagem": f"Erro interno ao salvar pacote: {str(e)}", "erro": True})


@main_bp.route('/profissional/novo', methods=['POST'])
def novo_profissional():
    nome = request.form.get('nome')
    especialidade = request.form.get('especialidade')
    conn = get_db_connection()
    conn.execute("INSERT INTO profissionais (nome, especialidade, status) VALUES (?, ?, 'Ativo')", (nome, especialidade))
    conn.commit()
    conn.close()
    return jsonify({"mensagem": "Profissional cadastrado com sucesso! A agenda já foi atualizada com a nova coluna."})

@main_bp.route('/cliente/novo', methods=['POST'])
def novo_cliente():
    nome = request.form.get('nome')
    cpf = request.form.get('cpf')
    data_nascimento = request.form.get('data_nascimento')
    telefone_cru = request.form.get('telefone')
    instagram = request.form.get('instagram', '')
    profissao = request.form.get('profissao', '')
    telefone_formatado = formatar_telefone(telefone_cru)
    conn = get_db_connection()
    try:
        conn.execute("INSERT INTO clientes (nome, cpf, telefone, data_nascimento, instagram, profissao) VALUES (?, ?, ?, ?, ?, ?)", (nome, cpf, telefone_formatado, data_nascimento, instagram, profissao))
        msg_boas_vindas = f"Olá, {nome}! Seu cadastro na Smell CLINIC | SPA foi realizado com sucesso. Seja muito bem-vindo(a)!"
        conn.execute("INSERT INTO fila_whatsapp (numero_destino, mensagem) VALUES (?, ?)", (telefone_formatado, msg_boas_vindas))
        conn.commit()
        mensagem = "Cliente cadastrado com sucesso! O DDI/DDD foi formatado automaticamente."
    except sqlite3.IntegrityError:
        mensagem = "Erro ao cadastrar. Verifique se o CPF já existe."
    finally:
        conn.close()
    return jsonify({"mensagem": mensagem})

@main_bp.route('/cliente/editar', methods=['POST'])
def editar_cliente():
    cliente_id = request.form.get('id')
    nome = request.form.get('nome')
    cpf = request.form.get('cpf')
    telefone_cru = request.form.get('telefone')
    data_nascimento = request.form.get('data_nascimento')
    instagram = request.form.get('instagram', '')
    profissao = request.form.get('profissao', '')
    telefone_formatado = formatar_telefone(telefone_cru)
    conn = get_db_connection()
    try:
        conn.execute("UPDATE clientes SET nome = ?, cpf = ?, telefone = ?, data_nascimento = ?, instagram = ?, profissao = ? WHERE id = ?", (nome, cpf, telefone_formatado, data_nascimento, instagram, profissao, cliente_id))
        conn.commit()
        msg = "Cliente atualizado com sucesso!"
    except sqlite3.IntegrityError:
        msg = "Erro ao atualizar: O CPF informado já pertence a outro cadastro."
    finally:
        conn.close()
    return jsonify({"mensagem": msg})

@main_bp.route('/cliente/excluir', methods=['POST'])
def excluir_cliente():
    cliente_id = request.form.get('id')
    conn = get_db_connection()
    try:
        conn.execute("DELETE FROM clientes WHERE id = ?", (cliente_id,))
        conn.commit()
        msg = "Cliente excluído permanentemente!"
    except sqlite3.IntegrityError:
        msg = "BLOQUEADO DE SEGURANÇA: Não é possível excluir este cliente pois ele já possui agendamentos no histórico."
    finally:
        conn.close()
    return jsonify({"mensagem": msg})

@main_bp.route('/servico/novo', methods=['POST'])
def novo_servico():
    nome = request.form.get('nome')
    duracao = request.form.get('duracao_minutos')
    preco = request.form.get('preco_padrao')
    conn = get_db_connection()
    conn.execute("INSERT INTO servicos (nome, duracao_minutos, preco_padrao) VALUES (?, ?, ?)", (nome, duracao, preco))
    conn.commit()
    conn.close()
    return jsonify({"mensagem": "Serviço salvo na base de dados! Ele já aparecerá na lista."})

@main_bp.route('/servico/editar', methods=['POST'])
def editar_servico():
    servico_id = request.form.get('id')
    nome = request.form.get('nome')
    duracao = request.form.get('duracao_minutos')
    preco = request.form.get('preco_padrao')
    conn = get_db_connection()
    conn.execute("UPDATE servicos SET nome = ?, duracao_minutos = ?, preco_padrao = ? WHERE id = ?", (nome, duracao, preco, servico_id))
    conn.commit()
    conn.close()
    return jsonify({"mensagem": "Serviço atualizado com sucesso na tabela!"})

@main_bp.route('/servico/excluir', methods=['POST'])
def excluir_servico():
    servico_id = request.form.get('id')
    conn = get_db_connection()
    try:
        conn.execute("DELETE FROM servicos WHERE id = ?", (servico_id,))
        conn.commit()
        msg = "Procedimento excluído com sucesso!"
    except sqlite3.IntegrityError:
        msg = "BLOQUEADO DE SEGURANÇA: Não é possível excluir este serviço pois ele faz parte de agendamentos."
    finally:
        conn.close()
    return jsonify({"mensagem": msg})

@main_bp.route('/financeiro/despesa', methods=['POST'])
def nova_despesa():
    observacoes = request.form.get('observacoes')
    valor = request.form.get('valor')
    forma_pagamento = request.form.get('forma_pagamento')
    conn = get_db_connection()
    conn.execute("INSERT INTO fluxo_caixa (tipo, valor, forma_pagamento, observacoes) VALUES ('Saída', ?, ?, ?)", (valor, forma_pagamento, observacoes))
    conn.commit()
    conn.close()
    return jsonify({"mensagem": "Despesa registrada. O saldo do caixa foi atualizado!"})

@main_bp.route('/financeiro/fechar', methods=['POST'])
def fechar_caixa():
    return jsonify({"mensagem": "Caixa do dia fechado com sucesso! Relatório gerado."})

@main_bp.route('/api/agendamentos/mes', methods=['GET'])
def agendamentos_mes():
    mes_ano = request.args.get('mes_ano') 
    conn = get_db_connection()
    agendamentos = conn.execute("SELECT substr(data_hora_inicio, 1, 10) as data, count(id) as total FROM agendamentos WHERE data_hora_inicio LIKE ? AND status != 'Cancelado' GROUP BY substr(data_hora_inicio, 1, 10)", (f"{mes_ano}%",)).fetchall()
    conn.close()
    resultado = {row['data']: row['total'] for row in agendamentos}
    return jsonify(resultado)

@main_bp.route('/whatsapp/iniciar-motor', methods=['POST'])
def iniciar_motor_whatsapp():
    global bot_process
    
    if bot_process is None or bot_process.poll() is not None:
        qr_path = os.path.join(current_app.root_path, '..', 'qr_code.png')
        if os.path.exists(qr_path):
            os.remove(qr_path)
            
        cwd = os.path.abspath(os.path.join(current_app.root_path, '..'))
        
        if sys.platform == 'win32':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            bot_process = subprocess.Popen('node bot.js', cwd=cwd, shell=True, startupinfo=startupinfo)
        else:
            bot_process = subprocess.Popen('node bot.js', cwd=cwd, shell=True)
            
        return jsonify({"status": "sucesso", "mensagem": "Motor do WhatsApp iniciado em background."})
    else:
        return jsonify({"status": "aviso", "mensagem": "O motor já está em execução."})

@main_bp.route('/whatsapp/status', methods=['GET'])
def whatsapp_status():
    status_path = os.path.join(current_app.root_path, '..', 'whatsapp_status.txt')
    if os.path.exists(status_path):
        with open(status_path, 'r') as f: return jsonify({"conectado": f.read().strip() == 'CONECTADO', "status_texto": f.read().strip()})
    return jsonify({"conectado": False, "status_texto": "DESLIGADO"})

@main_bp.route('/whatsapp/qr', methods=['GET'])
def whatsapp_qr():
    import base64
    qr_path = os.path.join(current_app.root_path, '..', 'qr_code.png')
    if os.path.exists(qr_path):
        with open(qr_path, "rb") as image_file: return jsonify({"status": "sucesso", "qr_data": f"data:image/png;base64,{base64.b64encode(image_file.read()).decode('utf-8')}"})
    return jsonify({"status": "aguardando", "mensagem": "Aguardando o motor Node.js..."})

@main_bp.route('/configuracoes/selecionar-pasta', methods=['GET'])
def selecionar_pasta():
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw() 
    root.attributes('-topmost', True) 
    pasta = filedialog.askdirectory(parent=root, title="Selecione a pasta de Backup")
    root.destroy()
    return jsonify({"pasta": pasta})

@main_bp.route('/configuracoes/backup', methods=['POST'])
def executar_backup():
    pasta_destino = request.form.get('pasta_destino')
    if not pasta_destino or not os.path.exists(pasta_destino): return jsonify({"mensagem": "Erro: A pasta destino selecionada não existe."})
    db_path = os.path.join(current_app.root_path, '..', 'smell_clinic_spa.db')
    fotos_path = os.path.join(current_app.root_path, '..', 'smell_fotos')
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    pasta_backup_final = os.path.join(pasta_destino, f"Backup_Smell_{timestamp}")
    try:
        os.makedirs(pasta_backup_final, exist_ok=True)
        if os.path.exists(db_path): shutil.copy2(db_path, pasta_backup_final)
        if os.path.exists(fotos_path): shutil.copytree(fotos_path, os.path.join(pasta_backup_final, 'smell_fotos'))
        return jsonify({"mensagem": f"Backup Realizado com Sucesso em:\n{pasta_backup_final}"})
    except Exception as e: return jsonify({"mensagem": f"Erro crítico ao realizar backup: {e}"})

@main_bp.route('/estoque', endpoint='estoque')
def estoque():
    conn = get_estoque_db_connection()
    produtos = conn.execute("SELECT p.*, c.nome as categoria_nome FROM produtos p LEFT JOIN categorias c ON p.categoria_id = c.id WHERE p.status = 'Ativo' ORDER BY p.descricao ASC").fetchall()
    categorias = conn.execute("SELECT * FROM categorias ORDER BY nome ASC").fetchall()
    conn.close()
    return render_template('estoque.html', produtos=produtos, categorias=categorias)

def gerar_codigo_produto_unico():
    conn = get_estoque_db_connection()
    while True:
        codigo = random.randint(10000000, 99999999)
        if not conn.execute("SELECT codigo FROM produtos WHERE codigo = ?", (codigo,)).fetchone():
            conn.close()
            return codigo

@main_bp.route('/estoque/produto/novo', methods=['POST'])
def novo_produto():
    descricao = request.form.get('descricao')
    categoria_nome = request.form.get('categoria', '').strip()
    valor_unitario = float(request.form.get('valor_unitario', 0.0))
    quantidade = int(request.form.get('quantidade', 0))

    conn = get_estoque_db_connection()
    categoria = conn.execute("SELECT id FROM categorias WHERE nome = ?", (categoria_nome,)).fetchone()
    if not categoria:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO categorias (nome) VALUES (?)", (categoria_nome,))
        categoria_id = cursor.lastrowid
    else:
        categoria_id = categoria['id']

    codigo = gerar_codigo_produto_unico()

    conn.execute("INSERT INTO produtos (codigo, descricao, categoria_id, valor_unitario, quantidade, status) VALUES (?, ?, ?, ?, ?, 'Ativo')", (codigo, descricao, categoria_id, valor_unitario, quantidade))
    
    if quantidade > 0:
        conn.execute("INSERT INTO historico_estoque (produto_codigo, tipo, quantidade_movimentada, quantidade_saldo, observacoes) VALUES (?, 'Entrada', ?, ?, 'Cadastro Inicial')", (codigo, quantidade, quantidade))
        
    conn.commit()
    conn.close()
    return jsonify({"mensagem": f"Produto cadastrado com sucesso! Código Gerado: {codigo}", "codigo": codigo})

@main_bp.route('/estoque/produto/editar', methods=['POST'])
def editar_produto():
    codigo = request.form.get('codigo')
    nova_descricao = request.form.get('descricao')
    nova_categoria_nome = request.form.get('categoria', '').strip()
    novo_valor = float(request.form.get('valor_unitario', 0.0))

    conn = get_estoque_db_connection()
    prod_atual = conn.execute("SELECT descricao, valor_unitario, quantidade, categoria_id FROM produtos WHERE codigo = ?", (codigo,)).fetchone()
    
    if not prod_atual:
        conn.close()
        return jsonify({"mensagem": "Produto não encontrado.", "erro": True})

    categoria = conn.execute("SELECT id FROM categorias WHERE nome = ?", (nova_categoria_nome,)).fetchone()
    if not categoria:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO categorias (nome) VALUES (?)", (nova_categoria_nome,))
        categoria_id = cursor.lastrowid
    else:
        categoria_id = categoria['id']

    observacoes_historico = []
    if prod_atual['descricao'] != nova_descricao:
        observacoes_historico.append(f"Nome alterado: de '{prod_atual['descricao']}' para '{nova_descricao}'")
    if float(prod_atual['valor_unitario']) != float(novo_valor):
        observacoes_historico.append(f"Preço alterado: de R$ {prod_atual['valor_unitario']:.2f} para R$ {novo_valor:.2f}")

    conn.execute("""
        UPDATE produtos 
        SET descricao = ?, categoria_id = ?, valor_unitario = ? 
        WHERE codigo = ?
    """, (nova_descricao, categoria_id, novo_valor, codigo))

    if observacoes_historico:
        obs_final = " | ".join(observacoes_historico)
        conn.execute("""
            INSERT INTO historico_estoque 
            (produto_codigo, tipo, quantidade_movimentada, quantidade_saldo, observacoes) 
            VALUES (?, 'Ajuste', 0, ?, ?)
        """, (codigo, prod_atual['quantidade'], obs_final))

    conn.commit()
    conn.close()
    
    return jsonify({"mensagem": "Produto atualizado com sucesso! O histórico foi registrado.", "erro": False})

@main_bp.route('/estoque/produto/inativar', methods=['POST'])
def inativar_produto():
    codigo = request.form.get('codigo')
    conn = get_estoque_db_connection()
    conn.execute("UPDATE produtos SET status = 'Inativo' WHERE codigo = ?", (codigo,))
    conn.commit()
    conn.close()
    return jsonify({"mensagem": "Produto inativado com sucesso."})

@main_bp.route('/estoque/produto/ajustar', methods=['POST'])
def ajustar_estoque_unitario():
    codigo = int(request.form.get('codigo'))
    nova_quantidade = int(request.form.get('quantidade', 0))
    
    conn = get_estoque_db_connection()
    prod = conn.execute("SELECT quantidade FROM produtos WHERE codigo = ?", (codigo,)).fetchone()
    
    if prod and prod['quantidade'] != nova_quantidade:
        diff = nova_quantidade - prod['quantidade']
        tipo = 'Entrada' if diff > 0 else 'Saída'
        
        conn.execute("UPDATE produtos SET quantidade = ? WHERE codigo = ?", (nova_quantidade, codigo))
        conn.execute("INSERT INTO historico_estoque (produto_codigo, tipo, quantidade_movimentada, quantidade_saldo, observacoes) VALUES (?, ?, ?, ?, 'Ajuste Manual Rápido')", (codigo, tipo, abs(diff), nova_quantidade))
        conn.commit()
        
    conn.close()
    return jsonify({"mensagem": "Estoque do produto atualizado com sucesso."})

@main_bp.route('/estoque/inventario/exportar', methods=['GET'])
def exportar_inventario():
    conn = get_estoque_db_connection()
    produtos = conn.execute("SELECT codigo, descricao, quantidade FROM produtos WHERE status = 'Ativo' ORDER BY descricao ASC").fetchall()
    conn.close()

    df = pd.DataFrame([{'Código do Item': p['codigo'], 'Descrição do Item': p['descricao'], 'Quantidade Atual': p['quantidade'], 'Quantidade Nova': ''} for p in produtos])
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Inventário')
    output.seek(0)
    return send_file(output, download_name="ficha_inventario.xlsx", as_attachment=True, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@main_bp.route('/estoque/inventario/importar', methods=['POST'])
def importar_inventario():
    file = request.files.get('file')
    if not file or file.filename == '': return jsonify({'erro': True, 'mensagem': 'Nenhum arquivo anexado.'})
    try:
        df = pd.read_excel(file)
        conn = get_estoque_db_connection()
        atualizados = 0
        for index, row in df.iterrows():
            codigo = row.get('Código do Item')
            nova_qtde = row.get('Quantidade Nova')
            if pd.notna(nova_qtde) and codigo:
                nova_qtde_int = int(nova_qtde)
                prod = conn.execute("SELECT quantidade FROM produtos WHERE codigo = ?", (int(codigo),)).fetchone()
                if prod and prod['quantidade'] != nova_qtde_int:
                    diff = nova_qtde_int - prod['quantidade']
                    tipo = 'Entrada' if diff > 0 else 'Saída'
                    conn.execute("UPDATE produtos SET quantidade = ? WHERE codigo = ?", (nova_qtde_int, int(codigo)))
                    conn.execute("INSERT INTO historico_estoque (produto_codigo, tipo, quantidade_movimentada, quantidade_saldo, observacoes) VALUES (?, ?, ?, ?, 'Importação de Inventário Excel')", (int(codigo), tipo, abs(diff), nova_qtde_int))
                    atualizados += 1
        conn.commit()
        conn.close()
        return jsonify({'erro': False, 'mensagem': f'Inventário importado! {atualizados} produtos foram atualizados.'})
    except Exception as e:
        return jsonify({'erro': True, 'mensagem': f'Falha ao processar planilha: {str(e)}'})

@main_bp.route('/relatorios', endpoint='relatorios')
def relatorios():
    conn = get_estoque_db_connection()
    produtos = conn.execute("SELECT codigo, descricao FROM produtos WHERE status = 'Ativo' ORDER BY descricao ASC").fetchall()
    conn.close()
    return render_template('relatorios.html', produtos=produtos)

@main_bp.route('/api/relatorios/vendas', methods=['GET'])
def relatorio_vendas():
    mes = request.args.get('mes') 
    conn = get_estoque_db_connection()
    query = """
        SELECT p.codigo, p.descricao, SUM(h.quantidade_movimentada) as total_vendido
        FROM historico_estoque h
        JOIN produtos p ON h.produto_codigo = p.codigo
        WHERE h.tipo = 'Venda' AND h.data_hora LIKE ?
        GROUP BY p.codigo
        ORDER BY total_vendido DESC
        LIMIT 50
    """
    rows = conn.execute(query, (f"{mes}%",)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@main_bp.route('/api/relatorios/saidas', methods=['GET'])
def relatorio_saidas():
    inicio = request.args.get('inicio')
    fim = request.args.get('fim')
    conn = get_db_connection()
    query = """
        SELECT data_hora_lancamento, observacoes, forma_pagamento, valor 
        FROM fluxo_caixa 
        WHERE tipo = 'Saída' AND date(data_hora_lancamento) BETWEEN ? AND ?
        ORDER BY data_hora_lancamento DESC
    """
    rows = conn.execute(query, (inicio, fim)).fetchall()
    
    formatted_rows = []
    for r in rows:
        row_dict = dict(r)
        row_dict['data_hora_lancamento_fmt'] = formatar_data_br(row_dict['data_hora_lancamento'])
        formatted_rows.append(row_dict)
        
    conn.close()
    return jsonify(formatted_rows)

@main_bp.route('/api/relatorios/historico-estoque', methods=['GET'])
def relatorio_historico():
    codigo = request.args.get('codigo')
    conn = get_estoque_db_connection()
    query = """
        SELECT data_hora, tipo, quantidade_movimentada, quantidade_saldo, observacoes
        FROM historico_estoque
        WHERE produto_codigo = ?
        ORDER BY data_hora DESC
    """
    rows = conn.execute(query, (codigo,)).fetchall()
    
    formatted_rows = []
    for r in rows:
        row_dict = dict(r)
        row_dict['data_hora_fmt'] = formatar_data_br(row_dict['data_hora'])
        formatted_rows.append(row_dict)
        
    conn.close()
    return jsonify(formatted_rows)