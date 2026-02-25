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
import random
from sqlalchemy import select, insert, delete, update, func
import threading
from typing import Optional, List
import time as _time

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
        "Olá\\! Eu sou o bot do SIMLAM\\.\n\n"
        "Utilize os comandos abaixo para gerenciar seus monitoramentos:\n\n"
        "*PROCESSOS:*\n"
        "\\- `/monitorar <num>` \\- Monitorar processo\n"
        "\\- `/status <num>` \\- Status do processo\n"
        "\\- `/desmonitorar <num>` \\- Parar de monitorar\n\n"
        "*DOCUMENTOS:*\n"
        "\\- `/monitorar-doc <num>` \\- Monitorar documento\n"
        "\\- `/status-doc <num>` \\- Status do documento\n"
        "\\- `/desmonitorar-doc <num>` \\- Parar de monitorar\n\n"
        "*GERAL:*\n"
        "\\- `/listar` \\- Ver tudo que você monitora\n\n"
        "_Dica: Você pode enviar vários números separados por vírgula nos comandos\\._"
    )
    await update.effective_message.reply_text(start_message, parse_mode='MarkdownV2')

async def consultar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Responde a mensagens que não são comandos."""
    await update.effective_message.reply_text(
        "[!] Por favor, utilize os comandos para interagir\\.\n"
        "Exemplo: `/monitorar <numero>` ou `/monitorar-doc <numero>`\\.\n"
        "Use `/start` para ver a lista completa\\.",
        parse_mode='MarkdownV2'
    )

# --- COMANDOS DE PROCESSO ---

async def monitorar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Adiciona PROCESSOS à lista de monitoramento."""
    await _generic_monitorar(update, context, is_document=False)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Verifica status de PROCESSOS."""
    await _generic_status(update, context, is_document=False)

async def desmonitorar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove PROCESSOS da lista."""
    await _generic_desmonitorar(update, context, is_document=False)

# --- COMANDOS DE DOCUMENTO ---

async def monitorar_doc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Adiciona DOCUMENTOS à lista de monitoramento."""
    await _generic_monitorar(update, context, is_document=True)

async def status_doc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Verifica status de DOCUMENTOS."""
    await _generic_status(update, context, is_document=True)

