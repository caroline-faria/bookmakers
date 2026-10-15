import os
from datetime import datetime
from typing import List, Optional
from fastapi import BackgroundTasks, FastAPI, Depends, HTTPException, status, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, HTMLResponse
from pydantic import BaseModel
from sqlalchemy import Column, DateTime, Integer, String, Text, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session, sessionmaker

import httpx
from bs4 import BeautifulSoup
import dateparser

# --- APLICAÇÃO FASTAPI ---
app = FastAPI(title="API de Bookmarks & ML - Render", version="1.8")

# --- CONFIGURAÇÃO DE CORS ROBUSTA ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

@app.middleware("http")
async def add_cors_headers(request: Request, call_next):
    if request.method == "OPTIONS":
        response = Response(status_code=200)
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "*"
        return response
    response = await call_next(request)
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response


# --- CONFIGURAÇÃO DO BANCO DE DADOS ---
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./bookmarks.sqlite")

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    engine = create_engine(DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# --- MODELO DO BANCO DE DADOS ---
class BookmarkModel(Base):
  
    
    
    __tablename__ = "bookmarks"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, index=True)
    url = Column(String, unique=True, index=True)
    description = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    extracted_text = Column(Text, nullable=True)
    event_date = Column(String, nullable=True)
    analisado_ml = Column(Integer, default=0)  # 0 = Pendente, 1 = Processado, -1 = Erro

# IMPORTANTE: drop_all foi removido para não apagar mais os seus dados!
Base.metadata.create_all(bind=engine)


# --- ESQUEMAS DE DADOS (Pydantic) ---
class BookmarkItem(BaseModel):

    title: str
    url: str
    dateAdded: Optional[int] = None
    extracted_text: Optional[str] = None  # <-- Novo: recebe o texto se vier pronto
    event_date: Optional[str] = None       # <-- Novo: recebe a data se vier pront
    title: str
    url: str
    dateAdded: Optional[int] = None

class BookmarkBatchCreate(BaseModel):
    timestamp: Optional[str] = None
    total: Optional[int] = None
    bookmarks: List[BookmarkItem]

