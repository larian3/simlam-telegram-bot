import os
from sqlalchemy import create_engine, Table, MetaData
from sqlalchemy.orm import sessionmaker

# Carrega as variáveis do .env
from dotenv import load_dotenv
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

if "?" in DATABASE_URL or "&" in DATABASE_URL:
    import re
    DATABASE_URL = re.sub(r'[?&]pgbouncer=[^&]*', '', DATABASE_URL)
    DATABASE_URL = re.sub(r'[?&]$', '', DATABASE_URL)
    if '&' in DATABASE_URL and '?' in DATABASE_URL:
        parts = DATABASE_URL.split('?', 1)
        if len(parts) == 2:
            DATABASE_URL = parts[0] + '?' + parts[1].lstrip('&')

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)
metadata = MetaData()

# Reflete a tabela
monitored_processes = Table('monitored_processes', metadata, autoload_with=engine)

def listar_processos():
    db = SessionLocal()
    try:
        # Busca todos os processos
        result = db.query(monitored_processes).all()
        processos = [row[0] for row in result]
        
        print(f"\nTotal de processos em monitoramento: {len(processos)}\n")
        for i, p in enumerate(processos, 1):
            print(f"{i}. {p}")
            
    except Exception as e:
        print(f"Erro ao acessar o banco: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    listar_processos()
