const { Client, LocalAuth } = require('whatsapp-web.js');
const sqlite3 = require('sqlite3').verbose();
const QRCode = require('qrcode');
const fs = require('fs');
const path = require('path');

// Verifica se está rodando como .exe (pkg) ou como script normal
const pastaReal = process.pkg ? path.dirname(process.execPath) : __dirname;
const dbPath = path.join(pastaReal, 'smell_clinic_spa.db');
const db = new sqlite3.Database(dbPath);

console.log('==========================================================================');
console.log(' Iniciando Motor do WhatsApp (Node.js - Injeção Direta e Invisível)       ');
console.log('==========================================================================');

fs.writeFileSync('whatsapp_status.txt', 'INICIANDO');

// ==========================================================================
// INTELIGÊNCIA DE AUTO-BUSCA DO NAVEGADOR
// ==========================================================================
const caminhosNavegador = [
    'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Users\\' + process.env.USERNAME + '\\AppData\\Local\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
    'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe'
];

let navegadorEncontrado = null;
for (let caminho of caminhosNavegador) {
    if (fs.existsSync(caminho)) {
        navegadorEncontrado = caminho;
        console.log('[SISTEMA] Navegador compatível encontrado em: ' + caminho);
        break;
    }
}

if (!navegadorEncontrado) {
    console.log('[ERRO CRÍTICO] Não encontrei o Google Chrome nem o Microsoft Edge no seu computador.');
    fs.writeFileSync('whatsapp_status.txt', 'ERRO DE NAVEGADOR');
    process.exit(1);
}
// ==========================================================================

const client = new Client({
    authStrategy: new LocalAuth({ dataPath: 'whatsapp_node_session' }),
    puppeteer: {
        executablePath: navegadorEncontrado, // Usa o navegador que o robô achou sozinho!
        headless: true,
        args: [
            '--no-sandbox', 
            '--disable-setuid-sandbox', 
            '--disable-dev-shm-usage',
            '--disable-gpu'
        ]
    },
    webVersionCache: {
        type: 'remote',
        remotePath: 'https://raw.githubusercontent.com/wppconnect-team/wa-version/main/html/2.2412.54.html',
    }
});

client.on('qr', (qr) => {
    console.log('Gerando QR Code seguro para o sistema Flask...');
    QRCode.toFile('qr_code.png', qr, {
        color: {
            dark: '#077626',
            light: '#FFFFFF'
        }
    }, function (err) {
        if (err) throw err;
        console.log('QR Code salvo! Pode escanear pela tela de Configurações do sistema.');
        fs.writeFileSync('whatsapp_status.txt', 'AGUARDANDO QR CODE');
    });
});

client.on('ready', () => {
    console.log('Motor Conectado e Pronto para enviar mensagens!');
    fs.writeFileSync('whatsapp_status.txt', 'CONECTADO');
    if (fs.existsSync('qr_code.png')) {
        fs.unlinkSync('qr_code.png');
    }

    // Loop de checagem da fila de mensagens (executa a cada 3 segundos)
    setInterval(() => {
        db.all("SELECT id, numero_destino, mensagem FROM fila_whatsapp WHERE status = 'Pendente'", [], (err, rows) => {
            if (err) return;

            rows.forEach((msg) => {
                let numero = String(msg.numero_destino).replace(/\D/g, '');
                
                // Garante o DDI 55 caso a inteligência do Flask não tenha pego
                if (numero.length <= 11) numero = "55" + numero;
                
                const chatId = numero + "@c.us";
                console.log(`Enviando mensagem instantânea para ${numero}...`);

                client.sendMessage(chatId, msg.mensagem).then(response => {
                    console.log(`Mensagem enviada com sucesso para ${numero}!`);
                    db.run("UPDATE fila_whatsapp SET status = 'Enviado' WHERE id = ?", [msg.id]);
                }).catch(err => {
                    console.log(`Falha ao enviar para ${numero}. O número não possui WhatsApp.`);
                    db.run("UPDATE fila_whatsapp SET status = 'Erro' WHERE id = ?", [msg.id]);
                });
            });
        });
    }, 3000);
});

client.on('disconnected', (reason) => {
    console.log('WhatsApp Desconectado:', reason);
    fs.writeFileSync('whatsapp_status.txt', 'DESCONECTADO');
});

// Desligamento Seguro
process.on('SIGINT', async () => {
    console.log('\nDesligando o motor do WhatsApp de forma segura...');
    fs.writeFileSync('whatsapp_status.txt', 'DESLIGADO');
    await client.destroy();
    process.exit(0);
});

client.initialize();