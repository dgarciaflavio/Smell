import time
import datetime
import sys
import os
import urllib.parse
from playwright.sync_api import sync_playwright

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app.database import get_db_connection

class WhatsappBotWorker:
    def __init__(self):
        self.running = True

    def iniciar_servico_background(self):
        print("==========================================================================")
        print(" Iniciando Motor de Automação do WhatsApp (Playwright)                    ")
        print("==========================================================================")
        
        with sync_playwright() as p:
            user_data_dir = os.path.join(os.getcwd(), 'whatsapp_session_data')
            
            browser_context = p.chromium.launch_persistent_context(
                user_data_dir,
                headless=False,
                viewport={'width': 800, 'height': 600},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            
            page = browser_context.new_page()
            print(f"[{datetime.datetime.now()}] Acessando WhatsApp Web...")
            page.goto("https://web.whatsapp.com", timeout=120000)
            
            while self.running:
                try:
                    qr_canvas = page.locator("canvas")
                    
                    if qr_canvas.count() > 0 and qr_canvas.first.is_visible():
                        qr_canvas.first.screenshot(path="qr_code.png")
                        print(f"[{datetime.datetime.now()}] QR Code real capturado! O site já pode exibir a imagem.")
                        time.sleep(10)
                    else:
                        if page.locator("#pane-side").is_visible():
                            if os.path.exists("qr_code.png"):
                                os.remove("qr_code.png")
                                print(f"[{datetime.datetime.now()}] Login realizado! Monitorando fila de mensagens...")
                            
                            self.processar_fila_pendente(page)
                            
                except Exception as e:
                    pass
                
                time.sleep(3)

    def processar_fila_pendente(self, page):
        conn = get_db_connection()
        cursor = conn.cursor()
        mensagens = cursor.execute("SELECT id, numero_destino, mensagem FROM fila_whatsapp WHERE status = 'Pendente'").fetchall()
        
        for msg in mensagens:
            msg_id = msg['id']
            numero = str(msg['numero_destino'])
            texto = msg['mensagem']
            
            num_formatado = numero.replace("+", "").replace("-", "").replace(" ", "").replace("(", "").replace(")", "")
            if len(num_formatado) <= 11:
                num_formatado = "55" + num_formatado
                
            print(f"[{datetime.datetime.now()}] Preparando envio real para {num_formatado}...")
            
            try:
                texto_url = urllib.parse.quote(texto)
                page.goto(f"https://web.whatsapp.com/send?phone={num_formatado}&text={texto_url}", wait_until="domcontentloaded", timeout=60000)
                
                send_selector = "span[data-icon='send']"
                invalid_selector = "div[role='dialog'] button"
                
                print(f"[{datetime.datetime.now()}] Aguardando o carregamento da conversa...")
                
                tempo_espera = 0
                enviou = False
                
                while tempo_espera < 30:
                    if page.locator(invalid_selector).is_visible():
                        print(f"[{datetime.datetime.now()}] Bloqueio do WhatsApp: O número {num_formatado} não existe.")
                        page.locator(invalid_selector).first.click()
                        cursor.execute("UPDATE fila_whatsapp SET status = 'Erro' WHERE id = ?", (msg_id,))
                        conn.commit()
                        enviou = True
                        break
                        
                    if page.locator(send_selector).is_visible():
                        # O SEGREDO: Aguarda a interface do WhatsApp estabilizar a animação
                        time.sleep(1.5)
                        
                        # AÇÃO 1: Clica com força ignorando camadas invisíveis da Meta
                        page.locator(send_selector).first.click(force=True)
                        
                        # AÇÃO 2 (Fallback Infalível): Dispara o ENTER no teclado
                        time.sleep(0.5)
                        page.keyboard.press("Enter")
                        
                        time.sleep(3) # Aguarda a mensagem subir para a tela
                        cursor.execute("UPDATE fila_whatsapp SET status = 'Enviado' WHERE id = ?", (msg_id,))
                        conn.commit()
                        print(f"[{datetime.datetime.now()}] Mensagem/Recibo enviado com sucesso para {num_formatado}!")
                        enviou = True
                        break
                        
                    time.sleep(1)
                    tempo_espera += 1
                
                if not enviou:
                    print(f"[{datetime.datetime.now()}] Falha de Timeout: A internet ou o WhatsApp demoraram mais de 30s para carregar a conversa.")
                    
            except Exception as e:
                print(f"[{datetime.datetime.now()}] Erro inesperado na automação para {num_formatado}: {str(e)}")
                
        conn.close()

if __name__ == "__main__":
    bot = WhatsappBotWorker()
    bot.iniciar_servico_background()