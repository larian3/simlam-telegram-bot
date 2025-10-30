from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.helpers import escape_markdown
from simlam_scraper import buscar_processo
import logging
import os
import json
import threading
import asyncio
import hashlib
from flask import Flask
from datetime import time
import pytz

# Configuração de logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("BOT_TOKEN")

# Arquivos para persistência
MONITORED_PROCESSES_FILE = 'monitored_processes.json'
PROCESS_STATES_FILE = 'process_states.json'

# Lock para acesso concorrente aos arquivos JSON
json_lock = threading.Lock()

# --- Flask App ---
# This is a minimal web server to keep the bot alive on free hosting platforms.
flask_app = Flask(__name__)

@flask_app.route('/health')
def health_check():
    return "OK", 200

def run_flask():
    # Use a port assigned by the hosting platform, or 8080 as a default.
    port = int(os.environ.get('PORT', 8080))
    flask_app.run(host='0.0.0.0', port=port)

# --- Bot Logic ---
def load_data(filename):
    """Carrega dados de um arquivo JSON de forma segura."""
    with json_lock:
        if not os.path.exists(filename):
            return {}
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            # Se o arquivo estiver corrompido ou não for encontrado, retorna um dicionário vazio.
            return {}

def save_data(data, filename):
    """Salva dados em um arquivo JSON de forma segura."""
    with json_lock:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    start_message = (
        "Olá\\! 👋 Eu sou o bot do SIMLAM\\.\n\n"
        "Envie um número de processo para uma consulta rápida ou use os comandos abaixo:\n\n"
        "*COMANDOS DISPONÍVEIS:*\n\n"
        "🔹 `/monitorar <proc1>, <proc2>`\n"
        "Para receber atualizações sobre um ou mais processos\\.\n\n"
        "🔹 `/desmonitorar <proc1>, <proc2>`\n"
        "Para parar de receber atualizações de um ou mais processos\\.\n\n"
        "🔹 `/status <proc1>, <proc2>`\n"
        "Verifica o status atual de processos já monitorados\\.\n\n"
        "🔹 `/listar`\n"
        "Mostra todos os seus processos monitorados\\.\n\n"
        "_Dica: Para os comandos `/monitorar`, `/desmonitorar` e `/status`, você pode enviar vários números de uma vez, separados por vírgula\\._"
    )
    await update.effective_message.reply_text(start_message, parse_mode='MarkdownV2')

async def consultar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    numero = update.effective_message.text.strip().strip('<>')
    if not numero.replace('/', '').isdigit() or not numero:
        await update.effective_message.reply_text("Por favor, envie um número de processo válido.")
        return

    await update.effective_message.reply_text(f"🔎 Buscando informações do processo {numero}, aguarde...")
    # Roda a função síncrona em uma thread separada para não bloquear o bot
    resultado_data = await asyncio.to_thread(buscar_processo, numero)
    # Escapa caracteres de Markdown para evitar erros de formatação
    resultado_escapado = escape_markdown(resultado_data.get('details', 'Não foi possível obter detalhes.'), version=2)
    await update.effective_message.reply_text(resultado_escapado, parse_mode='MarkdownV2')

