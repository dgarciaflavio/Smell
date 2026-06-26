from flask import Flask
import os

def create_app():
    """
    Função Fábrica (Application Factory) responsável por inicializar o servidor Flask,
    configurar os parâmetros de segurança e registrar os módulos de rotas.
    """
    app = Flask(__name__)
    
    # Chave secreta essencial para criptografia de sessões locais e mensagens de alerta (Flash)
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'smell_clinic_spa_chave_secreta_2026')

    # Inicializa o banco de dados de estoque assim que o app for criado
    from app.estoque_database import init_estoque_db
    init_estoque_db()

    # Importação tardia do Blueprint para evitar problemas de importação cíclica
    from app.routes import main_bp
    
    # Registra as rotas operacionais no servidor central do sistema
    app.register_blueprint(main_bp)

    return app