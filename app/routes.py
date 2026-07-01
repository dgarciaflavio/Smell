from flask import Blueprint, render_template, request, jsonify, current_app, send_from_directory, send_file, render_template_string, redirect, url_for, session
from app.database import get_db_connection
from app.estoque_database import get_estoque_db_connection
from app.models import Usuario, Comissao
from functools import wraps
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
import socket

main_bp = Blueprint('main', __name__)

bot_process = None
status_backup_global = {"em_andamento": False, "mensagem": "", "progresso": 0}
BACKGROUND_TASKS_STARTED = False

def get_base_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

def obter_ip_local():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))
        IP = s.getsockname()[0]
    except Exception:
        IP = '127.0.0.1'
    finally:
        s.close()
    return IP

def get_db_absoluto():
    db_path = os.path.join(get_base_dir(), 'smell_clinic_spa.db')
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

# ==============================================================
# HELPERS DE FORMATAÇÃO
# ==============================================================

def formatar_moeda_br(valor):
    try:
        return "{:,.2f}".format(float(valor)).replace(",", "X").replace(".", ",").replace("X", ".")
    except:
        return "0,00"

@main_bp.app_template_filter('moeda_br')
def moeda_br_filter(valor):
    return formatar_moeda_br(valor)

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

# ==============================================================
# MIDDLEWARES E SEGURANÇA (LOGIN E PERFIS)
# ==============================================================

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'usuario_id' not in session:
            return redirect(url_for('main.login'))
        return f(*args, **kwargs)
    return decorated_function

@main_bp.before_app_request
def verificar_regras_seguranca():
    rota_atual = request.endpoint
    rotas_livres = ['static', 'main.login', 'main.setup_admin', 'main.totem_facial', 
                    'main.totem_corporal', 'main.totem_pes', 'main.totem_sucesso', 'main.acesso_remoto']
    
    if rota_atual in rotas_livres or (rota_atual and rota_atual.startswith('static')):
        return

    if Usuario.contar_admins() == 0:
        return redirect(url_for('main.setup_admin'))

    if 'usuario_id' not in session:
        return redirect(url_for('main.login'))

    if session.get('primeiro_acesso') == 1 and rota_atual != 'main.primeiro_acesso':
        return redirect(url_for('main.primeiro_acesso'))

# ==============================================================
# ROTAS DE AUTENTICAÇÃO E CONFIGURAÇÃO INICIAL
# ==============================================================

@main_bp.route('/setup_admin', methods=['GET', 'POST'])
def setup_admin():
    if Usuario.contar_admins() > 0:
        return redirect(url_for('main.login'))

    if request.method == 'POST':
        nome = request.form.get('nome')
        cpf = request.form.get('cpf')
        senha = request.form.get('senha')
        
        sucesso = Usuario.criar_usuario(nome, cpf, senha, "", "Admin", 0.0)
        if sucesso:
            conn = get_db_connection()
            conn.execute("UPDATE usuarios SET primeiro_acesso = 0 WHERE cpf = ?", (cpf,))
            conn.commit()
            conn.close()
            return redirect(url_for('main.login'))
        else:
            return "Erro ao criar administrador. Tente novamente."
            
    return render_template('setup_admin.html')

@main_bp.route('/login', methods=['GET', 'POST'])
def login():
    if 'usuario_id' in session:
        return redirect(url_for('main.agenda'))

    erro = None
    if request.method == 'POST':
        cpf = request.form.get('cpf')
        senha = request.form.get('senha')
        usuario = Usuario.autenticar(cpf, senha)
        
        if usuario:
            session['usuario_id'] = usuario['id']
            session['usuario_nome'] = usuario['nome']
            session['usuario_perfil'] = usuario['nivel_perfil']
            session['primeiro_acesso'] = usuario['primeiro_acesso']
            
            if usuario['primeiro_acesso'] == 1:
                return redirect(url_for('main.primeiro_acesso'))
            return redirect(url_for('main.agenda'))
        else:
            erro = "CPF ou Senha incorretos."

    return render_template('login.html', erro=erro)

@main_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('main.login'))

@main_bp.route('/primeiro_acesso', methods=['GET', 'POST'])
@login_required
def primeiro_acesso():
    if session.get('primeiro_acesso') != 1:
        return redirect(url_for('main.agenda'))

    erro = None
    if request.method == 'POST':
        nova_senha = request.form.get('nova_senha')
        confirma_senha = request.form.get('confirma_senha')
        
        if nova_senha == confirma_senha and len(nova_senha) >= 4:
            Usuario.atualizar_senha(session['usuario_id'], nova_senha)
            session['primeiro_acesso'] = 0
            return redirect(url_for('main.agenda'))
        else:
            erro = "As senhas não conferem ou são muito curtas."

    return render_template('primeiro_acesso.html', erro=erro)


# ==============================================================
# NOTIFICAÇÕES E BACKGROUND TASKS
# ==============================================================

def garantir_colunas_notificacao():
    conn = get_db_absoluto()
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

def processar_notificacoes_inteligentes():
    conn = get_db_absoluto()
    try:
        agora = datetime.datetime.now()
        config = conn.execute("SELECT hora_abertura, hora_fechamento FROM configuracoes_clinica LIMIT 1").fetchone()
        abertura = int(config['hora_abertura']) if config and config['hora_abertura'] else 8
        fechamento = int(config['hora_fechamento']) if config and config['hora_fechamento'] else 20
        
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
                
                if ag['lembrete_48h_enviado'] == 0:
                    diferenca_dias = (dt_inicio.date() - agora.date()).days
                    if diferenca_dias == 2 and agora.hour >= 10:
                        msg_48h = f"Olá, {ag['nome']}! Passando para lembrar do seu agendamento na Smell CLINIC | SPA em dois dias, {dt_inicio.strftime('%d/%m/%Y')} às {dt_inicio.strftime('%H:%M')}."
                        conn.execute("INSERT INTO fila_whatsapp (numero_destino, mensagem, status) VALUES (?, ?, 'Pendente')", (ag['telefone'], msg_48h))
                        conn.execute("UPDATE agendamentos SET lembrete_48h_enviado = 1 WHERE id = ?", (ag['id'],))
                        
                if ag['lembrete_2h_enviado'] == 0:
                    hora_agendamento = dt_inicio.hour
                    
                    if (hora_agendamento - 2) <= abertura:
                        dt_alvo = datetime.datetime(dt_inicio.year, dt_inicio.month, dt_inicio.day, fechamento - 1, 0, 0) - datetime.timedelta(days=1)
                    else:
                        dt_alvo = dt_inicio - datetime.timedelta(hours=2)
                        
                    if agora >= dt_alvo:
                        msg_2h = f"Olá, {ag['nome']}! Seu agendamento na Smell CLINIC | SPA é logo mais, às {dt_inicio.strftime('%H:%M')}. Estamos te esperando com muito carinho!"
                        conn.execute("INSERT INTO fila_whatsapp (numero_destino, mensagem, status) VALUES (?, ?, 'Pendente')", (ag['telefone'], msg_2h))
                        conn.execute("UPDATE agendamentos SET lembrete_2h_enviado = 1 WHERE id = ?", (ag['id'],))
            except Exception as e:
                pass

        ontem = agora.date() - datetime.timedelta(days=1)
        concluidos_ontem = conn.execute("""
            SELECT a.id, a.cliente_id, a.data_hora_inicio, c.nome, c.telefone 
            FROM agendamentos a 
            JOIN clientes c ON a.cliente_id = c.id 
            WHERE a.status = 'Concluído' 
            AND a.avaliacao_enviada = 0 
            AND date(a.data_hora_inicio) = ?
        """, (ontem.strftime('%Y-%m-%d'),)).fetchall()
        
        link_avaliacao = "https://bit.ly/4gc6M3D" 
        
        for ag in concluidos_ontem:
            futuros = conn.execute("""
                SELECT COUNT(id) as total 
                FROM agendamentos 
                WHERE cliente_id = ? AND data_hora_inicio > ? AND status != 'Cancelado'
            """, (ag['cliente_id'], ag['data_hora_inicio'])).fetchone()
            
            if futuros['total'] == 0:
                msg_aval = f"Olá, {ag['nome']}! Agradecemos por escolher a Smell CLINIC | SPA. Sua opinião é muito importante para nós! Poderia avaliar nosso atendimento e deixar um comentário? É rapidinho:\n{link_avaliacao}"
                conn.execute("INSERT INTO fila_whatsapp (numero_destino, mensagem, status) VALUES (?, ?, 'Pendente')", (ag['telefone'], msg_aval))
                
            conn.execute("UPDATE agendamentos SET avaliacao_enviada = 1 WHERE id = ?", (ag['id'],))
            
        conn.commit()
    except Exception as e:
        pass
    finally:
        conn.close()

