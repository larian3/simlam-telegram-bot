import os
from datetime import datetime
from sqlalchemy import create_engine, Table, MetaData
from sqlalchemy.orm import sessionmaker
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

process_states = Table('process_states', metadata, autoload_with=engine)

def analyze():
    db = SessionLocal()
    try:
        results = db.query(process_states).all()
        
        # Data de hoje para referência
        hoje = datetime(2026, 8, 28)
        
        faixas = {
            "0 a 15 dias": 0,
            "16 a 30 dias": 0,
            "31 a 90 dias": 0,
            "91 a 180 dias": 0,
            "Mais de 180 dias": 0,
            "Sem data/Erro": 0
        }
        
        for row in results:
            ts_str = row[1]
            
            if not ts_str:
                faixas["Sem data/Erro"] += 1
                continue
                
            try:
                # O formato do SIMLAM geralmente é DD/MM/YYYY HH:MM:SS
                ts_clean = ts_str.strip()
                dt = datetime.strptime(ts_clean, "%d/%m/%Y %H:%M:%S")
                delta = (hoje - dt).days
                
                if delta <= 15:
                    faixas["0 a 15 dias"] += 1
                elif delta <= 30:
                    faixas["16 a 30 dias"] += 1
                elif delta <= 90:
                    faixas["31 a 90 dias"] += 1
                elif delta <= 180:
                    faixas["91 a 180 dias"] += 1
                else:
                    faixas["Mais de 180 dias"] += 1
            except Exception as e:
                # Tenta extrair só a data se falhar
                try:
                    dt = datetime.strptime(ts_clean[:10], "%d/%m/%Y")
                    delta = (hoje - dt).days
                    if delta <= 15:
                        faixas["0 a 15 dias"] += 1
                    elif delta <= 30:
                        faixas["16 a 30 dias"] += 1
                    elif delta <= 90:
                        faixas["31 a 90 dias"] += 1
                    elif delta <= 180:
                        faixas["91 a 180 dias"] += 1
                    else:
                        faixas["Mais de 180 dias"] += 1
                except:
                    faixas["Sem data/Erro"] += 1

        print("Análise de Movimentação dos Processos:")
        print("-" * 45)
        total = len(results)
        for faixa, count in faixas.items():
            pct = (count / total) * 100 if total > 0 else 0
            print(f"{faixa:18}: {count:3} processos ({pct:.1f}%)")
        print("-" * 45)
        print(f"Total analisado   : {total} processos")
            
    except Exception as e:
        print(f"Erro: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    analyze()
