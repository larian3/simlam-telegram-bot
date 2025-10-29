from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from simlam_scraper import buscar_processo
import logging
import os
import json
import threading
from flask import Flask

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

# --- Flask App ---
# This is a minimal web server to keep the bot alive on free hosting platforms.
flask_app = Flask(__name__)

@flask_app.route('/')
def index():
    return "Bot is running!"

def run_flask():
    # Use a port assigned by the hosting platform, or 8080 as a default.
    port = int(os.environ.get('PORT', 8080))
    flask_app.run(host='0.0.0.0', port=port)

# --- Bot Logic ---
def load_data(filename):
    """Carrega dados de um arquivo JSON."""
    if not os.path.exists(filename):
        return {}
    with open(filename, 'r') as f:
        return json.load(f)

def save_data(data, filename):
    """Salva dados em um arquivo JSON."""
    with open(filename, 'w') as f:
        json.dump(data, f, indent=4)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "Olá! 👋 Eu sou o bot do SIMLAM.\n"
        "Envie o número do processo para consultar.\n\n"
        "Use os comandos:\n"
        "/monitorar <numero> - Para receber atualizações sobre um processo.\n"
        "/desmonitorar <numero> - Para parar de receber atualizações.\n"
        "/listar - Para ver seus processos monitorados."
    )

async def consultar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    numero = update.effective_message.text.strip().strip('<>')
    if not numero.replace('/', '').isdigit() or not numero:
        await update.effective_message.reply_text("Por favor, envie um número de processo válido.")
        return

    await update.effective_message.reply_text(f"🔎 Buscando informações do processo {numero}, aguarde...")
    resultado = buscar_processo(numero)
    # Telegram usa MarkdownV2 por padrão em algumas versões, vamos escapar caracteres
    await update.effective_message.reply_text(resultado, parse_mode='Markdown')

async def monitorar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Adiciona um processo à lista de monitoramento do usuário."""
    chat_id = str(update.effective_chat.id)
    try:
        numero = context.args[0].strip('<>')
        if not numero.replace('/', '').isdigit() or not numero:
            await update.effective_message.reply_text("Uso: /monitorar <numero_do_processo>\nO número do processo deve conter apenas dígitos e, opcionalmente, uma barra '/'.")
            return
    except (IndexError, ValueError):
        await update.effective_message.reply_text("Uso: /monitorar <numero_do_processo>")
        return

    monitored = load_data(MONITORED_PROCESSES_FILE)
    if chat_id not in monitored:
        monitored[chat_id] = []
    
    if numero not in monitored[chat_id]:
        monitored[chat_id].append(numero)
        save_data(monitored, MONITORED_PROCESSES_FILE)
        await update.effective_message.reply_text(f"✅ Processo {numero} agora está sendo monitorado. Você será notificado sobre atualizações.")
        # Adiciona ao estado inicial se não existir e envia o status atual
        states = load_data(PROCESS_STATES_FILE)
        resultado = buscar_processo(numero)
        if numero not in states:
            states[numero] = resultado # Armazena o texto completo como estado inicial
            save_data(states, PROCESS_STATES_FILE)
        
        await update.effective_message.reply_text(f"*Situação atual do processo {numero}:*\n\n{resultado}", parse_mode='Markdown')
    else:
        await update.effective_message.reply_text(f"ℹ️ O processo {numero} já está na sua lista de monitoramento.")

async def desmonitorar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove um processo da lista de monitoramento."""
    chat_id = str(update.effective_chat.id)
    try:
        numero = context.args[0].strip('<>')
    except IndexError:
        await update.effective_message.reply_text("Uso: /desmonitorar <numero_do_processo>")
        return

    monitored = load_data(MONITORED_PROCESSES_FILE)
    if chat_id in monitored and numero in monitored[chat_id]:
        monitored[chat_id].remove(numero)
        if not monitored[chat_id]: # Se a lista ficar vazia
            del monitored[chat_id]
        save_data(monitored, MONITORED_PROCESSES_FILE)
        await update.effective_message.reply_text(f"❌ Processo {numero} removido da sua lista de monitoramento.")
    else:
        await update.effective_message.reply_text(f"ℹ️ O processo {numero} não está na sua lista de monitoramento.")

async def listar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lista os processos monitorados pelo usuário."""
    chat_id = str(update.effective_chat.id)
    monitored = load_data(MONITORED_PROCESSES_FILE)
    
    if chat_id in monitored and monitored[chat_id]:
        lista = "\n".join([f"- {p}" for p in monitored[chat_id]])
        await update.effective_message.reply_text(f"Você está monitorando os seguintes processos:\n{lista}")
    else:
        await update.effective_message.reply_text("Você não está monitorando nenhum processo.")

async def check_updates(context: ContextTypes.DEFAULT_TYPE):
    """Verifica periodicamente por atualizações nos processos monitorados."""
    logger.info("Executando verificação de atualizações...")
    monitored = load_data(MONITORED_PROCESSES_FILE)
    states = load_data(PROCESS_STATES_FILE)
    
    all_processes = set()
    for processes in monitored.values():
        all_processes.update(processes)

    for numero in all_processes:
        logger.info(f"Verificando processo: {numero}")
        current_state = buscar_processo(numero)
        last_state = states.get(numero)

        subscribers = [chat_id for chat_id, procs in monitored.items() if numero in procs]
        if not subscribers:
            continue

        # Caso 1: Houve uma atualização
        if current_state and last_state != current_state and current_state.startswith("📄"):
            logger.info(f"Atualização encontrada para o processo {numero}!")
            states[numero] = current_state
            save_data(states, PROCESS_STATES_FILE)
            
            message = f"📢 *Nova atualização no processo {numero}!*\n\n{current_state}"
            for chat_id in subscribers:
                try:
                    await context.bot.send_message(chat_id=chat_id, text=message, parse_mode='Markdown')
                except Exception as e:
                    logger.error(f"Falha ao enviar mensagem de atualização para {chat_id}: {e}")
        
        # Caso 2: Sem atualizações
        elif current_state and last_state == current_state:
            logger.info(f"Processo {numero} sem atualizações.")
            message = f"ℹ️ Processo {numero}: Sem novas atualizações."
            for chat_id in subscribers:
                try:
                    # Envia uma notificação silenciosa para não incomodar o usuário
                    await context.bot.send_message(chat_id=chat_id, text=message, disable_notification=True)
                except Exception as e:
                    logger.error(f"Falha ao enviar mensagem 'sem atualização' para {chat_id}: {e}")

        # Caso 3: Erro ao buscar o processo
        elif current_state and not current_state.startswith("📄"):
             logger.warning(f"Falha ao buscar processo {numero}. Resposta: {current_state}")
             message = f"⚠️ Não foi possível verificar o processo {numero}. O site pode estar offline ou o processo foi removido.\nErro: {current_state}"
             for chat_id in subscribers:
                try:
                    await context.bot.send_message(chat_id=chat_id, text=message)
                except Exception as e:
                    logger.error(f"Falha ao enviar mensagem de erro para {chat_id}: {e}")


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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, consultar))

    # Agenda o job para rodar a cada 3 horas (10800 segundos)
    job_queue.run_repeating(check_updates, interval=10800, first=10)

    print("Bot rodando...")
    app.run_polling()

if __name__ == "__main__":
    main()
