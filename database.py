import os
from sqlalchemy import create_engine, Column, String, MetaData, Table, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base

# Lê a URL do banco de dados da variável de ambiente
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("A variável de ambiente DATABASE_URL não foi configurada.")

# Garante que a URL use o dialeto 'postgresql' que o SQLAlchemy espera.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Remove parâmetros não suportados pelo psycopg2 (ex.: pgbouncer=true do Supabase pooler)
# O psycopg2 não reconhece esses parâmetros e causa erro "invalid connection option"
# Usamos regex para remover diretamente, evitando problemas com urlparse e URLs complexas
if "?" in DATABASE_URL or "&" in DATABASE_URL:
    import re
    # Remove parâmetros problemáticos usando regex (mais robusto que urlparse)
    DATABASE_URL = re.sub(r'[?&]pgbouncer=[^&]*', '', DATABASE_URL)
    DATABASE_URL = re.sub(r'[?&]sslmode=[^&]*', '', DATABASE_URL)
    # Remove ? ou & no final se sobrar após remover parâmetros
    DATABASE_URL = re.sub(r'[?&]$', '', DATABASE_URL)
    # Se o primeiro parâmetro foi removido e sobrou & no início da query string, troca por ?
    if '&' in DATABASE_URL and '?' in DATABASE_URL:
        # Garante que só tem um ? na URL
        parts = DATABASE_URL.split('?', 1)
        if len(parts) == 2:
            DATABASE_URL = parts[0] + '?' + parts[1].lstrip('&')

# Configuração do SQLAlchemy
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
metadata = MetaData()

# Define a tabela para processos monitorados (agora uma lista global única)
# Cada processo é verificado apenas uma vez, independentemente de quantos grupos o seguem.
monitored_processes = Table(
    'monitored_processes', metadata,
    Column('process_number', String, primary_key=True)
)

# Nova tabela para ligar os grupos aos processos que eles desejam monitorar
group_subscriptions = Table(
    'group_subscriptions', metadata,
    Column('chat_id', String, primary_key=True),
    Column('process_number', String, primary_key=True)
)

# Define a tabela para o estado (timestamp) dos processos
process_states = Table(
    'process_states', metadata,
    Column('process_number', String, primary_key=True),
    Column('last_timestamp', String, nullable=False)
)


def init_db():
    """
    Cria as tabelas no banco de dados se elas ainda não existirem.
    """
    inspector = inspect(engine)
    if not inspector.has_table('monitored_processes') or \
       not inspector.has_table('process_states') or \
       not inspector.has_table('group_subscriptions'):
        print("Criando ou atualizando tabelas no banco de dados...")
        metadata.create_all(bind=engine)
        print("Tabelas criadas/atualizadas com sucesso.")
    else:
        print("Tabelas já existem no banco de dados.")

# Exemplo de como usar a sessão em outras partes do código:
# def get_db():
#     db = SessionLocal()
#     try:
#         yield db
#     finally:
#         db.close()
