@echo off
echo ========================================================
echo       GERADOR DE VERSAO PARA O CLIENTE - SMELL CLINIC
echo ========================================================
echo.

:: 1. Compilando o Python (Flask)
echo [1/5] Empacotando o sistema web com PyInstaller...
pyinstaller --name "SmellClinic" --onedir --add-data "app/templates;app/templates" --add-data "app/static;app/static" --icon="app\static\img\favicon.ico" run.py -y
echo OK!
echo.

:: 2. Compilando o Node.js (Bot)
echo [2/5] Empacotando o bot do WhatsApp com pkg...
call npm install -g pkg
call pkg bot.js --targets node18-win-x64 --output bot.exe
echo OK!
echo.

:: 3. Preparando a pasta final
echo [3/5] Criando a pasta final do cliente...
set PASTA_ENTREGA=SmellClinic_VersaoFinal
if exist "%PASTA_ENTREGA%" rmdir /s /q "%PASTA_ENTREGA%"
mkdir "%PASTA_ENTREGA%"
echo OK!
echo.

:: 4. Juntando os arquivos
echo [4/5] Copiando arquivos e bancos de dados...
:: Copia a pasta compilada do Python
xcopy "dist\SmellClinic\*" "%PASTA_ENTREGA%\" /e /i /h /y

:: Copia o executável do Bot
copy /y "bot.exe" "%PASTA_ENTREGA%\"

:: Copia os bancos de dados
if exist "smell_clinic_spa.db" copy /y "smell_clinic_spa.db" "%PASTA_ENTREGA%\"
if exist "smell_estoque.db" copy /y "smell_estoque.db" "%PASTA_ENTREGA%\"

:: Copia o status do WhatsApp
if exist "whatsapp_status.txt" copy /y "whatsapp_status.txt" "%PASTA_ENTREGA%\"

:: Copia a sessão salva do WhatsApp (se houver, para o cliente nao precisar ler o QR Code de novo se ja tiver lido)
if exist ".wwebjs_auth" xcopy ".wwebjs_auth\*" "%PASTA_ENTREGA%\.wwebjs_auth\" /e /i /h /y
echo OK!
echo.

:: 5. Criando o arquivo de inicialização para o cliente
echo [5/5] Criando arquivo iniciar_sistema.bat para o cliente...
(
echo @echo off
echo echo ========================================================
echo echo               INICIANDO SMELL CLINIC
echo echo ========================================================
echo echo.
echo echo Verificando navegadores internos...
echo call playwright install chromium
echo echo.
echo echo Iniciando o Bot do WhatsApp...
echo start bot.exe
echo echo.
echo echo Iniciando o Sistema Web...
echo SmellClinic.exe
) > "%PASTA_ENTREGA%\iniciar_sistema.bat"
echo OK!
echo.

echo ========================================================
echo SUCESSO! 
echo A pasta "%PASTA_ENTREGA%" foi criada.
echo Entregue esta pasta para o seu cliente!
echo ========================================================
pause