async def desmonitorar_doc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove DOCUMENTOS da lista."""
    await _generic_desmonitorar(update, context, is_document=True)

# --- LÓGICA GENÉRICA COMPARTILHADA ---

async def _generic_monitorar(update: Update, context: ContextTypes.DEFAULT_TYPE, is_document: bool):
    chat_id = str(update.effective_chat.id)
    tipo_label = "Documento" if is_document else "Processo"
    cmd_exemplo = "/monitorar-doc" if is_document else "/monitorar"
    
    if not context.args:
        await update.effective_message.reply_text(f"Uso: {cmd_exemplo} <numero1>, <numero2>...")
        return

    numeros_str = " ".join(context.args)
    numeros_input = [num.strip('<>').strip() for num in numeros_str.split(',') if num.strip()]

    if not numeros_input:
        await update.effective_message.reply_text("Por favor, forneça ao menos um número válido.")
        return
        
    await update.effective_message.reply_text(f"Processando {len(numeros_input)} {tipo_label}(s)...")

    adicionados = []
    ja_monitorados = []
    erros = []

    db = SessionLocal()
    try:
        # Busca o que o usuário já monitora
        query = select(group_subscriptions.c.process_number).where(group_subscriptions.c.chat_id == chat_id)
        user_monitored_set = {row[0] for row in db.execute(query)}

        for numero in numeros_input:
            if not numero.replace('/', '').isdigit() or not numero:
                erros.append(f"{numero} (inválido)")
                continue

            # Define o ID de armazenamento (Documentos ganham prefixo DOC:)
            storage_id = f"DOC:{numero}" if is_document else numero

            if storage_id in user_monitored_set:
                ja_monitorados.append(numero)
                continue

            # Verifica se já existe globalmente
            query_global = select(monitored_processes.c.process_number).where(monitored_processes.c.process_number == storage_id)
            exists_global = db.execute(query_global).first()
            
            resultado_data = None
            
            if exists_global:
                # Já existe, apenas busca o estado atual para mostrar ao usuário
                search_type = "documento" if is_document else "processo"
                resultado_data = await asyncio.to_thread(buscar_processo, numero, search_type)
            else:
                # Novo item global. Busca para validar e pegar timestamp inicial.
                search_type = "documento" if is_document else "processo"
                resultado_data = await asyncio.to_thread(buscar_processo, numero, search_type)
                
                # Validação: Se não retornou timestamp ou detalhes válidos, assume erro/inexistente
                label_resumo = f"Resumo do {tipo_label}"
                if not (resultado_data.get('timestamp') or label_resumo in resultado_data.get('details', '')):
                    erros.append(f"{numero} (não encontrado/erro)")
                    continue

                # Insere na lista global
                stmt_global = insert(monitored_processes).values(process_number=storage_id)
                db.execute(stmt_global)

            # Insere na lista do usuário
            stmt_sub = insert(group_subscriptions).values(chat_id=chat_id, process_number=storage_id)
            db.execute(stmt_sub)
            
            # Salva estado inicial se tiver timestamp
            if resultado_data and resultado_data.get('timestamp'):
                state_query = select(process_states).where(process_states.c.process_number == storage_id)
                if not db.execute(state_query).first():
                    state_stmt = insert(process_states).values(process_number=storage_id, last_timestamp=resultado_data.get('timestamp'))
                    db.execute(state_stmt)
            
            # Resposta de sucesso
            numero_escapado = escape_markdown(numero.replace('-', '\\-'), version=2)
            details = resultado_data.get('details', 'Detalhes indisponíveis.')
            details_escapado = escape_markdown(details, version=2)
            
            message = (
                f"[OK] {tipo_label} {numero_escapado} agora está sendo monitorado\\.\n\n"
                f"*Situação atual:*\n{details_escapado}"
            )
            await update.effective_message.reply_text(message, parse_mode='MarkdownV2')
            adicionados.append(numero)
        
        db.commit()

    except Exception as e:
        logger.error(f"Erro DB em monitorar ({tipo_label}): {e}", exc_info=True)
        db.rollback()
        await update.effective_message.reply_text("Erro ao processar. Tente novamente.")
        return
    finally:
        db.close()

    # Resumo final
    reply_parts = []
    if ja_monitorados:
        reply_parts.append(f"[i] Já monitorados: {', '.join(ja_monitorados)}")
    if erros:
        reply_parts.append(f"[!] Erros/Não encontrados: {', '.join(erros)}")
    
    if reply_parts:
        await update.effective_message.reply_text("\n".join(reply_parts))


async def _generic_status(update: Update, context: ContextTypes.DEFAULT_TYPE, is_document: bool):
    chat_id = str(update.effective_chat.id)
    tipo_label = "Documento" if is_document else "Processo"
    cmd_exemplo = "/status-doc" if is_document else "/status"

    if not context.args:
        await update.effective_message.reply_text(f"Uso: {cmd_exemplo} <numero1>, <numero2>...")
        return

    numeros_str = " ".join(context.args)
    numeros_input = [num.strip('<>').strip() for num in numeros_str.split(',') if num.strip()]

    if not numeros_input:
        await update.effective_message.reply_text("Forneça ao menos um número.")
        return

    await update.effective_message.reply_text(f"[...] Verificando status de {len(numeros_input)} {tipo_label}(s)...")
    
    db = SessionLocal()
    try:
        # Verifica quais desses o usuário realmente monitora
        sub_query = select(group_subscriptions.c.process_number).where(group_subscriptions.c.chat_id == chat_id)
        user_subs = {row[0] for row in db.execute(sub_query)}
        
        # Prepara lista de IDs de armazenamento para buscar estados
        storage_ids = []
        for num in numeros_input:
            s_id = f"DOC:{num}" if is_document else num
            if s_id in user_subs:
                storage_ids.append(s_id)
        
        # Busca estados salvos
        process_states_map = {}
        if storage_ids:
            states_query = select(process_states).where(process_states.c.process_number.in_(storage_ids))
            process_states_map = {row.process_number: row.last_timestamp for row in db.execute(states_query)}

        for numero in numeros_input:
            storage_id = f"DOC:{numero}" if is_document else numero
            numero_escapado = escape_markdown(numero.replace('-', '\\-'), version=2)

            if storage_id not in user_subs:
                await update.effective_message.reply_text(f"[X] Você não está monitorando o {tipo_label} {numero_escapado}\\.", parse_mode='MarkdownV2')
                continue

            try:
                search_type = "documento" if is_document else "processo"
                resultado_data = await asyncio.to_thread(buscar_processo, numero, search_type)
                
                current_details = resultado_data.get('details')
                current_timestamp = resultado_data.get('timestamp')
                
                if not current_details:
                    await update.effective_message.reply_text(f"[!] Falha ao obter detalhes de {numero_escapado}\\.", parse_mode='MarkdownV2')
                    continue

                last_timestamp = process_states_map.get(storage_id)
                estado_escapado = escape_markdown(current_details, version=2)
                
                message_header = f"*Situação atual do {tipo_label} {numero_escapado}:*\n\n"
                message_body = f"{estado_escapado}"
                
                update_info = ""
                if last_timestamp == current_timestamp and current_timestamp is not None:
                    update_info = "\n\n*Status:* Sem novas atualizações desde a última verificação automática\\."
                elif current_timestamp is None:
                    update_info = "\n\n*Status:* Não foi possível determinar o status de atualização\\."
                else:
                    update_info = "\n\n*Status:* [NOVO] *Houve uma atualização desde a última verificação automática\\!*"

                await update.effective_message.reply_text(message_header + message_body + update_info, parse_mode='MarkdownV2')

            except Exception as e:
                logger.error(f"Erro status {numero}: {e}", exc_info=True)
                await update.effective_message.reply_text(f"[!] Erro ao verificar {numero_escapado}\\.", parse_mode='MarkdownV2')
    finally:
        db.close()


async def _generic_desmonitorar(update: Update, context: ContextTypes.DEFAULT_TYPE, is_document: bool):
    chat_id = str(update.effective_chat.id)
    tipo_label = "Documento" if is_document else "Processo"
    cmd_exemplo = "/desmonitorar-doc" if is_document else "/desmonitorar"

    if not context.args:
        await update.effective_message.reply_text(f"Uso: {cmd_exemplo} <numero1>, <numero2>...")
        return

    numeros_str = " ".join(context.args)
    numeros_input = [num.strip('<>').strip() for num in numeros_str.split(',') if num.strip()]

    if not numeros_input:
        await update.effective_message.reply_text("Forneça ao menos um número.")
        return
    
    db = SessionLocal()
    try:
        # Mapeia inputs para IDs de armazenamento
        to_remove = []
        for num in numeros_input:
            s_id = f"DOC:{num}" if is_document else num
            to_remove.append(s_id)
        
        # Verifica o que realmente existe na lista do usuário antes de tentar deletar
        # (Opcional, mas ajuda a dar feedback preciso)
        query_check = select(group_subscriptions.c.process_number).where(
            group_subscriptions.c.chat_id == chat_id,
            group_subscriptions.c.process_number.in_(to_remove)
        )
        existing_in_db = {row[0] for row in db.execute(query_check)}
        
        if not existing_in_db:
             await update.effective_message.reply_text(f"Nenhum dos números informados estava na sua lista de {tipo_label}s.")
             return

        # 1. Remove as inscrições deste chat
        stmt_delete_sub = delete(group_subscriptions).where(
            group_subscriptions.c.chat_id == chat_id,
            group_subscriptions.c.process_number.in_(existing_in_db)
        )
        db.execute(stmt_delete_sub)
        
        # 2. Verifica quais itens ficaram "órfãos" e limpa globalmente
        for stored_num in existing_in_db:
            query_refs = select(func.count()).select_from(group_subscriptions).where(group_subscriptions.c.process_number == stored_num)
            count = db.execute(query_refs).scalar()
            
            if count == 0:
                stmt_delete_global = delete(monitored_processes).where(monitored_processes.c.process_number == stored_num)
                db.execute(stmt_delete_global)
        
        db.commit()
        
        removidos_count = len(existing_in_db)
        nao_encontrados_count = len(numeros_input) - removidos_count

        reply_parts = []
        if removidos_count > 0:
            reply_parts.append(f"[X] {removidos_count} {tipo_label}(s) removido(s).")
        if nao_encontrados_count > 0:
            reply_parts.append(f"[i] {nao_encontrados_count} não estavam na lista.")

        await update.effective_message.reply_text("\n".join(reply_parts))

    except Exception as e:
        logger.error(f"Erro desmonitorar: {e}", exc_info=True)
        db.rollback()
        await update.effective_message.reply_text("Erro ao remover. Tente novamente.")
    finally:
        db.close()


async def fetch_process_for_list(stored_number: str) -> str:
    """Busca um processo e retorna uma string formatada para o comando /listar."""
    try:
        is_doc = stored_number.startswith("DOC:")
        search_type = "documento" if is_doc else "processo"
        clean_num = stored_number.replace("DOC:", "")
        
        resultado_data = await asyncio.to_thread(buscar_processo, clean_num, search_type)
        details = resultado_data.get('details', '')
        
        # Tenta extrair título/descrição
        lines = details.split('\n')
        desc = "(Sem descrição)"
        if len(lines) > 1:
            # Pega a segunda linha (Empreendimento ou Interessado)
            desc = lines[1].strip()
            
        prefix = "[DOC] " if is_doc else "[PROC] "
        return f"- {prefix}{clean_num} - {desc}"
    except Exception:
        return f"- {stored_number} - (Erro ao buscar detalhes)"

async def listar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lista os processos monitorados pelo chat."""
    chat_id = str(update.effective_chat.id)
    db = SessionLocal()
    try:
        query = select(group_subscriptions.c.process_number).where(group_subscriptions.c.chat_id == chat_id).order_by(group_subscriptions.c.process_number)
        user_processes = [row[0] for row in db.execute(query)]
        
        if user_processes:
            await update.effective_message.reply_text(f"Buscando detalhes de {len(user_processes)} item(ns), isso pode levar um momento...")
            
            # Cria e executa as tarefas de busca em paralelo
            tasks = [fetch_process_for_list(p) for p in user_processes]
            results = await asyncio.gather(*tasks)
            
            lista = "\n".join(results)
            await update.effective_message.reply_text(f"Você está monitorando:\n{lista}")
        else:
            await update.effective_message.reply_text("Você não está monitorando nenhum item.")
    finally:
        db.close()

