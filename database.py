import os
from sqlalchemy import create_engine, Column, String, MetaData, Table, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base

# Lê a URL do banco de dados da variável de ambiente
DATABASE_URL = os.getenv("DATABASE_URL")

# Garante que a URL use o dialeto 'postgresql' que o SQLAlchemy espera.
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

if not DATABASE_URL:
    raise ValueError("A variável de ambiente DATABASE_URL não foi configurada.")

# Configuração do SQLAlchemy
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
metadata = MetaData()

# Define a tabela para processos monitorados
# Cada linha representa um usuário monitorando um processo
monitored_processes = Table(
    'monitored_processes', metadata,
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
    if not inspector.has_table('monitored_processes') or not inspector.has_table('process_states'):
        print("Criando tabelas no banco de dados...")
        metadata.create_all(bind=engine)
        print("Tabelas criadas com sucesso.")
    else:
        print("Tabelas já existem no banco de dados.")

# Exemplo de como usar a sessão em outras partes do código:
# def get_db():
#     db = SessionLocal()
#     try:
#         yield db
#     finally:
#         db.close()