async def monitorar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Adiciona um ou mais processos à lista de monitoramento do usuário."""
    chat_id = str(update.effective_chat.id)
    if not context.args:
        await update.effective_message.reply_text("Uso: /monitorar <processo1>, <processo2>, ...")
        return

    numeros_str = " ".join(context.args)
    numeros_processo = [num.strip('<>').strip() for num in numeros_str.split(',') if num.strip()]

    if not numeros_processo:
        await update.effective_message.reply_text("Por favor, forneça ao menos um número de processo válido.")
        return
        
    await update.effective_message.reply_text("Processando {} número(s)...".format(len(numeros_processo)))

    monitored = load_data(MONITORED_PROCESSES_FILE)
    if chat_id not in monitored:
        monitored[chat_id] = []
    
    adicionados = []
    ja_monitorados = []
    erros = []

    states = load_data(PROCESS_STATES_FILE)

    for numero in numeros_processo:
        if not numero.replace('/', '').isdigit() or not numero:
            erros.append(f"{numero} (inválido)")
            continue

        if numero not in monitored[chat_id]:
            monitored[chat_id].append(numero)
            adicionados.append(numero)
            
            # Busca o estado inicial para o novo processo
            if numero not in states:
                try:
                    resultado_data = await asyncio.to_thread(buscar_processo, numero)
                    # Armazena o timestamp inicial, se existir
                    if resultado_data.get('timestamp'):
                        states[numero] = resultado_data['timestamp']
                except Exception as e:
                    logger.error(f"Falha ao buscar estado inicial para {numero}: {e}")
                    erros.append(f"{numero} (falha ao buscar)")
        else:
            ja_monitorados.append(numero)
    
    if adicionados:
        save_data(monitored, MONITORED_PROCESSES_FILE)
        save_data(states, PROCESS_STATES_FILE)

    # Monta a mensagem de resumo
    reply_parts = []
    if adicionados:
        reply_parts.append(f"✅ Processos monitorados: {', '.join(adicionados)}")
    if ja_monitorados:
        reply_parts.append(f"ℹ️ Já estavam monitorados: {', '.join(ja_monitorados)}")
    if erros:
        reply_parts.append(f"⚠️ Erros: {', '.join(erros)}")
    
    if reply_parts:
        await update.effective_message.reply_text("\n".join(reply_parts))

async def desmonitorar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove um ou mais processos da lista de monitoramento."""
    chat_id = str(update.effective_chat.id)
    if not context.args:
        await update.effective_message.reply_text("Uso: /desmonitorar <processo1>, <processo2>, ...")
        return

    numeros_str = " ".join(context.args)
    numeros_processo = [num.strip('<>').strip() for num in numeros_str.split(',') if num.strip()]

    if not numeros_processo:
        await update.effective_message.reply_text("Por favor, forneça ao menos um número de processo.")
        return

    monitored = load_data(MONITORED_PROCESSES_FILE)
    removidos = []
    nao_encontrados = []

    if chat_id in monitored:
        for numero in numeros_processo:
            if numero in monitored[chat_id]:
                monitored[chat_id].remove(numero)
                removidos.append(numero)
            else:
                nao_encontrados.append(numero)
        
        if not monitored[chat_id]:
            del monitored[chat_id]
        
        if removidos:
            save_data(monitored, MONITORED_PROCESSES_FILE)
    else:
        nao_encontrados.extend(numeros_processo)

    # Monta a mensagem de resumo
    reply_parts = []
    if removidos:
        reply_parts.append(f"❌ Processos removidos: {', '.join(removidos)}")
    if nao_encontrados:
        reply_parts.append(f"ℹ️ Não estavam na lista: {', '.join(nao_encontrados)}")

    if reply_parts:
        await update.effective_message.reply_text("\n".join(reply_parts))
    else:
        await update.effective_message.reply_text("Você não está monitorando nenhum processo.")

async def listar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lista os processos monitorados pelo usuário."""
    chat_id = str(update.effective_chat.id)
    monitored = load_data(MONITORED_PROCESSES_FILE)
    
    if chat_id in monitored and monitored[chat_id]:
        lista = "\n".join([f"- {p}" for p in monitored[chat_id]])
        await update.effective_message.reply_text(f"Você está monitorando os seguintes processos:\n{lista}")
    else:
        await update.effective_message.reply_text("Você não está monitorando nenhum processo.")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Verifica o status atual de um ou mais processos monitorados, sob demanda."""
    chat_id = str(update.effective_chat.id)
    if not context.args:
        await update.effective_message.reply_text("Uso: /status <processo1>, <processo2>, ...")
        return

    numeros_str = " ".join(context.args)
    numeros_processo = [num.strip('<>').strip() for num in numeros_str.split(',') if num.strip()]

    if not numeros_processo:
        await update.effective_message.reply_text("Por favor, forneça ao menos um número de processo.")
        return

    await update.effective_message.reply_text(f"🔎 Verificando status de {len(numeros_processo)} processo(s), aguarde...")

    monitored = load_data(MONITORED_PROCESSES_FILE)
    states = load_data(PROCESS_STATES_FILE)

    for numero in numeros_processo:
        numero_escapado = escape_markdown(numero, version=2)
        if not numero.replace('/', '').isdigit() or not numero:
            await update.effective_message.reply_text(f"⚠️ O número de processo '{numero_escapado}' é inválido\\.", parse_mode='MarkdownV2')
            continue

        if chat_id not in monitored or numero not in monitored[chat_id]:
            await update.effective_message.reply_text(f"❌ Você não está monitorando o processo {numero_escapado}\\. Use /monitorar para adicioná-lo\\.", parse_mode='MarkdownV2')
            continue

        await update.effective_message.reply_text(f"🔎 Verificando o status atual do processo {numero}, aguarde...")
        
        try:
            resultado_data = await asyncio.to_thread(buscar_processo, numero)
            current_details = resultado_data.get('details')
            current_timestamp = resultado_data.get('timestamp')
            
            # Se não houver detalhes, mostra o erro retornado pelo scraper
            if not current_details:
                 await update.effective_message.reply_text(f"⚠️ Não foi possível obter detalhes para o processo {numero_escapado}\\. Motivo: Nenhum detalhe retornado\\.", parse_mode='MarkdownV2')
                 return

            last_timestamp = states.get(numero)
            
            estado_escapado = escape_markdown(current_details, version=2)
            
            message_header = f"*Situação atual do processo {numero_escapado}:*\n\n"
            message_body = f"{estado_escapado}"
            
            # Compara timestamps para determinar se houve atualização
            if last_timestamp == current_timestamp and current_timestamp is not None:
                update_info = "\n\n*Status:* Sem novas atualizações desde a última verificação automática\\."
            elif current_timestamp is None:
                 update_info = "\n\n*Status:* Não foi possível determinar o status de atualização \\(sem data de tramitação\\)\\."
            else:
                update_info = "\n\n*Status:* 📢 *Houve uma atualização desde a última verificação automática\\!*"

            full_message = message_header + message_body + update_info
            await update.effective_message.reply_text(full_message, parse_mode='MarkdownV2')
        except Exception as e:
            logger.error(f"Erro ao verificar o status do processo {numero} no comando /status: {e}", exc_info=True)
            await update.effective_message.reply_text(f"⚠️ Ocorreu um erro ao verificar o processo {numero_escapado}\\. Tente novamente mais tarde\\.", parse_mode='MarkdownV2')


