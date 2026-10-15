import os
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import Column, DateTime, Integer, String, Text, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session, sessionmaker

# Bibliotecas para Scraping e PLN/Datas
import httpx
from bs4 import BeautifulSoup
import dateparser

# --- APLICAÇÃO FASTAPI ---
app = FastAPI(title="API de Bookmarks & ML - Render", version="1.1")

# --- CONFIGURAÇÃO DE CORS ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Permite requisições de extensões do Chrome e outros clientes
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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


# --- MODELO DO BANCO DE DADOS (Atualizado para armazenar dados do Scraping/ML) ---
class BookmarkModel(Base):
    __tablename__ = "bookmarks"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, index=True)
    url = Column(String, unique=True, index=True)
    description = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Novos campos para o Pipeline de Machine Learning
    extracted_text = Column(Text, nullable=True)       # Texto limpo extraído da página
    event_date = Column(String, nullable=True)          # Data do evento identificada
    analisado_ml = Column(Integer, default=0)           # 0 = Pendente, 1 = Processado com sucesso, -1 = Erro no acesso

# Cria as tabelas automaticamente
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


# --- FUNÇÃO AUXILIAR DE WEB SCRAPING E NLP ---
def extrair_texto_e_data_de_url(url: str):
    """Acessa a URL, remove elementos irrelevantes do HTML, extrai o texto principal e tenta achar uma data."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        with httpx.Client(headers=headers, timeout=8.0, follow_redirects=True) as client:
            response = client.get(url)
            if response.status_code != 200:
                return None, None
            
            soup = BeautifulSoup(response.text, "html.parser")
            
            # Remove scripts, estilos, navegação e rodapés para limpar o ruído
            for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
                tag.extract()
                
            texto_bruto = soup.get_text(separator=" ", strip=True)
            # Limpa espaços excessivos e limita o texto aos primeiros 4000 caracteres (ótimo para embeddings/ML)
            texto_limpo = " ".join(texto_bruto.split())[:4000]
            
            # Tenta extrair uma data usando dateparser no título ou nos primeiros parágrafos
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
    return {"status": "API de Bookmarks com Pipeline de ML rodando no Render!"}


# 1. Rota para receber o lote de favoritos da Extensão do Chrome
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
            analisado_ml=0 # Define como pendente para o scraping posterior
        )
        
        db.add(db_bookmark)
        db.commit()
        salvos_contador += 1

    return {
        "message": "Lote recebido com sucesso!",
        "novos_salvos": salvos_contador,
        "ja_existentes": duplicados_contador
    }


# 2. Rota para processar os links salvos (Scraping + Extração para ML)
@app.post("/api/bookmarks/processar-links", status_code=status.HTTP_200_OK)
def processar_links_pendentes(limite: int = 5, db: Session = Depends(get_db)):
    """Busca links pendentes no banco, acessa os sites, extrai o texto e tenta identificar a data do evento."""
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
            bm.analisado_ml = 1  # Sucesso
            status_proc = "Sucesso"
        else:
            bm.analisado_ml = -1  # Falha no acesso
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


# 3. Rota para listar todos os favoritos (com os dados extraídos)
@app.get("/api/bookmarks", response_model=List[BookmarkResponse])
def list_bookmarks(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    bookmarks = db.query(BookmarkModel).offset(skip).limit(limit).all()
    return bookmarks
