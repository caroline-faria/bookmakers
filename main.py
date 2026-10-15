import os
import httpx
import dateparser
from typing import List, Optional
from datetime import datetime
from fastapi import FastAPI, Depends, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from fastapi.middleware.cors import CORSMiddleware

# --- CONFIGURAÇÃO DO BANCO DE DADOS (POSTGRESQL) ---
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost/dbname")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- MODELO SQLALCHEMY ---
class BookmarkModel(Base):
    __tablename__ = "bookmarks"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, index=True)
    url = Column(String, unique=True, index=True)
    description = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    extracted_text = Column(Text, nullable=True)
    event_date = Column(String, nullable=True)
    analisado_ml = Column(Integer, default=0)

# --- MIGRAÇÃO AUTOMÁTICA (Compatível com Plano Gratuito do Render) ---
def atualizar_estrutura_banco():
    try:
        Base.metadata.create_all(bind=engine)
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE bookmarks ADD COLUMN IF NOT EXISTS extracted_text TEXT;"))
            conn.execute(text("ALTER TABLE bookmarks ADD COLUMN IF NOT EXISTS event_date VARCHAR;"))
            conn.execute(text("ALTER TABLE bookmarks ADD COLUMN IF NOT EXISTS analisado_ml INTEGER DEFAULT 0;"))
            conn.commit()
            print("Estrutura do banco de dados atualizada/verificada com sucesso!")
    except Exception as e:
        print(f"Aviso na migração do banco: {e}")

# Executa a verificação e migração assim que a aplicação inicia
atualizar_estrutura_banco()

# --- CONFIGURAÇÃO DO FASTAPI ---
app = FastAPI(title="Bookmakers & ML API", version="1.0")

# Habilita CORS para permitir conexões da Extensão do Chrome e painéis externos
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- SCHEMAS PYDANTIC ---
class BookmarkItem(BaseModel):
    title: str
    url: str
    dateAdded: Optional[int] = None

class BookmarkBatchRequest(BaseModel):
    timestamp: str
    total: int
    bookmarks: List[BookmarkItem]

# --- WEB SCRAPING E NLP ---
def extrair_texto_e_data_de_url(url: str):
    try:
        proxy_url = f"https://r.jina.ai/{url}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json, text/plain, */*"
        }
        
        with httpx.Client(headers=headers, timeout=15.0, follow_redirects=True) as client:
            response = client.get(proxy_url)
            if response.status_code != 200:
                return None, None
            
            texto_limpo = response.text[:4000]
            if not texto_limpo or len(texto_limpo) < 50:
                return None, None
            
            parsed_date = dateparser.parse(texto_limpo[:600], languages=['pt', 'en'])
            data_str = parsed_date.strftime('%Y-%m-%d') if parsed_date else None
            
            return texto_limpo, data_str
    except Exception as e:
        print(f"Erro ao processar URL {url}: {e}")
        return None, None

def processar_fila_background():
    db = SessionLocal()
    try:
        pendentes = db.query(BookmarkModel).filter(BookmarkModel.analisado_ml == 0).limit(10).all()
        for item in pendentes:
            texto, data_evento = extrair_texto_e_data_de_url(item.url)
            if texto:
                item.extracted_text = texto
                item.event_date = data_evento
                item.analisado_ml = 1
            else:
                item.analisado_ml = -1
            db.commit()
    finally:
        db.close()

# --- ENDPOINTS DA API ---

@app.post("/api/bookmarks")
def receber_lote_bookmarks(payload: BookmarkBatchRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    novos_salvos = 0
    ja_existentes = 0

    for item in payload.bookmarks:
        existe = db.query(BookmarkModel).filter(BookmarkModel.url == item.url).first()
        if not existe:
            db_bookmark = BookmarkModel(
                title=item.title,
                url=item.url,
                description=str(item.dateAdded) if item.dateAdded else None,
                analisado_ml=0
            )
            db.add(db_bookmark)
            novos_salvos += 1
        else:
            ja_existentes += 1

    db.commit()
    background_tasks.add_task(processar_fila_background)

    return {
        "status": "sucesso",
        "novos_salvos": novos_salvos,
        "ja_existentes": ja_existentes,
        "mensagem": "Lote recebido com sucesso e processamento iniciado."
    }

@app.get("/api/bookmarks")
def listar_bookmarks(db: Session = Depends(get_db)):
    return db.query(BookmarkModel).all()

@app.get("/api/bookmarks/processar-links")
def forçar_processamento(limite: int = 10, db: Session = Depends(get_db)):
    pendentes = db.query(BookmarkModel).filter(BookmarkModel.analisado_ml == 0).limit(limite).all()
    processados = 0
    for item in pendentes:
        texto, data_evento = extrair_texto_e_data_de_url(item.url)
        if texto:
            item.extracted_text = texto
            item.event_date = data_evento
            item.analisado_ml = 1
            processados += 1
        else:
            item.analisado_ml = -1
        db.commit()
    return {"status": "concluído", "processados_nesta_execucao": processados}

# --- PAINEL VISUAL HTML ---
@app.get("/painel", response_class=HTMLResponse)
def painel_visual(db: Session = Depends(get_db)):
    bookmarks = db.query(BookmarkModel).order_by(BookmarkModel.created_at.desc()).all()
    
    linhas = ""
    for b in bookmarks:
        status_txt = "Processado" if b.analisado_ml == 1 else ("Erro" if b.analisado_ml == -1 else "Pendente")
        cor_status = "green" if b.analisado_ml == 1 else ("red" if b.analisado_ml == -1 else "orange")
        texto_amostra = b.extracted_text[:200] + "..." if b.extracted_text else "Sem texto extraído"
        
        linhas += f"""
        <tr>
            <td>{b.id}</td>
            <td><b>{b.title}</b><br><a href="{b.url}" target="_blank" style="font-size: 11px; color: #0066cc;">{b.url}</a></td>
            <td><span style="color: {cor_status}; font-weight: bold;">{status_txt}</span></td>
            <td>{b.event_date or '-'}</td>
            <td style="font-size: 12px; color: #555;">{texto_amostra}</td>
        </tr>
        """

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Painel de Bookmarks</title>
        <meta charset="utf-8">
        <style>
            body {{ font-family: Arial, sans-serif; margin: 20px; background: #f9f9f9; }}
            h2 {{ color: #333; }}
            table {{ width: 100%; border-collapse: collapse; background: white; margin-top: 15px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
            th, td {{ padding: 12px; border-bottom: 1px solid #ddd; text-align: left; }}
            th {{ background-color: #2c3e50; color: white; }}
            tr:hover {{ background-color: #f1f1f1; }}
        </style>
    </head>
    <body>
        <h2>Painel de Inspeção de Bookmarks & NLP</h2>
        <p>Total de registros cadastrados: <b>{len(bookmarks)}</b></p>
        <table>
            <tr>
                <th>ID</th>
                <th>Título / URL</th>
                <th>Status</th>
                <th>Data Alvo</th>
                <th>Amostra do Texto Extraído</th>
            </tr>
            {linhas}
        </table>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)
