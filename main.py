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
    allow_methods=["*"],  # Permite todos os métodos (GET, POST, OPTIONS, PUT, DELETE, etc.)
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


# --- ESQUEMAS DE DADOS (Pydantic) ---
class BookmarkCreate(BaseModel):
    title: str
    url: str
    description: Optional[str] = None

class BookmarkResponse(BookmarkCreate):
    id: int
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
    return {"status": "API está rodando com CORS e Banco de Dados configurados!"}


@app.post("/api/bookmarks", response_model=BookmarkResponse, status_code=status.HTTP_201_CREATED)
def create_bookmark(bookmark: BookmarkCreate, db: Session = Depends(get_db)):
    # Verifica se a URL já existe
    existing = db.query(BookmarkModel).filter(BookmarkModel.url == bookmark.url).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="Este favorito com essa URL já está cadastrado."
        )

    # Cria o registro no banco
    db_bookmark = BookmarkModel(
        title=bookmark.title,
        url=bookmark.url,
        description=bookmark.description
    )
    
    db.add(db_bookmark)
    db.commit()
    db.refresh(db_bookmark)
    
    return db_bookmark


@app.get("/api/bookmarks", response_model=List[BookmarkResponse])
def list_bookmarks(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    bookmarks = db.query(BookmarkModel).offset(skip).limit(limit).all()
    return bookmarks