async def check_single_process(stored_number: str, context: ContextTypes.DEFAULT_TYPE):
    """Lógica para verificar um único item e notificar os assinantes."""
    try:
        logger.info(f"Verificando item: {stored_number}")
        
        is_doc = stored_number.startswith("DOC:")
        search_type = "documento" if is_doc else "processo"
        clean_num = stored_number.replace("DOC:", "")

        # IMPORTANTE: DB é síncrono.
        def _db_get_last_timestamp(p_num: str) -> Optional[str]:
            db = SessionLocal()
            try:
                state_query = select(process_states.c.last_timestamp).where(process_states.c.process_number == p_num)
                return db.execute(state_query).scalar_one_or_none()
            finally:
                db.close()

        last_timestamp_result = await asyncio.to_thread(_db_get_last_timestamp, stored_number)

        resultado_data = None
        for attempt in range(1, 4):
            resultado_data = await asyncio.to_thread(buscar_processo, clean_num, search_type)
            current_timestamp = resultado_data.get('timestamp')
            if current_timestamp:
                break
            if attempt < 3:
                logger.warning(f"Tentativa {attempt}/3 falhou para {stored_number}. Tentando de novo em 5s...")
                await asyncio.sleep(5)
            else:
                logger.error(f"Falha ao obter timestamp para {stored_number} após 3 tentativas.")
                return 

        current_timestamp = resultado_data.get('timestamp')
        current_details = resultado_data.get('details')

        if not current_timestamp:
            return

        if last_timestamp_result != current_timestamp:
            logger.info(f"Atualização encontrada para {stored_number}!")

            def _db_upsert_timestamp_and_get_subscribers(p_num: str, ts: str) -> List[str]:
                db = SessionLocal()
                try:
                    if last_timestamp_result is None:
                        db.execute(insert(process_states).values(process_number=p_num, last_timestamp=ts))
                    else:
                        update_stmt = (
                            update(process_states)
                            .where(process_states.c.process_number == p_num)
                            .values(last_timestamp=ts)
                        )
                        db.execute(update_stmt)

                    db.commit()

                    subscribers_query = select(group_subscriptions.c.chat_id).where(group_subscriptions.c.process_number == p_num)
                    return [row[0] for row in db.execute(subscribers_query)]
                except Exception:
                    db.rollback()
                    raise
                finally:
                    db.close()

            subscribers = await asyncio.to_thread(_db_upsert_timestamp_and_get_subscribers, stored_number, current_timestamp)

            numero_escapado = escape_markdown(clean_num.replace('-', '\\-'), version=2)
            estado_escapado = escape_markdown(current_details, version=2)
            tipo_label = "Documento" if is_doc else "Processo"
            message = f"[NOVO] *Nova atualização no {tipo_label} {numero_escapado}\\!*\n\n{estado_escapado}"
            
            for chat_id in subscribers:
                try:
                    await context.bot.send_message(chat_id=chat_id, text=message, parse_mode='MarkdownV2')
                except Exception as e:
                    logger.error(f"Falha ao enviar mensagem de atualização para {chat_id}: {e}")
        else:
            logger.info(f"Item {stored_number} sem atualizações.")

    except Exception as e:
        logger.error(f"Falha CRÍTICA ao verificar {stored_number}: {e}", exc_info=True)
    finally:
        pass