async def check_updates(context: ContextTypes.DEFAULT_TYPE):
    """Verifica periodicamente por atualizações nos processos monitorados."""
    logger.info("Executando verificação de atualizações...")
    monitored = load_data(MONITORED_PROCESSES_FILE)
    states = load_data(PROCESS_STATES_FILE)
    
    all_processes = set()
    for processes in monitored.values():
        all_processes.update(processes)

    for numero in all_processes:
        try:
            logger.info(f"Verificando processo: {numero}")
            
            # Roda a função síncrona que agora retorna um dict
            resultado_data = await asyncio.to_thread(buscar_processo, numero)
            current_timestamp = resultado_data.get('timestamp')
            current_details = resultado_data.get('details')

            # Pula se o scraper falhou em obter um timestamp (evita falsos positivos)
            if not current_timestamp:
                logger.warning(f"Não foi possível obter um timestamp para o processo {numero}. Detalhes: {current_details}")
                continue
            
            last_timestamp = states.get(numero)

            subscribers = [chat_id for chat_id, procs in monitored.items() if numero in procs]
            if not subscribers:
                continue

            # Se o timestamp for diferente, houve uma atualização real.
            if last_timestamp != current_timestamp:
                logger.info(f"Atualização encontrada para o processo {numero}!")
                states[numero] = current_timestamp
                save_data(states, PROCESS_STATES_FILE)
                
                numero_escapado = escape_markdown(numero, version=2)
                estado_escapado = escape_markdown(current_details, version=2)
                message = f"📢 *Nova atualização no processo {numero_escapado}!*\n\n{estado_escapado}"
                
                for chat_id in subscribers:
                    try:
                        await context.bot.send_message(chat_id=chat_id, text=message, parse_mode='MarkdownV2')
                    except Exception as e:
                        logger.error(f"Falha ao enviar mensagem de atualização para {chat_id} no processo {numero}: {e}")
            else:
                logger.info(f"Processo {numero} sem atualizações.")

        except Exception as e:
            logger.error(f"Falha CRÍTICA ao verificar o processo {numero}: {e}", exc_info=True)
            # Continua para o próximo processo em caso de erro
            continue


    logger.info("Verificação de atualizações concluída.")


def main():
    if not TOKEN:
        print("Erro: BOT_TOKEN não foi configurado como variável de ambiente.")
        return

    # Run Flask app in a separate thread
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()

    app = ApplicationBuilder().token(TOKEN).build()
    job_queue = app.job_queue

    # Adiciona os handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("monitorar", monitorar))
    app.add_handler(CommandHandler("desmonitorar", desmonitorar))
    app.add_handler(CommandHandler("listar", listar))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, consultar))

    # Define o fuso horário de São Paulo
    tz = pytz.timezone('America/Sao_Paulo')

    # Agenda as verificações para horários específicos
    job_queue.run_daily(check_updates, time=time(hour=9, minute=0, tzinfo=tz), job_kwargs={'misfire_grace_time': 3600})
    job_queue.run_daily(check_updates, time=time(hour=12, minute=0, tzinfo=tz), job_kwargs={'misfire_grace_time': 3600})
    job_queue.run_daily(check_updates, time=time(hour=15, minute=0, tzinfo=tz), job_kwargs={'misfire_grace_time': 3600})
    job_queue.run_daily(check_updates, time=time(hour=18, minute=0, tzinfo=tz), job_kwargs={'misfire_grace_time': 3600})


    print("Bot rodando...")
    app.run_polling()

if __name__ == "__main__":
    main()
