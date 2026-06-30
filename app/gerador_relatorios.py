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