async def check_updates(context: ContextTypes.DEFAULT_TYPE):
    """Verifica periodicamente por atualizações nos processos monitorados."""
    manual_jitter = random.uniform(0, 120)
    await asyncio.sleep(manual_jitter)

    logger.info("Executando verificação de atualizações...")
    
    def _db_get_all_monitored_processes() -> List[str]:
        db = SessionLocal()
        try:
            all_monitored_query = select(monitored_processes.c.process_number)
            return [row[0] for row in db.execute(all_monitored_query)]
        finally:
            db.close()

    processes_to_check = await asyncio.to_thread(_db_get_all_monitored_processes)

    if not processes_to_check:
        logger.info("Nenhum item sendo monitorado. Verificação concluída.")
        return

    semaphore = asyncio.Semaphore(4)

    async def check_with_semaphore(numero):
        async with semaphore:
            await check_single_process(numero, context)
            await asyncio.sleep(random.uniform(5, 15))

    tasks = [check_with_semaphore(numero) for numero in processes_to_check]
    await asyncio.gather(*tasks)

    logger.info(f"Verificação de {len(processes_to_check)} itens concluída.")


def main():
    if not TOKEN:
        print("Erro: BOT_TOKEN não foi configurado como variável de ambiente.")
        return
    if not DATABASE_URL:
        print("Erro: DATABASE_URL não foi configurado como variável de ambiente.")
        return

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    def _init_db_with_retries() -> None:
        max_attempts = int(os.getenv("DB_INIT_MAX_ATTEMPTS", "5"))
        base_sleep = float(os.getenv("DB_INIT_BASE_SLEEP", "2"))
        for attempt in range(1, max_attempts + 1):
            try:
                init_db()
                logger.info("Banco de dados inicializado com sucesso.")
                return
            except Exception as e:
                logger.error(f"Falha ao inicializar o DB (tentativa {attempt}/{max_attempts}): {e}", exc_info=True)
                if attempt < max_attempts:
                    _time.sleep(base_sleep * attempt)
        logger.error("DB continua indisponível. O bot seguirá ativo, mas comandos que usam DB podem falhar até o DB voltar.")

    threading.Thread(target=_init_db_with_retries, daemon=True).start()

    async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.error("Exceção não tratada durante o processamento de um update", exc_info=context.error)

    app = ApplicationBuilder().token(TOKEN).build()
    job_queue = app.job_queue

    # Handlers de Processo
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("monitorar", monitorar))
    app.add_handler(CommandHandler("desmonitorar", desmonitorar))
    app.add_handler(CommandHandler("status", status))
    
    # Handlers de Documento
    app.add_handler(CommandHandler("monitorar-doc", monitorar_doc))
    app.add_handler(CommandHandler("monitorar_doc", monitorar_doc)) # Alias com underscore
    app.add_handler(CommandHandler("desmonitorar-doc", desmonitorar_doc))
    app.add_handler(CommandHandler("desmonitorar_doc", desmonitorar_doc)) # Alias
    app.add_handler(CommandHandler("status-doc", status_doc))
    app.add_handler(CommandHandler("status_doc", status_doc)) # Alias

    app.add_handler(CommandHandler("listar", listar))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, consultar))
    app.add_error_handler(on_error)

    job_queue.run_repeating(
        check_updates,
        interval=2400,
        first=10,
        name="check_updates",
        job_kwargs={"max_instances": 1, "coalesce": True, "misfire_grace_time": 900},
    )

    print("Bot rodando...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