class BookmarkResponse(BaseModel):
    id: int
    title: str
    url: str
    description: Optional[str] = None
    extracted_text: Optional[str] = None
    event_date: Optional[str] = None
    analisado_ml: int
    created_at: datetime

    class Config:
        from_attributes = True


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --- WEB SCRAPING E NLP ---
# --- WEB SCRAPING E NLP ROBUSTO ---
# --- WEB SCRAPING VIA PROXY DE LEITURA (CONTORNA BLOQUEIOS NO RENDER) ---
def extrair_texto_e_data_de_url(url: str):
    try:
        # Usa um serviço de proxy de leitura amigável para transformar páginas bloqueadas em texto limpo
        proxy_url = f"https://r.jina.ai/{url}"
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json, text/plain, */*"
        }
        
        with httpx.Client(headers=headers, timeout=15.0, follow_redirects=True) as client:
            response = client.get(proxy_url)
            
            if response.status_code != 200:
                print(f"Erro no proxy para {url} (Status: {response.status_code})")
                return None, None
            
            texto_limpo = response.text[:4000]
            
            if not texto_limpo or len(texto_limpo) < 50:
                return None, None
            
            # Tenta extrair a data usando o dateparser no texto obtido
            parsed_date = dateparser.parse(texto_limpo[:600], languages=['pt', 'en'])
            data_str = parsed_date.strftime('%Y-%m-%d') if parsed_date else None
            
            return texto_limpo, data_str
            
    except Exception as e:
        print(f"Erro crítico ao raspar via proxy a URL {url}: {e}")
        return None, None


# --- TAREFA EM SEGUNDO PLANO (AUTOMATIZAÇÃO DO SCRAPING) ---
def processar_pendentes_background(db_factory, limite: int = 50):
    db = db_factory()
    try:
        pendentes = db.query(BookmarkModel).filter(BookmarkModel.analisado_ml == 0).limit(limite).all()
        for bm in pendentes:
            texto_extraido, data_encontrada = extrair_texto_e_data_de_url(bm.url)
            if texto_extraido:
                bm.extracted_text = texto_extraido
                if data_encontrada and not bm.event_date:
                    bm.event_date = data_encontrada
                bm.analisado_ml = 1
            else:
                bm.analisado_ml = -1
            db.commit()
    except Exception as e:
        print(f"Erro no background task de scraping: {e}")
    finally:
        db.close()


# --- ROTAS DA API ---
@app.get("/")
def read_root():
    return {"status": "API de Bookmarks & ML rodando perfeitamente no Render!"}


@app.post("/api/bookmarks", status_code=status.HTTP_201_CREATED)
def create_bookmarks_batch(
    payload: BookmarkBatchCreate, 
    background_tasks: BackgroundTasks, 
    db: Session = Depends(get_db)
):
    salvos_contador = 0
    duplicados_contador = 0

    try:
        for item in payload.bookmarks:
            existing = db.query(BookmarkModel).filter(BookmarkModel.url == item.url).first()
            if existing:
                duplicados_contador += 1
                continue

            date_str = None
            if item.dateAdded:
                try:
                    date_str = datetime.fromtimestamp(item.dateAdded / 1000).strftime('%Y-%m-%d %H:%M:%S')
                except Exception:
                    date_str = str(item.dateAdded)

            db_bookmark = BookmarkModel(
                title=item.title,
                url=item.url,
                description=date_str,
                extracted_text=item.extracted_text,  # Salva o texto enviado pelo navegador
                event_date=item.event_date,          # Salva a data enviada (se houver)
                analisado_ml=1 if item.extracted_text else 0  # Se já mandou o texto, marca como processado!
            )
            )
            db.add(db_bookmark)
            salvos_contador += 1

        db.commit()

        # DISPARA O SCRAPING AUTOMATICAMENTE EM SEGUNDO PLANO QUANDO NOVOS FOREM SALVOS
        if salvos_contador > 0:
            background_tasks.add_task(processar_pendentes_background, SessionLocal, limite=50)

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro interno ao salvar lote: {str(e)}")

    return {
        "message": "Lote recebido, salvo e scraping automatizado iniciado!",
        "novos_salvos": salvos_contador,
        "ja_existentes": duplicados_contador
    }


@app.post("/api/bookmarks/processar-links", status_code=status.HTTP_200_OK)
def processar_links_pendentes_manual(limite: int = 5, db: Session = Depends(get_db)):
    pendentes = db.query(BookmarkModel).filter(BookmarkModel.analisado_ml == 0).limit(limite).all()
    
    if not pendentes:
        return {"message": "Nenhum link pendente para processar no momento."}

    resultados = []
    for bm in pendentes:
        texto_extraido, data_encontrada = extrair_texto_e_data_de_url(bm.url)
        
        if texto_extraido:
            bm.extracted_text = texto_extraido
            if data_encontrada and not bm.event_date:
                bm.event_date = data_encontrada
            bm.analisado_ml = 1
            status_proc = "Sucesso"
        else:
            bm.analisado_ml = -1
            status_proc = "Erro ao acessar URL"
            
        db.commit()
        resultados.append({
            "id": bm.id,
            "url": bm.url,
            "status": status_proc,
            "data_evento": bm.event_date
        })

    return {
        "processados_nesta_execucao": len(resultados),
        "detalhes": resultados
    }


@app.get("/api/bookmarks/{bookmark_id}/texto", response_class=PlainTextResponse)
def ver_conteudo_pagina(bookmark_id: int, db: Session = Depends(get_db)):
    bookmark = db.query(BookmarkModel).filter(BookmarkModel.id == bookmark_id).first()
    if not bookmark:
        raise HTTPException(status_code=404, detail="Bookmark não encontrado.")
    
    if not bookmark.extracted_text:
        return "Este link ainda não foi processado pelo pipeline de scraping ou nenhum texto foi extraído."
        
    return bookmark.extracted_text


@app.get("/api/bookmarks", response_model=List[BookmarkResponse])
def list_bookmarks(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    bookmarks = db.query(BookmarkModel).offset(skip).limit(limit).all()
    return bookmarks
