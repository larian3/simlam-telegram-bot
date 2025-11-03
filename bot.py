from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.helpers import escape_markdown
from simlam_scraper import buscar_processo
import logging
import os
import asyncio
from flask import Flask
from datetime import time
import pytz
from sqlalchemy import select, insert, delete, update, func
import threading

# Importa as configurações do banco de dados
from database import SessionLocal, monitored_processes, process_states, group_subscriptions, init_db


# Configuração de logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL") # Garante que a variável de ambiente do DB seja lida

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
    """Adiciona um ou mais processos à lista de monitoramento do chat."""
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

    adicionados = []
    ja_monitorados = []
    erros = []

    db = SessionLocal()
    try:
        # Busca processos que este chat já monitora
        query = select(group_subscriptions.c.process_number).where(group_subscriptions.c.chat_id == chat_id)
        user_monitored_set = {row[0] for row in db.execute(query)}

        for numero in numeros_processo:
            if not numero.replace('/', '').isdigit() or not numero:
                erros.append(f"{numero} (inválido)")
                continue

            if numero not in user_monitored_set:
                # 1. Adiciona à lista de monitoramento global apenas se não existir
                query_exists = select(monitored_processes).where(monitored_processes.c.process_number == numero)
                exists = db.execute(query_exists).first()
                if not exists:
                    stmt_global = insert(monitored_processes).values(process_number=numero)
                    db.execute(stmt_global)

                # 2. Cria a inscrição para este chat
                stmt_sub = insert(group_subscriptions).values(chat_id=chat_id, process_number=numero)
                db.execute(stmt_sub)
                
                # Busca o estado atual para responder ao usuário e armazena se for novo
                try:
                    resultado_data = await asyncio.to_thread(buscar_processo, numero)
                    
                    # Armazena o timestamp inicial, se o processo ainda não estiver no DB de estados
                    if timestamp := resultado_data.get('timestamp'):
                        state_query = select(process_states).where(process_states.c.process_number == numero)
                        if not db.execute(state_query).first():
                            state_stmt = insert(process_states).values(process_number=numero, last_timestamp=timestamp)
                            db.execute(state_stmt)
                    
                    # Envia a mensagem com o status atual
                    numero_escapado = escape_markdown(numero.replace('-', '\\-'), version=2)
                    details = resultado_data.get('details', 'Não foi possível obter os detalhes do processo no momento.')
                    details_escapado = escape_markdown(details, version=2)
                    
                    message = (
                        f"✅ Processo {numero_escapado} agora está sendo monitorado\\.\n\n"
                        f"*Situação atual:*\n{details_escapado}"
                    )
                    await update.effective_message.reply_text(message, parse_mode='MarkdownV2')
                    adicionados.append(numero)

                except Exception as e:
                    logger.error(f"Falha ao buscar estado inicial para {numero}: {e}")
                    erros.append(f"{numero} (falha ao buscar)")
            else:
                ja_monitorados.append(numero)
        
        db.commit()

    except Exception as e:
        logger.error(f"Erro de banco de dados em /monitorar: {e}", exc_info=True)
        db.rollback()
        await update.effective_message.reply_text("Ocorreu um erro ao processar sua solicitação. Tente novamente.")
        return
    finally:
        db.close()

    # Monta a mensagem de resumo para o que não foi reportado individualmente
    reply_parts = []
    if ja_monitorados:
        reply_parts.append(f"ℹ️ Já estavam monitorados: {', '.join(ja_monitorados)}")
    if erros:
        reply_parts.append(f"⚠️ Erros: {', '.join(erros)}")
    
    if reply_parts:
        await update.effective_message.reply_text("\n".join(reply_parts))

