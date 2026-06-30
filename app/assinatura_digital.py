import hashlib
from datetime import datetime

def gerar_hash_assinatura(cpf_recebedor, valor, data_hora=None):
    """
    Gera uma assinatura digital única para o comprovante de pagamento da comissão.
    Usa o CPF do vendedor, o valor e o timestamp exato da transação.
    """
    if not data_hora:
        data_hora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
    # Concatena os dados de forma estruturada
    string_base = f"REC:{cpf_recebedor}|VAL:{valor}|DAT:{data_hora}"
    
    # Gera o Hash SHA-256
    hash_gerado = hashlib.sha256(string_base.encode('utf-8')).hexdigest()
    
    return hash_gerado, data_hora

def validar_assinatura(cpf_recebedor, valor, data_hora, hash_fornecido):
    """
    Função útil caso no futuro você precise auditar se um recibo é autêntico.
    """
    string_base = f"REC:{cpf_recebedor}|VAL:{valor}|DAT:{data_hora}"
    hash_esperado = hashlib.sha256(string_base.encode('utf-8')).hexdigest()
    
    return hash_esperado == hash_fornecido