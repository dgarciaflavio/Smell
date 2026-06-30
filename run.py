from app import create_app
from app.database import init_db
import os
import sys
import threading
import time
import webbrowser
import pystray
from pystray import MenuItem as item
from PIL import Image
from waitress import serve

app = create_app()

def get_resource_dir():
    """Busca o ícone e arquivos de interface dentro da pasta oculta do PyInstaller"""
    if getattr(sys, 'frozen', False):
        return sys._MEIPASS
    return os.path.abspath(os.path.dirname(__file__))

def get_exe_dir():
    """Busca os arquivos externos que ficam soltos na pasta do cliente (DB, status.txt)"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.abspath(os.path.dirname(__file__))

def start_flask():
    init_db()
    serve(app, host='0.0.0.0', port=5000)

def abrir_navegador(icon, item):
    webbrowser.open("http://127.0.0.1:5000")

def sair_sistema(icon, item):
    icon.stop()
    os._exit(0)

def monitorar_status_whatsapp(icon):
    status_path = os.path.join(get_exe_dir(), 'whatsapp_status.txt')
    icon.visible = True
    while icon.visible:
        texto_hover = "Smell CLINIC | Servidor ON"
        if os.path.exists(status_path):
            with open(status_path, 'r', encoding='utf-8') as f:
                status = f.read().strip()
                if status == 'CONECTADO':
                    texto_hover = "Smell CLINIC | WhatsApp Conectado!"
                else:
                    texto_hover = f"Smell CLINIC | WhatsApp: {status}"
        
        icon.title = texto_hover
        time.sleep(3)

if __name__ == '__main__':
    flask_thread = threading.Thread(target=start_flask, daemon=True)
    flask_thread.start()

    # Agora ele encontra o ícone com precisão milimétrica
    caminho_icone = os.path.join(get_resource_dir(), 'app', 'static', 'img', 'favicon.ico')
    try:
        image = Image.open(caminho_icone)
    except Exception:
        image = Image.new('RGB', (64, 64), color=(0, 128, 0))

    menu = pystray.Menu(
        item('Abrir Sistema', abrir_navegador, default=True),
        item('Desligar Servidor', sair_sistema)
    )

    icon = pystray.Icon("SmellClinic", image, "Smell CLINIC | Iniciando...", menu)

    monitor_thread = threading.Thread(target=monitorar_status_whatsapp, args=(icon,), daemon=True)
    monitor_thread.start()

    icon.run()