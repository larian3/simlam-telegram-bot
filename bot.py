from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.helpers import escape_markdown
from simlam_scraper import buscar_processo
import logging
import os
import asyncio
from flask import Flask
from datetime import datetime, time as dt_time, timedelta, timezone
import pytz
import random  # Adicionar import
from sqlalchemy import select, insert, delete, update, func
import threading
from typing import Optional, List, Dict, Any, Tuple
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

# --- Monitoramento adaptativo ---
# last_timestamp (process_states) = última tramitação no SIMLAM.
# last_checked_at (monitored_processes) = última consulta feita pelo bot.
TZ_BR = pytz.timezone("America/Sao_Paulo")
ACTIVE_WINDOW = timedelta(days=30)
FREQUENT_INTERVAL_SECONDS = 2400  # ~40 minutos
# python-telegram-bot v20+: days usa cron (0=domingo ... 6=sábado)
LOW_FREQUENCY_CRON_DAYS = (1, 4)  # segunda e quinta
LOW_FREQUENCY_TIME = dt_time(9, 0, tzinfo=TZ_BR)
MONITOR_FREQUENT = "frequent"
MONITOR_LOW = "low_frequency"


def parse_movement_datetime(ts: Optional[str]) -> Optional[datetime]:
    """Converte o timestamp de tramitação do SIMLAM (ex.: 28/08/2026 10:00:00)."""
    if not ts:
        return None
    ts_clean = str(ts).strip()
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            dt = datetime.strptime(ts_clean, fmt)
            return TZ_BR.localize(dt)
        except ValueError:
            continue
    return None


def is_frequent_process(
    last_timestamp: Optional[str],
    first_monitored_at: Optional[datetime] = None,
    now: Optional[datetime] = None,
) -> bool:
    """
    Processo ativo (consulta a cada ~40 min) se:
    - ainda não tem data de tramitação; OU
    - a última movimentação ocorreu há no máximo 30 dias (inclusive); OU
    - foi cadastrado no monitoramento há no máximo 30 dias.
    """
    now = now or datetime.now(TZ_BR)
    if now.tzinfo is None:
        now = TZ_BR.localize(now)

    parsed = parse_movement_datetime(last_timestamp)
    if parsed is None:
        return True
    if (now - parsed) <= ACTIVE_WINDOW:
        return True

    if first_monitored_at is not None:
        first = first_monitored_at
        if first.tzinfo is None:
            first = first.replace(tzinfo=timezone.utc)
        first = first.astimezone(TZ_BR)
        if (now - first) <= ACTIVE_WINDOW:
            return True
    return False

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
                    stmt_global = insert(monitored_processes).values(
                        process_number=numero,
                        first_monitored_at=datetime.now(timezone.utc),
                    )
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


async def fetch_process_for_list(numero: str) -> str:
    """Busca um processo e retorna uma string formatada para o comando /listar."""
    try:
        resultado_data = await asyncio.to_thread(buscar_processo, numero)
        empreendimento = resultado_data.get('details', '').split('\n')[1] # Pega a segunda linha da resposta formatada
        if 'Empreendimento:' in empreendimento:
            empreendimento_nome = empreendimento.replace('Empreendimento:', '').strip()
            return f"- {numero} - {empreendimento_nome}"
        return f"- {numero} - (Não foi possível obter o empreendimento)"
    except Exception:
        return f"- {numero} - (Erro ao buscar detalhes)"