def loop_notificacoes_background():
    while True:
        processar_notificacoes_inteligentes()
        time.sleep(60) 

@main_bp.before_app_request
def iniciar_motores_background():
    global BACKGROUND_TASKS_STARTED
    if not BACKGROUND_TASKS_STARTED:
        BACKGROUND_TASKS_STARTED = True
        garantir_colunas_notificacao()
        thread_notif = threading.Thread(target=loop_notificacoes_background, daemon=True)
        thread_notif.start()

def rotina_de_backup_fantasma(pasta_destino):
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
        
        base_dir = get_base_dir()
        db_path = os.path.join(base_dir, 'smell_clinic_spa.db')
        if os.path.exists(db_path):
            shutil.copy2(db_path, pasta_backup_final)
            
        db_estoque_path = os.path.join(base_dir, 'smell_estoque.db')
        if os.path.exists(db_estoque_path):
            shutil.copy2(db_estoque_path, pasta_backup_final)

        status_backup_global["progresso"] = 60
        status_backup_global["mensagem"] = "Salvando evoluções e fotos..."
        fotos_path = os.path.join(base_dir, 'smell_fotos')
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

        conn = sqlite3.connect(db_path)
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
        thread = threading.Thread(target=rotina_de_backup_fantasma, args=(config['pasta_backup'],))
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
@login_required
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
@login_required
def clientes():
    cpf_busca = request.args.get('cpf', '').strip()
    conn = get_db_connection()
    if cpf_busca: clientes_lista = conn.execute("SELECT * FROM clientes WHERE cpf = ?", (cpf_busca,)).fetchall()
    else: clientes_lista = conn.execute("SELECT * FROM clientes ORDER BY id DESC").fetchall()
    conn.close()
    return render_template('clientes.html', clientes=clientes_lista, cpf_busca=cpf_busca)

@main_bp.route('/cliente/<int:cliente_id>/prontuario', endpoint='prontuario')
@login_required
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
    fotos_dir = os.path.abspath(os.path.join(get_base_dir(), 'smell_fotos'))
    return send_from_directory(fotos_dir, filename)

@main_bp.route('/fotos_produtos/<path:filename>', endpoint='serve_fotos_produtos')
def serve_fotos_produtos(filename):
    fotos_dir = os.path.abspath(os.path.join(get_base_dir(), 'fotos_produtos'))
    return send_from_directory(fotos_dir, filename)

@main_bp.route('/api/evolucao/foto', methods=['POST'])
@login_required
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
    
    fotos_dir = os.path.abspath(os.path.join(get_base_dir(), 'smell_fotos', nome_pasta))
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
@login_required
def excluir_foto():
    foto_id = request.form.get('foto_id')
    conn = get_db_connection()
    foto = conn.execute("SELECT * FROM evolucao_fotos WHERE id = ?", (foto_id,)).fetchone()
    if foto:
        fotos_dir = os.path.abspath(os.path.join(get_base_dir(), 'smell_fotos'))
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
@login_required
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
@login_required
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
@login_required
def anamnese_facial(cliente_id):
    conn = get_db_connection()
    cliente = conn.execute("SELECT * FROM clientes WHERE id = ?", (cliente_id,)).fetchone()
    profissionais = conn.execute("SELECT * FROM profissionais WHERE status = 'Ativo'").fetchall()
    conn.close()
    cliente_dict = dict(cliente)
    cliente_dict['idade'] = calcular_idade(cliente_dict['data_nascimento'])
    return render_template('anamnese_facial.html', cliente=cliente_dict, profissionais=profissionais, modo_cliente=False, modo_avaliacao=False)

@main_bp.route('/cliente/<int:cliente_id>/anamnese/corporal', endpoint='anamnese_corporal')
@login_required
def anamnese_corporal(cliente_id):
    conn = get_db_connection()
    cliente = conn.execute("SELECT * FROM clientes WHERE id = ?", (cliente_id,)).fetchone()
    profissionais = conn.execute("SELECT * FROM profissionais WHERE status = 'Ativo'").fetchall()
    conn.close()
    cliente_dict = dict(cliente)
    cliente_dict['idade'] = calcular_idade(cliente_dict['data_nascimento'])
    return render_template('anamnese_corporal.html', cliente=cliente_dict, profissionais=profissionais, modo_cliente=False, modo_avaliacao=False)

@main_bp.route('/cliente/<int:cliente_id>/anamnese/pes', endpoint='anamnese_pes')
@login_required
def anamnese_pes(cliente_id):
    conn = get_db_connection()
    cliente = conn.execute("SELECT * FROM clientes WHERE id = ?", (cliente_id,)).fetchone()
    profissionais = conn.execute("SELECT * FROM profissionais WHERE status = 'Ativo'").fetchall()
    conn.close()
    cliente_dict = dict(cliente)
    cliente_dict['idade'] = calcular_idade(cliente_dict['data_nascimento'])
    return render_template('anamnese_pes.html', cliente=cliente_dict, profissionais=profissionais, modo_cliente=False, modo_avaliacao=False)

@main_bp.route('/cliente/<int:cliente_id>/anamnese/facial/<int:anamnese_id>/avaliar', endpoint='avaliar_anamnese_facial')
@login_required
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
@login_required
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
@login_required
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
    token = request.args.get('token')
    conn = get_db_connection()
    
    if token:
        agora = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        registro_token = conn.execute("SELECT * FROM anamneses_termos WHERE token_temporario = ? AND data_expiracao_token > ? AND data_expiracao_token != 'UTILIZADO'", (token, agora)).fetchone()
        if not registro_token:
            conn.close()
            return render_template_string("""
                <html><head><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Link Expirado</title></head>
                <body style="font-family: sans-serif; text-align: center; padding: 40px; background: #f8d7da; color: #721c24;">
                    <h2>⚠️ Link Expirado ou Inválido</h2>
                    <p>Este link de acesso expirou por segurança ou já foi utilizado. Por favor, solicite um novo na recepção.</p>
                </body></html>
            """)

    cliente = conn.execute("SELECT * FROM clientes WHERE id = ?", (cliente_id,)).fetchone()
    profissionais = conn.execute("SELECT * FROM profissionais WHERE status = 'Ativo'").fetchall()
    conn.close()
    cliente_dict = dict(cliente)
    cliente_dict['idade'] = calcular_idade(cliente_dict['data_nascimento'])
    return render_template('anamnese_facial.html', cliente=cliente_dict, profissionais=profissionais, modo_cliente=True, modo_avaliacao=False)