async def desmonitorar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove um ou mais processos da lista de monitoramento do chat."""
    chat_id = str(update.effective_chat.id)
    if not context.args:
        await update.effective_message.reply_text("Uso: /desmonitorar <processo1>, <processo2>, ...")
        return

    numeros_str = " ".join(context.args)
    numeros_processo = [num.strip('<>').strip() for num in numeros_str.split(',') if num.strip()]

    if not numeros_processo:
        await update.effective_message.reply_text("Por favor, forneça ao menos um número de processo.")
        return
    
    db = SessionLocal()
    try:
        # 1. Remove as inscrições deste chat
        stmt_delete_sub = delete(group_subscriptions).where(
            group_subscriptions.c.chat_id == chat_id,
            group_subscriptions.c.process_number.in_(numeros_processo)
        )
        result = db.execute(stmt_delete_sub)
        
        # 2. Verifica quais processos ficaram "órfãos" (sem ninguém monitorando)
        for numero in numeros_processo:
            query_refs = select(func.count()).select_from(group_subscriptions).where(group_subscriptions.c.process_number == numero)
            count = db.execute(query_refs).scalar()
            
            if count == 0:
                # Se ninguém mais monitora, remove da lista global
                stmt_delete_global = delete(monitored_processes).where(monitored_processes.c.process_number == numero)
                db.execute(stmt_delete_global)
        
        db.commit()
        
        removidos_count = result.rowcount
        nao_encontrados_count = len(numeros_processo) - removidos_count

        reply_parts = []
        if removidos_count > 0:
            reply_parts.append(f"❌ {removidos_count} processo(s) removido(s) da sua lista.")
        if nao_encontrados_count > 0:
            reply_parts.append(f"ℹ️ {nao_encontrados_count} processo(s) não estavam na sua lista.")

        if reply_parts:
            await update.effective_message.reply_text("\n".join(reply_parts))
        else:
             await update.effective_message.reply_text("Nenhum dos processos informados estava na sua lista.")

    except Exception as e:
        logger.error(f"Erro de banco de dados em /desmonitorar: {e}", exc_info=True)
        db.rollback()
        await update.effective_message.reply_text("Ocorreu um erro ao remover os processos. Tente novamente.")
    finally:
        db.close()


async def listar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lista os processos monitorados pelo chat."""
    chat_id = str(update.effective_chat.id)
    db = SessionLocal()
    try:
        query = select(group_subscriptions.c.process_number).where(group_subscriptions.c.chat_id == chat_id).order_by(group_subscriptions.c.process_number)
        user_processes = [row[0] for row in db.execute(query)]
        
        if user_processes:
            lista = "\n".join([f"- {p}" for p in user_processes])
            await update.effective_message.reply_text(f"Você está monitorando os seguintes processos:\n{lista}")
        else:
            await update.effective_message.reply_text("Você não está monitorando nenhum processo.")
    finally:
        db.close()

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
    
    db = SessionLocal()
    try:
        # Busca os processos que o usuário monitora para validação
        monitored_query = select(monitored_processes.c.process_number).where(
            monitored_processes.c.process_number.in_(numeros_processo)
        )
        user_monitored_set = {row[0] for row in db.execute(monitored_query)}
        
        # Busca os estados dos processos
        states_query = select(process_states).where(process_states.c.process_number.in_(numeros_processo))
        process_states_map = {row.process_number: row.last_timestamp for row in db.execute(states_query)}

        for numero in numeros_processo:
            numero_escapado = escape_markdown(numero.replace('-', '\\-'), version=2)
            if not numero.replace('/', '').isdigit() or not numero:
                await update.effective_message.reply_text(f"⚠️ O número de processo '{numero_escapado}' é inválido\\.", parse_mode='MarkdownV2')
                continue

            if numero not in user_monitored_set:
                await update.effective_message.reply_text(f"❌ Você não está monitorando o processo {numero_escapado}\\. Use /monitorar para adicioná-lo\\.", parse_mode='MarkdownV2')
                continue

            try:
                resultado_data = await asyncio.to_thread(buscar_processo, numero)
                current_details = resultado_data.get('details')
                current_timestamp = resultado_data.get('timestamp')
                
                if not current_details:
                    await update.effective_message.reply_text(f"⚠️ Não foi possível obter detalhes para o processo {numero_escapado}\\. Motivo: Nenhum detalhe retornado\\.", parse_mode='MarkdownV2')
                    continue

                last_timestamp = process_states_map.get(numero)
                estado_escapado = escape_markdown(current_details, version=2)
                
                message_header = f"*Situação atual do processo {numero_escapado}:*\n\n"
                message_body = f"{estado_escapado}"
                
                if last_timestamp == current_timestamp and current_timestamp is not None:
                    update_info = "\n\n*Status:* Sem novas atualizações desde a última verificação automática\\."
                elif current_timestamp is None:
                    update_info = "\n\n*Status:* Não foi possível determinar o status de atualização \\(sem data de tramitação\\)\\."
                else:
                    update_info = "\n\n*Status:* 📢 *Houve uma atualização desde a última verificação automática\\!*"

                full_message = message_header + message_body + update_info
                await update.effective_message.reply_text(full_message, parse_mode='MarkdownV2')
            except Exception as e:
                logger.error(f"Erro ao verificar o status do processo {numero}: {e}", exc_info=True)
                await update.effective_message.reply_text(f"⚠️ Ocorreu um erro ao verificar o processo {numero_escapado}\\. Tente novamente mais tarde\\.", parse_mode='MarkdownV2')
    finally:
        db.close()

