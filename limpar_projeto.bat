@echo off
chcp 65001 >nul
color 0C

echo ========================================================================
echo        ATENCAO: SCRIPT DE LIMPEZA PROFUNDA (CRIAR BACKUP VIRGEM)
echo ========================================================================
echo.
echo  Este script apagará definitivamente:
echo  - O Banco de Dados (smell_clinic_spa.db)
echo  - A pasta do Node.js (node_modules)
echo  - Todas as fotos de evolucao dos clientes
echo  - As sessoes autenticadas do WhatsApp
echo.
echo  O sistema voltara ao estado original de fabrica.
echo.
echo  Se voce clicou sem querer, feche esta janela ou aperte CTRL+C.
echo  Para continuar e apagar tudo,
pause

echo.
echo Iniciando a varredura e limpeza...
echo.

:: 1. Apagando Pastas Pesadas e de Sessão
echo [1/4] Removendo bibliotecas (node_modules)...
IF EXIST "node_modules" rmdir /s /q "node_modules"

echo [2/4] Removendo sessoes e cache do WhatsApp...
IF EXIST "whatsapp_node_session" rmdir /s /q "whatsapp_node_session"
IF EXIST ".wwebjs_cache" rmdir /s /q ".wwebjs_cache"
IF EXIST "whatsapp_session_data" rmdir /s /q "whatsapp_session_data"

echo [3/4] Removendo pastas de midia e arquivos temporarios do Python...
IF EXIST "smell_fotos" rmdir /s /q "smell_fotos"
IF EXIST "app\__pycache__" rmdir /s /q "app\__pycache__"
IF EXIST "__pycache__" rmdir /s /q "__pycache__"

:: 2. Apagando Arquivos Soltos (Banco de dados e Imagens)
echo [4/4] Deletando Banco de Dados e arquivos residuais...
IF EXIST "smell_clinic_spa.db" del /q /f "smell_clinic_spa.db"
IF EXIST "qr_code.png" del /q /f "qr_code.png"
IF EXIST "whatsapp_status.txt" del /q /f "whatsapp_status.txt"

color 0A
echo.
echo ========================================================================
echo  LIMPEZA CONCLUIDA COM SUCESSO!
echo  O seu projeto agora tem apenas os codigos fonte (Megabytes).
echo  Pode compactar (ZIP) e guardar no seu OneDrive/Nuvem com seguranca.
echo ========================================================================
echo.
pause