@main_bp.route('/totem/cliente/<int:cliente_id>/corporal', endpoint='totem_corporal')
def totem_corporal(cliente_id):
    token = request.args.get('token')
    conn = get_db_connection()
    
    if token:
        agora = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        registro_token = conn.execute("SELECT * FROM anamneses_termos WHERE token_temporario = ? AND data_expiracao_token > ? AND data_expiracao_token != 'UTILIZADO'", (token, agora)).fetchone()
        if not registro_token:
            conn.close()
            return render_template_string("""
                <html><head><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Link Expirado</title></head>
                <body style="font-family: sans-serif; text-align: center; padding: 40px; background: #f8d7da; color: #721c24;">
                    <h2>⚠️ Link Expirado ou Inválido</h2>
                    <p>Este link de acesso expirou por segurança ou já foi utilizado. Por favor, solicite um novo na recepção.</p>
                </body></html>
            """)

    cliente = conn.execute("SELECT * FROM clientes WHERE id = ?", (cliente_id,)).fetchone()
    profissionais = conn.execute("SELECT * FROM profissionais WHERE status = 'Ativo'").fetchall()
    conn.close()
    cliente_dict = dict(cliente)
    cliente_dict['idade'] = calcular_idade(cliente_dict['data_nascimento'])
    return render_template('anamnese_corporal.html', cliente=cliente_dict, profissionais=profissionais, modo_cliente=True, modo_avaliacao=False)

@main_bp.route('/totem/cliente/<int:cliente_id>/pes', endpoint='totem_pes')
def totem_pes(cliente_id):
    token = request.args.get('token')
    conn = get_db_connection()
    
    if token:
        agora = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        registro_token = conn.execute("SELECT * FROM anamneses_termos WHERE token_temporario = ? AND data_expiracao_token > ? AND data_expiracao_token != 'UTILIZADO'", (token, agora)).fetchone()
        if not registro_token:
            conn.close()
            return render_template_string("""
                <html><head><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Link Expirado</title></head>
                <body style="font-family: sans-serif; text-align: center; padding: 40px; background: #f8d7da; color: #721c24;">
                    <h2>⚠️ Link Expirado ou Inválido</h2>
                    <p>Este link de acesso expirou por segurança ou já foi utilizado. Por favor, solicite um novo na recepção.</p>
                </body></html>
            """)

    cliente = conn.execute("SELECT * FROM clientes WHERE id = ?", (cliente_id,)).fetchone()
    profissionais = conn.execute("SELECT * FROM profissionais WHERE status = 'Ativo'").fetchall()
    conn.close()
    cliente_dict = dict(cliente)
    cliente_dict['idade'] = calcular_idade(cliente_dict['data_nascimento'])
    return render_template('anamnese_pes.html', cliente=cliente_dict, profissionais=profissionais, modo_cliente=True, modo_avaliacao=False)

@main_bp.route('/totem/sucesso', endpoint='totem_sucesso')
def totem_sucesso():
    return render_template('totem_sucesso.html', modo_cliente=True)

@main_bp.route('/remoto/token/<token>', endpoint='acesso_remoto')
def acesso_remoto(token):
    conn = get_db_connection()
    agora = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        registro = conn.execute("SELECT * FROM anamneses_termos WHERE token_temporario = ? AND data_expiracao_token > ? AND data_expiracao_token != 'UTILIZADO'", (token, agora)).fetchone()
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
    
    assinatura_cliente = request.form.get('assinatura_cliente_base64')
    
    # NOVAS LINHAS PARA TRATAR AS NOVAS ASSINATURAS:
    assinatura_profissional = request.form.get('assinatura_profissional_base64')
    # A assinatura de testemunha não é mais exigida pela UI, mas mantemos o código
    # caso haja envios nulos ou para não quebrar compatibilidade do banco:
    assinatura_testemunha = request.form.get('assinatura_testemunha_base64')
    
    data_retroativa = request.form.get('data_retroativa')
    token = request.form.get('token') 
    
    conn = get_db_connection()
    
    if token:
        agora = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            registro_token = conn.execute("SELECT * FROM anamneses_termos WHERE token_temporario = ? AND data_expiracao_token > ?", (token, agora)).fetchone()
            if not registro_token:
                conn.close()
                return jsonify({"mensagem": "ERRO: O link utilizado expirou ou a ficha já foi enviada ao servidor.", "erro": True})
                
            conn.execute("UPDATE anamneses_termos SET data_expiracao_token = '2000-01-01 00:00:00' WHERE token_temporario = ?", (token,))
        except sqlite3.OperationalError: pass
    
    # GARANTINDO QUE AS COLUNAS EXISTAM:
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

    # UPDATE DAS QUERIES DE INSERÇÃO:
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
        
    conn.commit()
    conn.close()
    return jsonify({"mensagem": f"Anamnese {tipo} salva com sucesso!", "erro": False})

@main_bp.route('/api/anamnese/atualizar', methods=['POST'])
@login_required
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
@login_required
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
@login_required
def servicos():
    conn = get_db_connection()
    servicos_lista = conn.execute("SELECT * FROM servicos ORDER BY nome ASC").fetchall()
    conn.close()
    return render_template('servicos.html', servicos=servicos_lista)

