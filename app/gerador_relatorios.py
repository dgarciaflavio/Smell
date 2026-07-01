import os
import datetime
import sys
from playwright.sync_api import sync_playwright

def calcular_metricas_estoque(itens_estoque, vendas_ultimos_90_dias):
    """
    Recebe os itens e o histórico de vendas para montar o relatório solicitado.
    
    itens_estoque: Lista de dicionários com 'id', 'nome', 'quantidade', 'custo'
    vendas_ultimos_90_dias: Dicionário onde a chave é o ID do item e o valor é a qtd total vendida.
    """
    relatorio = []
    
    for item in itens_estoque:
        id_item = item['id']
        qtd_atual = item['quantidade']
        custo = item['custo']
        
        # 1. Valorização de estoque
        valorizacao = qtd_atual * custo
        
        # 2. Consumo Médio Mensal (CMM)
        # Pega as vendas dos últimos 3 meses e divide por 3
        vendas_trimestre = vendas_ultimos_90_dias.get(id_item, 0)
        cmm = vendas_trimestre / 3.0
        
        # 3. Dias de Estoque
        # Se CMM for 0 (não vendeu nada), o estoque dura "infinito"
        dias_estoque = "Sem saída recente"
        if cmm > 0:
            consumo_diario = cmm / 30.0
            dias_estoque = round(qtd_atual / consumo_diario, 0)
            
        relatorio.append({
            'nome': item['nome'],
            'quantidade': qtd_atual,
            'valorizacao': valorizacao,
            'cmm': round(cmm, 2),
            'dias_estoque': dias_estoque
        })
        
    return relatorio


def gerar_pdf_comissao(dados_recibo):
    """
    Gera um PDF de recibo de comissão e retorna o caminho absoluto do arquivo salvo.
    O dicionário dados_recibo deve conter: nome_vendedor, valor_vendas, percentual, valor_pago, data_hora, hash_assinatura, assinatura_img
    """
    
    valor_vendas_str = "{:,.2f}".format(float(dados_recibo.get('valor_vendas', 0))).replace(",", "X").replace(".", ",").replace("X", ".")
    valor_pago_str = "{:,.2f}".format(float(dados_recibo.get('valor_pago', 0))).replace(",", "X").replace(".", ",").replace("X", ".")
    percentual_str = str(dados_recibo.get('percentual', 0)).replace(".", ",")

    html_content = f'''
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <title>Recibo de Comissão</title>
        <style>
            body {{ font-family: 'Arial', sans-serif; color: #333; padding: 40px; line-height: 1.6; }}
            .header {{ text-align: center; border-bottom: 2px solid #10b981; padding-bottom: 20px; margin-bottom: 30px; }}
            .header h1 {{ color: #10b981; margin: 0; text-transform: uppercase; font-size: 24px; }}
            .header p {{ margin: 5px 0 0 0; color: #666; }}
            .content {{ margin-bottom: 40px; }}
            .destaque {{ font-size: 18px; font-weight: bold; background: #ecfdf5; padding: 15px; border-radius: 8px; border-left: 4px solid #10b981; text-align: center; }}
            .detalhes-table {{ width: 100%; border-collapse: collapse; margin-top: 30px; }}
            .detalhes-table th, .detalhes-table td {{ border: 1px solid #ddd; padding: 12px; text-align: left; }}
            .detalhes-table th {{ background-color: #f8fafc; width: 40%; color: #475569; }}
            .signature-area {{ text-align: center; margin-top: 80px; }}
            .signature-area img {{ max-width: 250px; max-height: 100px; border-bottom: 1px solid #333; margin-bottom: 10px; }}
            .hash {{ font-size: 10px; color: #94a3b8; text-align: center; margin-top: 40px; word-break: break-all; background: #f1f5f9; padding: 10px; border-radius: 4px; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>Recibo de Pagamento</h1>
            <p>Comissão sobre Vendas - Smell CLINIC | SPA</p>
        </div>
        
        <div class="content">
            <p>Pelo presente instrumento, o sistema registra o recebimento do valor detalhado abaixo pelo(a) colaborador(a) <strong>{dados_recibo.get('nome_vendedor', '')}</strong>.</p>
            
            <div class="destaque">
                VALOR RECEBIDO: R$ {valor_pago_str}
            </div>
            
            <table class="detalhes-table">
                <tr>
                    <th>Base de Cálculo (Total em Vendas)</th>
                    <td>R$ {valor_vendas_str}</td>
                </tr>
                <tr>
                    <th>Percentual de Comissão Aplicado</th>
                    <td>{percentual_str}%</td>
                </tr>
                <tr>
                    <th>Data e Hora do Fechamento</th>
                    <td>{dados_recibo.get('data_hora', '')}</td>
                </tr>
            </table>
        </div>
        
        <div class="signature-area">
            <img src="{dados_recibo.get('assinatura_img', '')}" alt="Assinatura Eletrônica">
            <p><strong>{dados_recibo.get('nome_vendedor', '')}</strong></p>
            <p style="font-size: 12px; color: #64748b; margin-top: 0;">Assinatura Digital Capturada em Tela</p>
        </div>
        
        <div class="hash">
            <strong>Chave de Autenticidade (Hash SHA-256):</strong><br>
            {dados_recibo.get('hash_assinatura', '')}
        </div>
    </body>
    </html>
    '''

    # Caminho base adaptado para o executável ou script Python
    base_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    
    # Cria pasta de recibos se não existir
    pdfs_dir = os.path.join(base_dir, 'recibos_comissao')
    os.makedirs(pdfs_dir, exist_ok=True)
    
    # Nomeação única baseada no timestamp
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    pdf_filename = f"recibo_comissao_{timestamp}.pdf"
    pdf_path = os.path.join(pdfs_dir, pdf_filename)
    
    # Geração do PDF utilizando Playwright de forma síncrona
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html_content)
        page.pdf(path=pdf_path, format="A4", print_background=True)
        browser.close()
        
    return pdf_path