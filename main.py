import os
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import Column, DateTime, Integer, String, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session, sessionmaker

# --- APLICAÇÃO FASTAPI ---
app = FastAPI(title="API de Bookmarks - Render", version="1.0")

# --- CONFIGURAÇÃO DE CORS (DEVE FICAR LOGO APÓS CRIAR O app) ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Permite requisições de qualquer origem (inclusive extensões do Chrome)
    allow_credentials=True,
    allow_methods=["*"],  # Permite todos os métodos (GET, POST, OPTIONS, etc.)
    allow_headers=["*"],  # Permite todos os headers
)


# --- CONFIGURAÇÃO DO BANCO DE DADOS ---
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./bookmarks.sqlite")

# Correção caso a URL venha com "postgres://" do Render
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Configuração da engine (compatível com SQLite e PostgreSQL)
if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    engine = create_engine(DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# --- MODELO DO BANCO DE DADOS (SQLAlchemy) ---
class BookmarkModel(Base):
    __tablename__ = "bookmarks"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, index=True)
    url = Column(String, unique=True, index=True)
    description = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

# Cria as tabelas automaticamente ao iniciar
Base.metadata.create_all(bind=engine)


# --- ESQUEMAS DE DADOS (Pydantic - Ajustados para receber o lote) ---
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


# --- ROTAS DA API ---
@app.get("/")
def read_root():
    return {"status": "API de Bookmarks rodando com sucesso no Render!"}


# --- ROTA POST: Recebe o lote de favoritos da extensão ---
@app.post("/api/bookmarks", status_code=status.HTTP_201_CREATED)
def create_bookmarks_batch(payload: BookmarkBatchCreate, db: Session = Depends(get_db)):
    salvos_contador = 0
    duplicados_contador = 0

    for item in payload.bookmarks:
        # Verifica se a URL já existe para evitar duplicar registros
        existing = db.query(BookmarkModel).filter(BookmarkModel.url == item.url).first()
        if existing:
            duplicados_contador += 1
            continue

        # Converte o timestamp epoch (milissegundos) para texto legível, se houver
        date_str = None
        if item.dateAdded:
            try:
                date_str = datetime.fromtimestamp(item.dateAdded / 1000).strftime('%Y-%m-%d %H:%M:%S')
            except Exception:
                date_str = str(item.dateAdded)

        db_bookmark = BookmarkModel(
            title=item.title,
            url=item.url,
            description=date_str
        )
        
        db.add(db_bookmark)
        db.commit()
        salvos_contador += 1

    return {
        "message": "Processamento concluído com sucesso!",
        "novos_salvos": salvos_contador,
        "ja_existentes": duplicados_contador,
        "total_recebido": len(payload.bookmarks)
    }


# --- ROTA GET: Listar todos os bookmarks salvos ---
@app.get("/api/bookmarks", response_model=List[BookmarkResponse])
def list_bookmarks(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    bookmarks = db.query(BookmarkModel).offset(skip).limit(limit).all()
    return bookmarks
