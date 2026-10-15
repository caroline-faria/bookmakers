import os
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import Column, DateTime, Integer, String, Text, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session, sessionmaker

import httpx
from bs4 import BeautifulSoup
import dateparser

# --- APLICAÇÃO FASTAPI ---
app = FastAPI(title="API de Bookmarks & ML - Render", version="1.1")

# --- CONFIGURAÇÃO DE CORS (OBRIGATÓRIO FICAR AQUI NO TOPO, LOGO APÓS O app = FastAPI()) ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Permite requisições de qualquer origem (inclusive extensões do Chrome)
    allow_credentials=True,
    allow_methods=["*"],  # Permite todos os métodos (GET, POST, OPTIONS, etc.)
    allow_headers=["*"],  # Permite todos os headers
)


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
    analisado_ml = Column(Integer, default=0)

Base.metadata.create_all(bind=engine)


# --- ESQUEMAS DE DADOS (Pydantic) ---
class BookmarkItem(BaseModel):
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


# --- DEPENDÊNCIA DE SESSÃO DO BANCO ---
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --- WEB SCRAPING E NLP ---
def extrair_texto_e_data_de_url(url: str):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        with httpx.Client(headers=headers, timeout=8.0, follow_redirects=True) as client:
            response = client.get(url)
            if response.status_code != 200:
                return None, None
            
            soup = BeautifulSoup(response.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
                tag.extract()
                
            texto_bruto = soup.get_text(separator=" ", strip=True)
            texto_limpo = " ".join(texto_bruto.split())[:4000]
            
            texto_para_data = (soup.title.string if soup.title else "") + " " + texto_limpo[:500]
            parsed_date = dateparser.parse(texto_para_data, languages=['pt', 'en'])
            data_str = parsed_date.strftime('%Y-%m-%d') if parsed_date else None
            
            return texto_limpo, data_str
    except Exception as e:
        print(f"Erro ao raspar a URL {url}: {e}")
        return None, None


# --- ROTAS DA API ---
@app.get("/")
def read_root():
    return {"status": "API de Bookmarks com CORS e Pipeline de ML rodando no Render!"}


@app.post("/api/bookmarks", status_code=status.HTTP_201_CREATED)
def create_bookmarks_batch(payload: BookmarkBatchCreate, db: Session = Depends(get_db)):
    salvos_contador = 0
    duplicados_contador = 0

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
            analisado_ml=0
        )
        
        db.add(db_bookmark)
        db.commit()
        salvos_contador += 1

    return {
        "message": "Lote recebido com sucesso!",
        "novos_salvos": salvos_contador,
        "ja_existentes": duplicados_contador
    }


@app.post("/api/bookmarks/processar-links", status_code=status.HTTP_200_OK)
def processar_links_pendentes(limite: int = 5, db: Session = Depends(get_db)):
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


@app.get("/api/bookmarks", response_model=List[BookmarkResponse])
def list_bookmarks(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    bookmarks = db.query(BookmarkModel).offset(skip).limit(limit).all()
    return bookmarks
