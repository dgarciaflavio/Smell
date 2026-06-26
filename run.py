from app import create_app
from app.database import init_db

# Cria a instância da aplicação Flask utilizando o Application Factory
app = create_app()

if __name__ == '__main__':
    # FASE 1: Garante a integridade do banco de dados local na inicialização
    # Cria o arquivo 'smell_clinic_spa.db' e as 8 tabelas caso não existam
    init_db()
    
    # FASE 1 / Tópico 1: Inicializa o servidor web local do Flask
    # host='0.0.0.0' expõe o servidor para a rede interna da clínica
    # port=5000 é a porta padrão que o motor UDP/ZeroConf usará para descoberta
    print("==========================================================================")
    print(" Iniciando o Servidor Central da Smell CLINIC | SPA...                    ")
    print(" O sistema está pronto para receber conexões da rede local.               ")
    print("==========================================================================")
    
    app.run(host='0.0.0.0', port=5000, debug=True)