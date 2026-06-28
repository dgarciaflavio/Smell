@echo off
echo ========================================================
echo       GERADOR DE VERSAO PARA O CLIENTE - SMELL CLINIC
echo ========================================================
echo.

:: 0. Limpeza total de arquivos antigos
echo [0/3] Limpando pastas antigas (build, dist, versao)...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "SmellClinic_VersaoFinal" rmdir /s /q "SmellClinic_VersaoFinal"
if exist "SmellClinic.spec" del /f /q "SmellClinic.spec"
echo OK!
echo.

:: 1. Compilando o Python (Flask)
echo [1/3] Empacotando o sistema web com PyInstaller (Invisivel)...
pyinstaller --name "SmellClinic" --onedir --noconsole --add-data "app/templates;app/templates" --add-data "app/static;app/static" --icon="app\static\img\favicon.ico" run.py -y
echo OK!
echo.

:: 2. Preparando a pasta final
echo [2/3] Criando a pasta final do cliente...
set PASTA_ENTREGA=SmellClinic_VersaoFinal
mkdir "%PASTA_ENTREGA%"
echo OK!
echo.

:: 3. Juntando os arquivos
echo [3/3] Copiando arquivos e modulos do Node.js...

:: Copia a pasta compilada do Python
xcopy "dist\SmellClinic\*" "%PASTA_ENTREGA%\" /e /i /h /y

:: Copia os arquivos essenciais do Bot
copy /y "bot.js" "%PASTA_ENTREGA%\"
copy /y "package.json" "%PASTA_ENTREGA%\"

:: Copia a pasta node_modules inteira
echo Copiando node_modules (isso pode levar alguns segundos)...
xcopy "node_modules\*" "%PASTA_ENTREGA%\node_modules\" /e /i /h /y

:: Copia os bancos de dados
if exist "smell_clinic_spa.db" copy /y "smell_clinic_spa.db" "%PASTA_ENTREGA%\"
if exist "smell_estoque.db" copy /y "smell_estoque.db" "%PASTA_ENTREGA%\"

echo OK!
echo.

echo ========================================================
echo SUCESSO! 
echo A pasta "%PASTA_ENTREGA%" foi criada e esta pronta!
echo ========================================================
pause