async def listar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lista os processos monitorados pelo chat com o nome do empreendimento."""
    chat_id = str(update.effective_chat.id)
    db = SessionLocal()
    try:
        query = select(group_subscriptions.c.process_number).where(group_subscriptions.c.chat_id == chat_id).order_by(group_subscriptions.c.process_number)
        user_processes = [row[0] for row in db.execute(query)]
        
        if user_processes:
            await update.effective_message.reply_text(f"Buscando detalhes de {len(user_processes)} processo(s), isso pode levar um momento...")
            
            # Cria e executa as tarefas de busca em paralelo
            tasks = [fetch_process_for_list(p) for p in user_processes]
            results = await asyncio.gather(*tasks)
            
            lista = "\n".join(results)
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
                await update.effective_message.reply_text(f"❌ Você não está monitorando o processo {numero_escapado}\\. Use /monitorar para adicioná\\-lo\\.", parse_mode='MarkdownV2')
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

def _db_mark_checked(process_number: str) -> None:
    """Atualiza last_checked_at após uma consulta efetiva ao SIMLAM."""
    db = SessionLocal()
    try:
        db.execute(
            update(monitored_processes)
            .where(monitored_processes.c.process_number == process_number)
            .values(last_checked_at=datetime.now(timezone.utc))
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _db_get_monitored_with_timestamps() -> List[Tuple[str, Optional[str], Optional[datetime]]]:
    """Retorna (process_number, last_timestamp, first_monitored_at) de todos os monitorados."""
    db = SessionLocal()
    try:
        query = (
            select(
                monitored_processes.c.process_number,
                process_states.c.last_timestamp,
                monitored_processes.c.first_monitored_at,
            )
            .select_from(
                monitored_processes.outerjoin(
                    process_states,
                    monitored_processes.c.process_number == process_states.c.process_number,
                )
            )
        )
        return [(row[0], row[1], row[2]) for row in db.execute(query)]
    finally:
        db.close()


def classify_monitored_processes(
    rows: List[Tuple[str, Optional[str], Optional[datetime]]],
    now: Optional[datetime] = None,
) -> Dict[str, List[str]]:
    """Separa processos em grupos mutuamente exclusivos: frequente vs baixa frequência."""
    now = now or datetime.now(TZ_BR)
    frequent: List[str] = []
    low_frequency: List[str] = []
    just_demoted: List[str] = []

    for numero, last_timestamp, first_monitored_at in rows:
        if is_frequent_process(last_timestamp, first_monitored_at, now):
            frequent.append(numero)
            continue
        low_frequency.append(numero)
        parsed = parse_movement_datetime(last_timestamp)
        if parsed is not None:
            age = now - parsed
            if timedelta(days=30) < age <= timedelta(days=31):
                just_demoted.append(numero)

    return {
        MONITOR_FREQUENT: frequent,
        MONITOR_LOW: low_frequency,
        "just_demoted": just_demoted,
    }


async def check_single_process(numero: str, context: ContextTypes.DEFAULT_TYPE) -> Dict[str, Any]:
    """Lógica para verificar um único processo e notificar os assinantes."""
    result: Dict[str, Any] = {
        "numero": numero,
        "status": "error",
        "updated": False,
        "promoted": False,
        "consulted": False,
    }
    consulted = False
    try:
        logger.info(f"[PROCESSO] {numero} consultado.")

        # IMPORTANTE: DB é síncrono. Se o Postgres estiver instável, db.execute pode travar o event loop
        # e o bot inteiro para de responder. Por isso, todo acesso ao DB aqui roda em thread.
        def _db_get_last_timestamp(process_number: str) -> Optional[str]:
            db = SessionLocal()
            try:
                state_query = select(process_states.c.last_timestamp).where(process_states.c.process_number == process_number)
                return db.execute(state_query).scalar_one_or_none()
            finally:
                db.close()

        last_timestamp_result = await asyncio.to_thread(_db_get_last_timestamp, numero)
        was_frequent = is_frequent_process(last_timestamp_result)

        resultado_data = None
        for attempt in range(1, 4):
            consulted = True
            resultado_data = await asyncio.to_thread(buscar_processo, numero)
            current_timestamp = resultado_data.get('timestamp')
            if current_timestamp:
                break
            if attempt < 3:
                logger.warning(f"Tentativa {attempt}/3 falhou para {numero} (sem timestamp). Detalhes: {resultado_data.get('details')}. Tentando de novo em 5s...")
                await asyncio.sleep(5)
            else:
                logger.error(f"Falha ao obter timestamp para {numero} após 3 tentativas. Detalhes: {resultado_data.get('details')}")
                result["status"] = "no_timestamp"
                return result

        current_timestamp = resultado_data.get('timestamp')
        current_details = resultado_data.get('details')

        if not current_timestamp:
            result["status"] = "no_timestamp"
            return result

        if last_timestamp_result != current_timestamp:
            logger.info(f"[PROCESSO] {numero} consultado. Nova movimentação encontrada.")

            def _db_upsert_timestamp_and_get_subscribers(process_number: str, ts: str) -> List[str]:
                db = SessionLocal()
                try:
                    if last_timestamp_result is None:
                        db.execute(insert(process_states).values(process_number=process_number, last_timestamp=ts))
                    else:
                        update_stmt = (
                            update(process_states)
                            .where(process_states.c.process_number == process_number)
                            .values(last_timestamp=ts)
                        )
                        db.execute(update_stmt)

                    db.commit()

                    subscribers_query = select(group_subscriptions.c.chat_id).where(group_subscriptions.c.process_number == process_number)
                    return [row[0] for row in db.execute(subscribers_query)]
                except Exception:
                    db.rollback()
                    raise
                finally:
                    db.close()

            subscribers = await asyncio.to_thread(_db_upsert_timestamp_and_get_subscribers, numero, current_timestamp)

            now_frequent = is_frequent_process(current_timestamp)
            if last_timestamp_result is not None and not was_frequent and now_frequent:
                result["promoted"] = True
                logger.info(
                    f"[PROCESSO] {numero} voltou para monitoramento frequente após nova movimentação."
                )

            numero_escapado = escape_markdown(numero.replace('-', '\\-'), version=2)
            estado_escapado = escape_markdown(current_details, version=2)
            message = f"📢 *Nova atualização no processo {numero_escapado}\\!*\n\n{estado_escapado}"
            
            for chat_id in subscribers:
                try:
                    await context.bot.send_message(chat_id=chat_id, text=message, parse_mode='MarkdownV2')
                except Exception as e:
                    logger.error(f"Falha ao enviar mensagem de atualização para {chat_id} no processo {numero}: {e}")
            result["status"] = "updated"
            result["updated"] = True
        else:
            logger.info(f"[PROCESSO] {numero} consultado. Nenhuma nova movimentação encontrada.")
            result["status"] = "unchanged"

    except Exception as e:
        logger.error(f"Falha CRÍTICA ao verificar o processo {numero}: {e}", exc_info=True)
        result["status"] = "error"
    finally:
        result["consulted"] = consulted
        if consulted:
            try:
                await asyncio.to_thread(_db_mark_checked, numero)
            except Exception as e:
                logger.error(f"Falha ao atualizar last_checked_at de {numero}: {e}", exc_info=True)
    return result

async def check_updates(context: ContextTypes.DEFAULT_TYPE):
    """
    Job único de monitoramento, parametrizado por context.job.data:
    - frequent: só processos com movimentação nos últimos 30 dias (ou sem data)
    - low_frequency: só processos parados há mais de 30 dias (segunda e quinta)
    """
    started = _time.monotonic()
    mode = MONITOR_FREQUENT
    if context.job is not None and context.job.data:
        mode = context.job.data

    # Jitter apenas no ciclo frequente, para não atrasar o agendamento de segunda/quinta.
    if mode == MONITOR_FREQUENT:
        manual_jitter = random.uniform(0, 120)
        await asyncio.sleep(manual_jitter)

    logger.info(f"[MONITORAMENTO] Iniciando ciclo ({mode})...")

    try:
        rows = await asyncio.to_thread(_db_get_monitored_with_timestamps)
    except Exception as e:
        logger.error(f"[MONITORAMENTO] Falha ao listar processos no DB: {e}", exc_info=True)
        return

    groups = classify_monitored_processes(rows)
    frequent = groups[MONITOR_FREQUENT]
    low_frequency = groups[MONITOR_LOW]
    total = len(rows)

    logger.info(f"[MONITORAMENTO] {len(frequent)} processos selecionados para monitoramento frequente.")
    logger.info(f"[MONITORAMENTO] {len(low_frequency)} processos selecionados para baixa frequência.")

    for numero in groups["just_demoted"]:
        logger.info(f"[PROCESSO] {numero} passou para monitoramento de baixa frequência.")

    if mode == MONITOR_FREQUENT:
        processes_to_check = frequent
        ignored = len(low_frequency)
    else:
        processes_to_check = low_frequency
        ignored = len(frequent)

    logger.info(
        f"[MONITORAMENTO] Ciclo {mode}: {len(processes_to_check)} a consultar, {ignored} ignorados neste ciclo."
    )

    if not processes_to_check:
        elapsed = _time.monotonic() - started
        logger.info(f"[MONITORAMENTO] Nenhum processo para este ciclo. Duração: {elapsed:.1f}s.")
        return

    semaphore = asyncio.Semaphore(4)

    async def check_with_semaphore(numero: str) -> Dict[str, Any]:
        async with semaphore:
            try:
                return await check_single_process(numero, context)
            except Exception as e:
                logger.error(f"Erro isolado ao verificar {numero}: {e}", exc_info=True)
                return {"numero": numero, "status": "error", "updated": False, "promoted": False, "consulted": False}
            finally:
                await asyncio.sleep(random.uniform(5, 15))

    results = await asyncio.gather(*[check_with_semaphore(numero) for numero in processes_to_check])

    updates = sum(1 for r in results if r.get("updated"))
    promoted = sum(1 for r in results if r.get("promoted"))
    errors = sum(1 for r in results if r.get("status") == "error")
    no_ts = sum(1 for r in results if r.get("status") == "no_timestamp")
    consulted = sum(1 for r in results if r.get("consulted"))
    elapsed = _time.monotonic() - started

    logger.info(
        "[MONITORAMENTO] Ciclo %s concluído. total_monitorados=%s consultados=%s ignorados=%s "
        "novas_movimentacoes=%s promovidos=%s sem_timestamp=%s erros=%s duracao=%.1fs",
        mode,
        total,
        consulted,
        ignored,
        updates,
        promoted,
        no_ts,
        errors,
        elapsed,
    )


def main():
    if not TOKEN:
        print("Erro: BOT_TOKEN não foi configurado como variável de ambiente.")
        return
    if not DATABASE_URL:
        print("Erro: DATABASE_URL não foi configurado como variável de ambiente.")
        return

    # Sobe o healthcheck o quanto antes para o Render detectar porta aberta,
    # mesmo que o DB esteja instável no momento do deploy.
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # Inicializa o banco de dados (cria tabelas se necessário).
    # IMPORTANTE: não podemos derrubar o processo se o Postgres estiver instável,
    # senão o bot nem chega a iniciar o polling e não responde /start.
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

    # Error handler para capturar exceções não tratadas dos handlers
    async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.error("Exceção não tratada durante o processamento de um update", exc_info=context.error)

    app = ApplicationBuilder().token(TOKEN).build()
    job_queue = app.job_queue

    # Adiciona os handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("monitorar", monitorar))
    app.add_handler(CommandHandler("desmonitorar", desmonitorar))
    app.add_handler(CommandHandler("listar", listar))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, consultar))
    app.add_error_handler(on_error)

    # Monitoramento adaptativo (mesma aplicação, dois jobs mutuamente exclusivos):
    # - frequentes: a cada ~40 min (movimentação <= 30 dias, ou processo novo/sem data)
    # - baixa frequência: segunda e quinta às 09:00 America/Sao_Paulo (parados > 30 dias)
    job_queue.run_repeating(
        check_updates,
        interval=FREQUENT_INTERVAL_SECONDS,
        first=10,
        name="check_frequent_updates",
        data=MONITOR_FREQUENT,
        job_kwargs={"max_instances": 1, "coalesce": True, "misfire_grace_time": 900},
    )
    job_queue.run_daily(
        check_updates,
        time=LOW_FREQUENCY_TIME,
        days=LOW_FREQUENCY_CRON_DAYS,
        name="check_low_frequency_updates",
        data=MONITOR_LOW,
        job_kwargs={"max_instances": 1, "coalesce": True, "misfire_grace_time": 3600},
    )


    print("Bot rodando...")
    # drop_pending_updates evita backlog gigante depois de downtime/sleep do Render
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