async def check_single_process(numero: str, context: ContextTypes.DEFAULT_TYPE):
    """Lógica para verificar um único processo e notificar os assinantes."""
    db = None
    try:
        logger.info(f"Verificando processo: {numero}")
        
        db = SessionLocal()
        
        state_query = select(process_states.c.last_timestamp).where(process_states.c.process_number == numero)
        last_timestamp_result = db.execute(state_query).scalar_one_or_none()

        resultado_data = None
        for attempt in range(1, 4):
            resultado_data = await asyncio.to_thread(buscar_processo, numero)
            current_timestamp = resultado_data.get('timestamp')
            if current_timestamp:
                break
            if attempt < 3:
                logger.warning(f"Tentativa {attempt}/3 falhou para {numero} (sem timestamp). Detalhes: {resultado_data.get('details')}. Tentando de novo em 5s...")
                await asyncio.sleep(5)
            else:
                logger.error(f"Falha ao obter timestamp para {numero} após 3 tentativas. Detalhes: {resultado_data.get('details')}")
                return # Encerra a verificação para este processo

        current_timestamp = resultado_data.get('timestamp')
        current_details = resultado_data.get('details')

        if not current_timestamp:
            return

        if last_timestamp_result != current_timestamp:
            logger.info(f"Atualização encontrada para o processo {numero}!")

            if last_timestamp_result is None:
                db.execute(insert(process_states).values(process_number=numero, last_timestamp=current_timestamp))
            else:
                update_stmt = update(process_states).where(process_states.c.process_number == numero).values(last_timestamp=current_timestamp)
                db.execute(update_stmt)
            
            db.commit()

            # Busca todos os chats inscritos neste processo
            subscribers_query = select(group_subscriptions.c.chat_id).where(group_subscriptions.c.process_number == numero)
            subscribers = [row[0] for row in db.execute(subscribers_query)]

            numero_escapado = escape_markdown(numero.replace('-', '\\-'), version=2)
            estado_escapado = escape_markdown(current_details, version=2)
            message = f"📢 *Nova atualização no processo {numero_escapado}\\!*\n\n{estado_escapado}"
            
            for chat_id in subscribers:
                try:
                    await context.bot.send_message(chat_id=chat_id, text=message, parse_mode='MarkdownV2')
                except Exception as e:
                    logger.error(f"Falha ao enviar mensagem de atualização para {chat_id} no processo {numero}: {e}")
        else:
            logger.info(f"Processo {numero} sem atualizações.")

    except Exception as e:
        logger.error(f"Falha CRÍTICA ao verificar o processo {numero}: {e}", exc_info=True)
        if db:
            db.rollback()
    finally:
        if db:
            db.close()

async def check_updates(context: ContextTypes.DEFAULT_TYPE):
    """Verifica periodicamente por atualizações nos processos monitorados."""
    logger.info("Executando verificação de atualizações...")
    
    processes_to_check = []
    db = None
    try:
        db = SessionLocal()
        # Busca da lista global de processos
        all_monitored_query = select(monitored_processes.c.process_number)
        processes_to_check = [row[0] for row in db.execute(all_monitored_query)]
    finally:
        if db:
            db.close()

    if not processes_to_check:
        logger.info("Nenhum processo sendo monitorado globalmente. Verificação concluída.")
        return

    # Cria uma tarefa para cada processo para rodar em paralelo
    tasks = [check_single_process(numero, context) for numero in processes_to_check]
    await asyncio.gather(*tasks)

    logger.info("Verificação de atualizações concluída.")


def main():
    if not TOKEN:
        print("Erro: BOT_TOKEN não foi configurado como variável de ambiente.")
        return
    if not DATABASE_URL:
        print("Erro: DATABASE_URL não foi configurado como variável de ambiente.")
        return

    # Inicializa o banco de dados (cria tabelas se necessário)
    init_db()

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

    # Agenda a verificação para rodar a cada 15 minutos (900 segundos)
    # A primeira verificação acontece 10 segundos após o bot iniciar.
    job_queue.run_repeating(check_updates, interval=900, first=10)


    print("Bot rodando...")
    app.run_polling()

if __name__ == "__main__":
    main()