@main_bp.route('/financeiro', endpoint='financeiro')
@login_required
def financeiro():
    data_busca = request.args.get('data')
    hoje = data_busca if data_busca else datetime.datetime.now().strftime('%Y-%m-%d')
    
    conn = get_db_connection()
    movimentacoes = conn.execute("SELECT * FROM fluxo_caixa WHERE date(data_hora_lancamento) = ? ORDER BY data_hora_lancamento DESC", (hoje,)).fetchall()
    entradas = sum(m['valor'] for m in movimentacoes if m['tipo'] == 'Entrada')
    saidas = sum(m['valor'] for m in movimentacoes if m['tipo'] == 'Saída')
    saldo = entradas - saidas
    
    # VALIDAÇÃO CAIXA ANTERIOR
    ontem_str = (datetime.datetime.strptime(hoje, '%Y-%m-%d') - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
    movs_ontem = conn.execute("SELECT id FROM fluxo_caixa WHERE date(data_hora_lancamento) = ?", (ontem_str,)).fetchall()
    caixa_ontem_fechado = conn.execute("SELECT id FROM fluxo_caixa WHERE date(data_hora_lancamento) = ? AND observacoes LIKE '%Fechamento%'", (ontem_str,)).fetchone()
    
    alerta_caixa = False
    if len(movs_ontem) > 0 and not caixa_ontem_fechado:
        alerta_caixa = True

    conn.close()

    conn_est = get_estoque_db_connection()
    produtos = conn_est.execute("SELECT codigo, descricao, valor_unitario, quantidade FROM produtos WHERE status = 'Ativo' AND quantidade > 0 ORDER BY descricao ASC").fetchall()
    produtos_json = [dict(p) for p in produtos]
    conn_est.close()

    return render_template('financeiro.html', movimentacoes=movimentacoes, entradas=entradas, saidas=saidas, saldo=saldo, produtos=produtos_json, data_hoje=hoje, alerta_caixa=alerta_caixa)

@main_bp.route('/financeiro/venda', methods=['POST'])
@login_required
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
@login_required
def configuracoes():
    if session.get('usuario_perfil') != 'Admin':
        return redirect(url_for('main.agenda'))

    conn = get_db_connection()
    profissionais = conn.execute("SELECT * FROM profissionais ORDER BY nome ASC").fetchall()
    config = conn.execute("SELECT * FROM configuracoes_clinica LIMIT 1").fetchone()
    usuarios = conn.execute("SELECT id, nome, cpf, telefone, nivel_perfil, comissao_percentual, status FROM usuarios ORDER BY nome ASC").fetchall()
    conn.close()
    return render_template('configuracoes.html', profissionais=profissionais, config=config, usuarios=usuarios)

@main_bp.route('/configuracoes/usuario/novo', methods=['POST'])
@login_required
def novo_usuario():
    nome = request.form.get('nome')
    cpf = request.form.get('cpf')
    senha = request.form.get('senha')
    telefone = request.form.get('telefone')
    nivel_perfil = request.form.get('nivel_perfil')
    comissao = request.form.get('comissao_percentual', 0.0)
    
    sucesso = Usuario.criar_usuario(nome, cpf, senha, telefone, nivel_perfil, float(comissao))
    if sucesso:
        return jsonify({"erro": False, "mensagem": "Usuário criado com sucesso!"})
    else:
        return jsonify({"erro": True, "mensagem": "O CPF já está cadastrado no sistema."})

@main_bp.route('/configuracoes/horario', methods=['POST'])
@login_required
def salvar_horario():
    abertura = request.form.get('hora_abertura')
    fechamento = request.form.get('hora_fechamento')
    conn = get_db_connection()
    conn.execute("UPDATE configuracoes_clinica SET hora_abertura = ?, hora_fechamento = ? WHERE id = 1", (abertura, fechamento))
    conn.commit()
    conn.close()
    return jsonify({"mensagem": "Horário de funcionamento atualizado com sucesso!"})

@main_bp.route('/agendamento/novo', methods=['POST'])
@login_required
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
        
        token = secrets.token_urlsafe(16)
        expiracao = (datetime.datetime.now() + datetime.timedelta(minutes=60)).strftime("%Y-%m-%d %H:%M:%S")
        try:
            conn.execute("INSERT INTO anamneses_termos (cliente_id, token_temporario, data_expiracao_token, origem_preenchimento) VALUES (?, ?, ?, 'Link Remoto')", (cliente['id'], token, expiracao))
        except sqlite3.OperationalError:
            pass

        link_remoto = f"http://{obter_ip_local()}:5000/remoto/token/{token}"
        msg_agenda_1 = f"Olá, {cliente['nome']}! Seu agendamento de {nome_procedimento} foi confirmado para o dia {inicio_dt.strftime('%d/%m/%Y')} às {inicio_dt.strftime('%H:%M')} na Smell CLINIC | SPA.\n\nPara adiantar seu atendimento, por favor, preencha sua ficha de anamnese clicando neste link (válido por 60 min):"
        msg_agenda_2 = link_remoto
        
        conn.execute("INSERT INTO fila_whatsapp (numero_destino, mensagem, status) VALUES (?, ?, 'Pendente')", (cliente['telefone'], msg_agenda_1))
        conn.execute("INSERT INTO fila_whatsapp (numero_destino, mensagem, status) VALUES (?, ?, 'Pendente')", (cliente['telefone'], msg_agenda_2))
        
    conn.commit()
    conn.close()
    
    texto_sucesso = "Horário salvo com sucesso!" if len(datas_horas) == 1 else f"{len(datas_horas)} horários salvos com sucesso (Combo/Pacote)!"
    return jsonify({"mensagem": f"{texto_sucesso} O lembrete já foi enviado ao WhatsApp do cliente.", "erro": False})

@main_bp.route('/agendamento/atualizar', methods=['POST'])
@login_required
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

    if enviar_link or novo_status == 'Aguardando':
        ag = conn.execute("SELECT a.*, c.nome as cliente_nome, c.telefone FROM agendamentos a JOIN clientes c ON a.cliente_id = c.id WHERE a.id = ?", (ag_id,)).fetchone()
        if ag:
            token = secrets.token_urlsafe(16)
            expiracao = (datetime.datetime.now() + datetime.timedelta(minutes=60)).strftime("%Y-%m-%d %H:%M:%S")
            try:
                conn.execute("INSERT INTO anamneses_termos (cliente_id, token_temporario, data_expiracao_token, origem_preenchimento) VALUES (?, ?, ?, 'Link Remoto')", (ag['cliente_id'], token, expiracao))
            except sqlite3.OperationalError:
                pass

            link_remoto = f"http://{obter_ip_local()}:5000/remoto/token/{token}"
            msg_1 = f"Olá, {ag['cliente_nome']}! Você já está aguardando seu atendimento. Por favor, preencha sua ficha de anamnese clicando no link a seguir (válido por 60 min):"
            msg_2 = link_remoto
            conn.execute("INSERT INTO fila_whatsapp (numero_destino, mensagem, status) VALUES (?, ?, 'Pendente')", (ag['telefone'], msg_1))
            conn.execute("INSERT INTO fila_whatsapp (numero_destino, mensagem, status) VALUES (?, ?, 'Pendente')", (ag['telefone'], msg_2))

    conn.execute("UPDATE agendamentos SET status = ? WHERE id = ?", (novo_status, ag_id))
    conn.commit()
    conn.close()
    
    msg_retorno = f"Status atualizado para '{novo_status}'."
    if enviar_link or novo_status == 'Aguardando':
        msg_retorno += " Link de acesso remoto enviado ao WhatsApp do cliente!"
        
    return jsonify({"mensagem": msg_retorno})

@main_bp.route('/agendamento/remarcar', methods=['POST'])
@login_required
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
    conn.execute("INSERT INTO fila_whatsapp (numero_destino, mensagem, status) VALUES (?, ?, 'Pendente')", (cliente['telefone'], msg))
    
    conn.commit()
    conn.close()
    
    return jsonify({"mensagem": "Agendamento remarcado com sucesso! Lembrete enviado ao WhatsApp.", "erro": False})

@main_bp.route('/combo/novo', methods=['POST'])
@login_required
def novo_combo():
    nome_combo = request.form.get('nome_combo')
    servicos = request.form.getlist('combo_servicos') 
    observacoes = request.form.get('observacoes', '')
    valor_base = request.form.get('valor_base', '0').replace('R$ ', '')
    porcentagem_desconto = request.form.get('porcentagem_desconto', '0')
    valor_final = request.form.get('valor_final', '0').replace('R$ ', '')

    try:
        conn = get_db_connection()
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
@login_required
def novo_pacote():
    nome_pacote = request.form.get('nome_pacote')
    servico_id = request.form.get('servico_id')
    quantidade_sessoes = request.form.get('quantidade_sessoes')
    valor_base = request.form.get('valor_base', '0').replace('R$ ', '')

    try:
        conn = get_db_connection()
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
@login_required
def novo_profissional():
    nome = request.form.get('nome')
    especialidade = request.form.get('especialidade')
    conn = get_db_connection()
    conn.execute("INSERT INTO profissionais (nome, especialidade, status) VALUES (?, ?, 'Ativo')", (nome, especialidade))
    conn.commit()
    conn.close()
    return jsonify({"mensagem": "Profissional cadastrado com sucesso! A agenda já foi atualizada com a nova coluna."})

@main_bp.route('/cliente/novo', methods=['POST'])
@login_required
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
        conn.execute("INSERT INTO fila_whatsapp (numero_destino, mensagem, status) VALUES (?, ?, 'Pendente')", (telefone_formatado, msg_boas_vindas))
        conn.commit()
        mensagem = "Cliente cadastrado com sucesso! O DDI/DDD foi formatado automaticamente."
    except sqlite3.IntegrityError:
        mensagem = "Erro ao cadastrar. Verifique se o CPF já existe."
    finally:
        conn.close()
    return jsonify({"mensagem": mensagem})

@main_bp.route('/cliente/editar', methods=['POST'])
@login_required
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
@login_required
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
@login_required
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
@login_required
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
@login_required
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
@login_required
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
@login_required
def fechar_caixa():
    data_retroativa = request.form.get('data_retroativa')
    data_fechamento = data_retroativa if data_retroativa else datetime.datetime.now().strftime('%d/%m/%Y')
    
    # Registra no BD que o caixa foi fechado
    conn = get_db_connection()
    conn.execute("INSERT INTO fluxo_caixa (tipo, valor, forma_pagamento, observacoes) VALUES ('Fechamento', 0, '-', ?)", (f"Fechamento de Caixa do dia {data_fechamento}",))
    conn.commit()
    conn.close()

    return jsonify({"mensagem": f"Caixa do dia {data_fechamento} fechado com sucesso! Relatório gerado (Visual)."})

@main_bp.route('/api/agendamentos/mes', methods=['GET'])
@login_required
def agendamentos_mes():
    mes_ano = request.args.get('mes_ano') 
    conn = get_db_connection()
    agendamentos = conn.execute("SELECT substr(data_hora_inicio, 1, 10) as data, count(id) as total FROM agendamentos WHERE data_hora_inicio LIKE ? AND status != 'Cancelado' GROUP BY substr(data_hora_inicio, 1, 10)", (f"{mes_ano}%",)).fetchall()
    conn.close()
    resultado = {row['data']: row['total'] for row in agendamentos}
    return jsonify(resultado)

@main_bp.route('/whatsapp/iniciar-motor', methods=['POST'])
@login_required
def iniciar_motor_whatsapp():
    global bot_process
    
    if bot_process is None or bot_process.poll() is not None:
        base_dir = get_base_dir()
        qr_path = os.path.join(base_dir, 'qr_code.png')
        if os.path.exists(qr_path):
            os.remove(qr_path)
            
        caminho_bot = os.path.join(base_dir, 'bot.js')
        
        if sys.platform == 'win32':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            CREATE_NO_WINDOW = 0x08000000
            bot_process = subprocess.Popen(['node', caminho_bot], cwd=base_dir, shell=False, startupinfo=startupinfo, creationflags=CREATE_NO_WINDOW)
        else:
            bot_process = subprocess.Popen(['node', caminho_bot], cwd=base_dir, shell=False)
            
        return jsonify({"status": "sucesso", "mensagem": "Motor do WhatsApp iniciado em background."})
    else:
        return jsonify({"status": "aviso", "mensagem": "O motor já está em execução."})

@main_bp.route('/whatsapp/status', methods=['GET'])
@login_required
def whatsapp_status():
    status_path = os.path.join(get_base_dir(), 'whatsapp_status.txt')
    if os.path.exists(status_path):
        with open(status_path, 'r') as f: 
            conteudo = f.read().strip()
            return jsonify({"conectado": conteudo == 'CONECTADO', "status_texto": conteudo})
    return jsonify({"conectado": False, "status_texto": "DESLIGADO"})

@main_bp.route('/whatsapp/qr', methods=['GET'])
@login_required
def whatsapp_qr():
    import base64
    qr_path = os.path.join(get_base_dir(), 'qr_code.png')
    if os.path.exists(qr_path):
        with open(qr_path, "rb") as image_file: return jsonify({"status": "sucesso", "qr_data": f"data:image/png;base64,{base64.b64encode(image_file.read()).decode('utf-8')}"})
    return jsonify({"status": "aguardando", "mensagem": "Aguardando o motor Node.js..."})

@main_bp.route('/configuracoes/selecionar-pasta', methods=['GET'])
@login_required
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
@login_required
def executar_backup():
    pasta_destino = request.form.get('pasta_destino')
    if not pasta_destino or not os.path.exists(pasta_destino): return jsonify({"mensagem": "Erro: A pasta destino selecionada não existe."})
    
    base_dir = get_base_dir()
    db_path = os.path.join(base_dir, 'smell_clinic_spa.db')
    fotos_path = os.path.join(base_dir, 'smell_fotos')
    
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    pasta_backup_final = os.path.join(pasta_destino, f"Backup_Smell_{timestamp}")
    try:
        os.makedirs(pasta_backup_final, exist_ok=True)
        if os.path.exists(db_path): shutil.copy2(db_path, pasta_backup_final)
        if os.path.exists(fotos_path): shutil.copytree(fotos_path, os.path.join(pasta_backup_final, 'smell_fotos'))
        return jsonify({"mensagem": f"Backup Realizado com Sucesso em:\n{pasta_backup_final}"})
    except Exception as e: return jsonify({"mensagem": f"Erro crítico ao realizar backup: {e}"})

@main_bp.route('/estoque', endpoint='estoque')
@login_required
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
@login_required
def novo_produto():
    descricao = request.form.get('descricao')
    categoria_nome = request.form.get('categoria', '').strip()
    
    # Tratamento da formatação (Ponto de Milhar e Vírgula Decimal)
    valor_unitario_str = request.form.get('valor_unitario', '0')
    valor_unitario_str = valor_unitario_str.replace('.', '').replace(',', '.')
    valor_unitario = float(valor_unitario_str)
    
    quantidade = int(request.form.get('quantidade', 0))
    foto = request.files.get('foto')

    conn = get_estoque_db_connection()
    categoria = conn.execute("SELECT id FROM categorias WHERE nome = ?", (categoria_nome,)).fetchone()
    if not categoria:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO categorias (nome) VALUES (?)", (categoria_nome,))
        categoria_id = cursor.lastrowid
    else:
        categoria_id = categoria['id']

    codigo = gerar_codigo_produto_unico()

    foto_filename = None
    if foto and foto.filename != '':
        ext = foto.filename.split('.')[-1]
        foto_filename = f"prod_{codigo}_{int(time.time())}.{ext}"
        pasta_fotos = os.path.join(get_base_dir(), 'fotos_produtos')
        os.makedirs(pasta_fotos, exist_ok=True)
        foto.save(os.path.join(pasta_fotos, foto_filename))

    conn.execute("INSERT INTO produtos (codigo, descricao, categoria_id, valor_unitario, quantidade, status, foto) VALUES (?, ?, ?, ?, ?, 'Ativo', ?)", (codigo, descricao, categoria_id, valor_unitario, quantidade, foto_filename))
    
    if quantidade > 0:
        conn.execute("INSERT INTO historico_estoque (produto_codigo, tipo, quantidade_movimentada, quantidade_saldo, observacoes) VALUES (?, 'Entrada', ?, ?, 'Cadastro Inicial')", (codigo, quantidade, quantidade))
        
    conn.commit()
    conn.close()
    return jsonify({"mensagem": f"Produto cadastrado com sucesso! Código Gerado: {codigo}", "codigo": codigo})

@main_bp.route('/estoque/produto/editar', methods=['POST'])
@login_required
def editar_produto():
    codigo = request.form.get('codigo')
    nova_descricao = request.form.get('descricao')
    nova_categoria_nome = request.form.get('categoria', '').strip()
    
    # Tratamento da formatação (Ponto de Milhar e Vírgula Decimal)
    novo_valor_str = request.form.get('valor_unitario', '0')
    novo_valor_str = novo_valor_str.replace('.', '').replace(',', '.')
    novo_valor = float(novo_valor_str)
    
    foto = request.files.get('foto')

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

    foto_filename = None
    if foto and foto.filename != '':
        ext = foto.filename.split('.')[-1]
        foto_filename = f"prod_{codigo}_{int(time.time())}.{ext}"
        pasta_fotos = os.path.join(get_base_dir(), 'fotos_produtos')
        os.makedirs(pasta_fotos, exist_ok=True)
        foto.save(os.path.join(pasta_fotos, foto_filename))

    if foto_filename:
        conn.execute("""
            UPDATE produtos 
            SET descricao = ?, categoria_id = ?, valor_unitario = ?, foto = ? 
            WHERE codigo = ?
        """, (nova_descricao, categoria_id, novo_valor, foto_filename, codigo))
    else:
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
@login_required
def inativar_produto():
    codigo = request.form.get('codigo')
    conn = get_estoque_db_connection()
    conn.execute("UPDATE produtos SET status = 'Inativo' WHERE codigo = ?", (codigo,))
    conn.commit()
    conn.close()
    return jsonify({"mensagem": "Produto inativado com sucesso."})

@main_bp.route('/estoque/produto/ajustar', methods=['POST'])
@login_required
def ajustar_estoque_unitario():
    codigo = int(request.form.get('codigo'))
    nova_quantidade = int(request.form.get('quantidade', 0))
    
    # Formatação do novo valor para ajuste
    novo_valor_str = request.form.get('valor_unitario')
    
    conn = get_estoque_db_connection()
    prod = conn.execute("SELECT quantidade, valor_unitario FROM produtos WHERE codigo = ?", (codigo,)).fetchone()
    
    if prod:
        if prod['quantidade'] != nova_quantidade:
            diff = nova_quantidade - prod['quantidade']
            tipo = 'Entrada' if diff > 0 else 'Saída'
            
            conn.execute("UPDATE produtos SET quantidade = ? WHERE codigo = ?", (nova_quantidade, codigo))
            conn.execute("INSERT INTO historico_estoque (produto_codigo, tipo, quantidade_movimentada, quantidade_saldo, observacoes) VALUES (?, ?, ?, ?, 'Ajuste Manual Rápido')", (codigo, tipo, abs(diff), nova_quantidade))
            
        if novo_valor_str:
             novo_valor_str = novo_valor_str.replace('.', '').replace(',', '.')
             novo_valor = float(novo_valor_str)
             if prod['valor_unitario'] != novo_valor:
                 conn.execute("UPDATE produtos SET valor_unitario = ? WHERE codigo = ?", (novo_valor, codigo))
                 conn.execute("INSERT INTO historico_estoque (produto_codigo, tipo, quantidade_movimentada, quantidade_saldo, observacoes) VALUES (?, 'Ajuste', 0, ?, ?)", (codigo, nova_quantidade, f"Preço ajustado via Busca e Ajuste para R$ {novo_valor:.2f}"))
                 
        conn.commit()
        
    conn.close()
    return jsonify({"mensagem": "Estoque do produto atualizado com sucesso."})

@main_bp.route('/estoque/inventario/exportar', methods=['GET'])
@login_required
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
@login_required
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
@login_required
def relatorios():
    conn = get_estoque_db_connection()
    produtos = conn.execute("SELECT codigo, descricao FROM produtos WHERE status = 'Ativo' ORDER BY descricao ASC").fetchall()
    conn.close()
    
    conn_db = get_db_connection()
    usuarios = conn_db.execute("SELECT id, nome, comissao_percentual FROM usuarios WHERE status = 'Ativo'").fetchall()
    conn_db.close()
    
    return render_template('relatorios.html', produtos=produtos, usuarios=usuarios)

@main_bp.route('/relatorios/estoque/imprimir', methods=['GET'])
@login_required
def imprimir_relatorio_estoque():
    """Gera um HTML otimizado para impressão (PDF) do relatório inteligente de estoque (CMM, Valorização)"""
    conn = get_estoque_db_connection()
    
    # Produtos Ativos
    produtos = conn.execute("SELECT codigo, descricao, quantidade, valor_unitario FROM produtos WHERE status = 'Ativo' ORDER BY descricao ASC").fetchall()
    
    # Data de 90 dias atrás para o CMM
    data_90_dias = (datetime.datetime.now() - datetime.timedelta(days=90)).strftime('%Y-%m-%d %H:%M:%S')
    
    relatorio = []
    valorizacao_total = 0
    
    for p in produtos:
        # Busca vendas nos últimos 90 dias
        vendas = conn.execute("""
            SELECT SUM(quantidade_movimentada) as total_vendido 
            FROM historico_estoque 
            WHERE produto_codigo = ? AND tipo = 'Venda' AND data_hora >= ?
        """, (p['codigo'], data_90_dias)).fetchone()
        
        total_vendido = vendas['total_vendido'] if vendas['total_vendido'] else 0
        cmm = round(total_vendido / 3.0, 2)
        
        valorizacao = p['quantidade'] * p['valor_unitario']
        valorizacao_total += valorizacao
        
        dias_estoque = "Sem saída recente"
        if cmm > 0:
            consumo_diario = cmm / 30.0
            dias_estoque = f"{round(p['quantidade'] / consumo_diario)} dias"
            
        relatorio.append({
            'codigo': p['codigo'],
            'descricao': p['descricao'],
            'quantidade': p['quantidade'],
            'valor_unitario': f"R$ {formatar_moeda_br(p['valor_unitario'])}",
            'valorizacao': f"R$ {formatar_moeda_br(valorizacao)}",
            'cmm': str(cmm).replace('.', ','),
            'dias_estoque': dias_estoque
        })
        
    conn.close()
    
    html_template = """
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <title>Relatório de Estoque - PDF</title>
        <style>
            body { font-family: 'Helvetica', 'Arial', sans-serif; color: #333; margin: 0; padding: 20px; }
            h1 { color: #0d9488; text-align: center; border-bottom: 2px solid #0d9488; padding-bottom: 10px; }
            .header-info { display: flex; justify-content: space-between; margin-bottom: 30px; font-size: 14px; color: #666; }
            table { width: 100%; border-collapse: collapse; margin-top: 20px; font-size: 12px; }
            th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
            th { background-color: #f3f4f6; color: #374151; font-weight: bold; }
            .total-row { font-weight: bold; background-color: #e0f2fe; }
            @media print {
                @page { margin: 1cm; }
                button { display: none; }
            }
            .print-btn { background-color: #0d9488; color: white; border: none; padding: 10px 20px; cursor: pointer; border-radius: 5px; font-weight: bold; float: right; margin-bottom: 20px; }
        </style>
    </head>
    <body>
        <button class="print-btn" onclick="window.print()">🖨️ Salvar como PDF / Imprimir</button>
        <h1>Relatório Inteligente de Estoque</h1>
        <div class="header-info">
            <span><strong>Clínica:</strong> Smell CLINIC | SPA</span>
            <span><strong>Data da Geração:</strong> {{ data_hoje }}</span>
        </div>
        
        <table>
            <thead>
                <tr>
                    <th>Código</th>
                    <th>Produto</th>
                    <th>Qtd. Atual</th>
                    <th>Custo Un.</th>
                    <th>Valorização (R$)</th>
                    <th>CMM (Mês)</th>
                    <th>Durabilidade Prevista</th>
                </tr>
            </thead>
            <tbody>
                {% for item in relatorio %}
                <tr>
                    <td>{{ item.codigo }}</td>
                    <td>{{ item.descricao }}</td>
                    <td style="text-align: center;">{{ item.quantidade }}</td>
                    <td>{{ item.valor_unitario }}</td>
                    <td>{{ item.valorizacao }}</td>
                    <td style="text-align: center;">{{ item.cmm }}</td>
                    <td>{{ item.dias_estoque }}</td>
                </tr>
                {% endfor %}
            </tbody>
            <tfoot>
                <tr class="total-row">
                    <td colspan="4" style="text-align: right;">VALORIZAÇÃO TOTAL DO ESTOQUE:</td>
                    <td colspan="3" style="color: #0d9488; font-size: 14px;">R$ {{ valorizacao_total|moeda_br }}</td>
                </tr>
            </tfoot>
        </table>
        
        <div style="margin-top: 40px; font-size: 11px; color: #999; text-align: center;">
            * CMM: Consumo Médio Mensal (baseado nos últimos 90 dias).<br>
            * Durabilidade Prevista: Estimativa de dias que o estoque durará mantendo o ritmo do CMM.
        </div>
    </body>
    </html>
    """
    return render_template_string(html_template, relatorio=relatorio, valorizacao_total=valorizacao_total, data_hoje=datetime.datetime.now().strftime('%d/%m/%Y %H:%M'))

@main_bp.route('/api/relatorios/vendas', methods=['GET'])
@login_required
def relatorio_vendas():
    mes = request.args.get('mes') 
    
    # Se uma ID de usuário for passada, filtramos por vendedor
    usuario_id = request.args.get('usuario_id')
    
    conn = get_estoque_db_connection()
    conn_db = get_db_connection()
    
    if usuario_id:
        # Busca nas vendas do PDV (onde temos a amarração com o vendedor)
        vendas = conn_db.execute("""
            SELECT vi.produto_codigo, vi.descricao, SUM(vi.quantidade) as total_vendido, SUM(vi.total_item) as valor_total
            FROM vendas_itens vi
            JOIN vendas v ON vi.venda_id = v.id
            WHERE v.vendedor_id = ? AND v.data_hora LIKE ?
            GROUP BY vi.produto_codigo
            ORDER BY total_vendido DESC
        """, (usuario_id, f"{mes}%")).fetchall()
        
        resultado = []
        for v in vendas:
            resultado.append({
                'codigo': v['produto_codigo'],
                'descricao': v['descricao'],
                'total_vendido': v['total_vendido'],
                'valor_total': v['valor_total']
            })
            
        conn_db.close()
        conn.close()
        return jsonify(resultado)
    else:
        # Relatório geral de estoque
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
        
        resultado = []
        for r in rows:
            # Pegar o valor atual do produto apenas para referência, já que no historico antigo não tem o valor de venda
            prod = conn.execute("SELECT valor_unitario FROM produtos WHERE codigo = ?", (r['codigo'],)).fetchone()
            val_uni = prod['valor_unitario'] if prod else 0
            resultado.append({
                'codigo': r['codigo'],
                'descricao': r['descricao'],
                'total_vendido': r['total_vendido'],
                'valor_total': r['total_vendido'] * val_uni
            })
            
        conn_db.close()
        conn.close()
        return jsonify(resultado)

@main_bp.route('/api/relatorios/saidas', methods=['GET'])
@login_required
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
@login_required
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

@main_bp.route('/api/comissoes/pagar', methods=['POST'])
@login_required
def pagar_comissao():
    # Bloqueio simples de acesso se não for Admin
    if session.get('usuario_perfil') != 'Admin':
        return jsonify({"erro": True, "mensagem": "Acesso Negado."})
        
    usuario_id = request.form.get('usuario_id')
    valor_total = float(request.form.get('valor_total_vendas', 0))
    percentual_aplicado = float(request.form.get('percentual_aplicado', 0))
    assinatura_admin_base64 = request.form.get('assinatura_admin_base64')
    
    if valor_total <= 0:
        return jsonify({"erro": True, "mensagem": "Não há vendas válidas para pagar comissão."})
        
    if not assinatura_admin_base64:
        return jsonify({"erro": True, "mensagem": "A assinatura do pagador é obrigatória."})
        
    # Registra no BD e gera a Assinatura (Hash)
    recibo = Comissao.registrar_pagamento(usuario_id, valor_total, percentual_aplicado)
    
    # GERA O PDF E DISPARA
    from app.gerador_relatorios import gerar_pdf_comissao
    
    conn = get_db_connection()
    usuario = conn.execute("SELECT nome, telefone FROM usuarios WHERE id = ?", (usuario_id,)).fetchone()
    
    if usuario and usuario['telefone']:
        dados_recibo = {
            'nome_vendedor': usuario['nome'],
            'valor_vendas': valor_total,
            'percentual': percentual_aplicado,
            'valor_pago': recibo['valor_pago'],
            'data_hora': recibo['data'],
            'hash_assinatura': recibo['hash_assinatura'],
            'assinatura_img': assinatura_admin_base64
        }
        
        # Gera o PDF
        pdf_path = gerar_pdf_comissao(dados_recibo)
        
        msg_whatsapp = (
            f"🧾 *RECIBO DIGITAL DE COMISSÃO*\n\n"
            f"Olá, {usuario['nome']}!\n"
            f"O pagamento da sua comissão foi processado.\n\n"
            f"🔸 *Base de Cálculo:* R$ {formatar_moeda_br(valor_total)}\n"
            f"🔸 *Percentual Aplicado:* {str(percentual_aplicado).replace('.', ',')}%\n"
            f"💰 *VALOR RECEBIDO:* R$ {formatar_moeda_br(recibo['valor_pago'])}\n\n"
            f"O comprovante detalhado e assinado segue em anexo."
        )
        
        # Insere a mensagem de texto
        conn.execute("INSERT INTO fila_whatsapp (numero_destino, mensagem, status) VALUES (?, ?, 'Pendente')", (usuario['telefone'], msg_whatsapp))
        
        # Insere o arquivo na fila de mídia
        try:
            conn.execute("INSERT INTO fila_whatsapp_midia (numero_destino, caminho_arquivo, legenda, status) VALUES (?, ?, ?, 'Pendente')", 
                         (usuario['telefone'], pdf_path, "Recibo Comissão.pdf"))
        except sqlite3.OperationalError:
            # Fallback caso a tabela não exista, cria e insere
            conn.execute("CREATE TABLE IF NOT EXISTS fila_whatsapp_midia (id INTEGER PRIMARY KEY AUTOINCREMENT, numero_destino TEXT, caminho_arquivo TEXT, legenda TEXT, status TEXT, data_criacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
            conn.execute("INSERT INTO fila_whatsapp_midia (numero_destino, caminho_arquivo, legenda, status) VALUES (?, ?, ?, 'Pendente')", 
                         (usuario['telefone'], pdf_path, "Recibo Comissão.pdf"))
            
        conn.commit()
        
    conn.close()
    
    return jsonify({
        "erro": False, 
        "mensagem": "Comissão paga, PDF gerado com assinatura e enviado via WhatsApp!"
    })

# ==============================================================
# PDV / VITRINE E GESTÃO FINANCEIRA
# ==============================================================

@main_bp.route('/pdv', endpoint='pdv_vitrine')
@login_required
def pdv_vitrine():
    conn_est = get_estoque_db_connection()
    produtos = conn_est.execute("SELECT * FROM produtos WHERE status = 'Ativo' AND quantidade > 0 ORDER BY descricao ASC").fetchall()
    conn_est.close()
    
    conn = get_db_connection()
    clientes = conn.execute("SELECT id, nome FROM clientes ORDER BY nome ASC").fetchall()
    profissionais = conn.execute("SELECT id, nome FROM profissionais WHERE status = 'Ativo' ORDER BY nome ASC").fetchall()
    
    # Busca os vendedores que recebem comissão
    vendedores = conn.execute("SELECT id, nome FROM usuarios WHERE status = 'Ativo'").fetchall()
    
    conn.close()
    
    return render_template('vitrine.html', produtos=produtos, clientes=clientes, profissionais=profissionais, vendedores=vendedores)

@main_bp.route('/pdv/finalizar', methods=['POST'])
@login_required
def pdv_finalizar():
    dados = request.get_json()
    cliente_id = dados.get('cliente_id')
    vendedor_id = dados.get('vendedor_id')
    pagamento = dados.get('pagamento')
    itens = dados.get('itens')
    
    if not itens:
        return jsonify({"erro": True, "mensagem": "Carrinho vazio!"})
        
    valor_total = sum(float(item['valor']) * int(item['quantidade']) for item in itens)
    
    conn = get_db_connection()
    conn_est = get_estoque_db_connection()
    
    cursor = conn.cursor()
    # O campo vendedor_id salva o ID do usuário (sistema de perfis/comissão)
    cursor.execute("INSERT INTO vendas (cliente_id, vendedor_id, valor_total, forma_pagamento) VALUES (?, ?, ?, ?)", 
                   (cliente_id if cliente_id != '0' else None, vendedor_id if vendedor_id != '0' else None, valor_total, pagamento))
    venda_id = cursor.lastrowid
    
    for item in itens:
        qtd = int(item['quantidade'])
        vlr = float(item['valor'])
        tot = qtd * vlr
        
        prod = conn_est.execute("SELECT quantidade FROM produtos WHERE codigo = ?", (item['codigo'],)).fetchone()
        if prod:
            nova_qtd = prod['quantidade'] - qtd
            conn_est.execute("UPDATE produtos SET quantidade = ? WHERE codigo = ?", (nova_qtd, item['codigo']))
            conn_est.execute("INSERT INTO historico_estoque (produto_codigo, tipo, quantidade_movimentada, quantidade_saldo, observacoes) VALUES (?, 'Venda', ?, ?, ?)", 
                             (item['codigo'], qtd, nova_qtd, f"Venda PDV #{venda_id}"))
        
        conn.execute("INSERT INTO vendas_itens (venda_id, produto_codigo, descricao, quantidade, valor_unitario, total_item) VALUES (?, ?, ?, ?, ?, ?)",
                     (venda_id, item['codigo'], item['descricao'], qtd, vlr, tot))
                     
    obs_caixa = f"Venda PDV #{venda_id} ({len(itens)} itens)"
    conn.execute("INSERT INTO fluxo_caixa (tipo, valor, forma_pagamento, observacoes) VALUES ('Entrada', ?, ?, ?)", (valor_total, pagamento, obs_caixa))
    
    conn.commit()
    conn.close()
    conn_est.commit()
    conn_est.close()
    
    return jsonify({"erro": False, "venda_id": venda_id})

@main_bp.route('/pdv/imprimir/<int:venda_id>')
@login_required
def pdv_imprimir(venda_id):
    conn = get_db_connection()
    # Modificado para puxar o nome do vendedor da tabela `usuarios`
    venda_row = conn.execute("""
        SELECT v.*, 
               c.nome as cliente_nome, 
               u.nome as vendedor_nome 
        FROM vendas v 
        LEFT JOIN clientes c ON v.cliente_id = c.id 
        LEFT JOIN usuarios u ON v.vendedor_id = u.id 
        WHERE v.id = ?
    """, (venda_id,)).fetchone()
    
    if not venda_row:
        conn.close()
        return "Venda não encontrada", 404
        
    venda = dict(venda_row)
    venda['cliente_nome'] = venda['cliente_nome'] if venda['cliente_nome'] else "Não informado"
    venda['vendedor_nome'] = venda['vendedor_nome'] if venda['vendedor_nome'] else "Não informado"
    
    dt_obj = datetime.datetime.strptime(venda['data_hora'], "%Y-%m-%d %H:%M:%S")
    venda['data_fmt'] = dt_obj.strftime("%d/%m/%Y")
    venda['hora_fmt'] = dt_obj.strftime("%H:%M:%S")
    
    itens = conn.execute("SELECT * FROM vendas_itens WHERE venda_id = ?", (venda_id,)).fetchall()
    conn.close()
    
    return render_template('cupom_venda.html', venda=venda, itens=itens)

@main_bp.route('/financeiro/contas', endpoint='gestao_financeira')
@login_required
def gestao_financeira():
    conn = get_db_connection()
    agora = datetime.datetime.now()
    hoje_str = agora.strftime("%Y-%m-%d")
    semana_que_vem_str = (agora + datetime.timedelta(days=7)).strftime("%Y-%m-%d")
    
    todas_pagar = conn.execute("SELECT * FROM contas_pagar_receber WHERE tipo = 'Pagar' ORDER BY status DESC, data_vencimento ASC").fetchall()
    todas_receber = conn.execute("SELECT * FROM contas_pagar_receber WHERE tipo = 'Receber' ORDER BY status DESC, data_vencimento ASC").fetchall()
    
    def processar_contas(contas):
        res = []
        for c in contas:
            d = dict(c)
            d['data_vencimento_fmt'] = formatar_data_br(d['data_vencimento'])
            d['data_pagamento_fmt'] = formatar_data_br(d['data_pagamento']) if d['data_pagamento'] else ""
            d['atrasada'] = d['data_vencimento'] < hoje_str and d['status'] == 'Pendente'
            res.append(d)
        return res

    contas_pagar = processar_contas(todas_pagar)
    contas_receber = processar_contas(todas_receber)
    
    total_pagar = conn.execute("SELECT SUM(valor) as total FROM contas_pagar_receber WHERE tipo = 'Pagar' AND status = 'Pendente' AND data_vencimento BETWEEN ? AND ?", ("2000-01-01", semana_que_vem_str)).fetchone()['total'] or 0
    total_receber = conn.execute("SELECT SUM(valor) as total FROM contas_pagar_receber WHERE tipo = 'Receber' AND status = 'Pendente' AND data_vencimento BETWEEN ? AND ?", ("2000-01-01", semana_que_vem_str)).fetchone()['total'] or 0
    
    entradas = conn.execute("SELECT SUM(valor) as t FROM fluxo_caixa WHERE tipo = 'Entrada'").fetchone()['t'] or 0
    saidas = conn.execute("SELECT SUM(valor) as t FROM fluxo_caixa WHERE tipo = 'Saída'").fetchone()['t'] or 0
    saldo_geral = entradas - saidas
    
    resumo = {
        "total_pagar": total_pagar,
        "total_receber": total_receber,
        "saldo_geral": saldo_geral
    }
    conn.close()
    return render_template('gestao_financeira.html', contas_pagar=contas_pagar, contas_receber=contas_receber, resumo=resumo)

@main_bp.route('/financeiro/contas/salvar', methods=['POST'])
@login_required
def salvar_conta():
    tipo = request.form.get('tipo_conta')
    descricao = request.form.get('descricao')
    valor = request.form.get('valor')
    data_vencimento = request.form.get('data_vencimento')
    
    conn = get_db_connection()
    conn.execute("INSERT INTO contas_pagar_receber (tipo, descricao, valor, data_vencimento) VALUES (?, ?, ?, ?)",
                 (tipo, descricao, valor, data_vencimento))
    conn.commit()
    conn.close()
    return redirect(url_for('main.gestao_financeira'))

@main_bp.route('/financeiro/contas/baixar', methods=['POST'])
@login_required
def baixar_conta():
    conta_id = request.form.get('conta_id')
    tipo = request.form.get('tipo')
    forma_pagamento = request.form.get('forma_pagamento')
    
    conn = get_db_connection()
    conta = conn.execute("SELECT * FROM contas_pagar_receber WHERE id = ?", (conta_id,)).fetchone()
    if not conta:
        conn.close()
        return jsonify({"erro": True, "mensagem": "Conta não encontrada"})
        
    agora = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    novo_status = 'Pago' if tipo == 'Pagar' else 'Recebido'
    
    conn.execute("UPDATE contas_pagar_receber SET status = ?, data_pagamento = ?, forma_pagamento = ? WHERE id = ?", 
                 (novo_status, agora, forma_pagamento, conta_id))
                 
    tipo_caixa = 'Saída' if tipo == 'Pagar' else 'Entrada'
    obs = f"Baixa de Conta a {tipo}: {conta['descricao']}"
    conn.execute("INSERT INTO fluxo_caixa (tipo, valor, forma_pagamento, observacoes) VALUES (?, ?, ?, ?)",
                 (tipo_caixa, conta['valor'], forma_pagamento, obs))
    
    conn.commit()
    conn.close()
    return jsonify({"erro": False, "mensagem": "Baixa realizada e caixa atualizado!"})