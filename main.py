from fastapi import FastAPI, UploadFile, File
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx
from bs4 import BeautifulSoup

app = FastAPI()

from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware  # <--- 1. Importe o CORSMiddleware
# ... (restante das suas importações)

app = FastAPI(title="API de Bookmarks - Render", version="1.0")

# --- 2. ADICIONE ESTE BLOCO DE CORS LOGO ABAIXO ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Permite requisições de qualquer origem (extensões, sites, etc.)
    allow_credentials=True,
    allow_methods=["*"],  # Permite todos os métodos (GET, POST, OPTIONS, etc.)
    allow_headers=["*"],  # Permite todos os cabeçalhos
)

import os
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, Depends, HTTPException, status
from pydantic import BaseModel, HttpUrl
from sqlalchemy import Column, DateTime, Integer, String, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session, sessionmaker

# --- CONFIGURAÇÃO DO BANCO DE DADOS ---
# O Render fornece a URL do PostgreSQL através da variável de ambiente DATABASE_URL.
# Se estiver rodando localmente sem ela, usamos um banco SQLite de teste por segurança.
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./bookmarks.sqlite")

# Correção necessária para o Render caso a URL comece com "postgres://" em vez de "postgresql://"
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

# Cria as tabelas automaticamente ao iniciar (caso não existam)
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
        from_attributes = True # Compatível com versões recentes do Pydantic (v2)


# --- DEPENDÊNCIA DE SESSÃO DO BANCO ---
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --- APLICAÇÃO FASTAPI ---
app = FastAPI(title="API de Bookmarks - Render", version="1.0")

@app.get("/")
def read_root():
    return {"status": "API está rodando com sucesso!"}


# --- ROTA POST: Salvar um novo bookmark no banco ---
@app.post("/api/bookmarks", response_model=BookmarkResponse, status_code=status.HTTP_201_CREATED)
def create_bookmark(bookmark: BookmarkCreate, db: Session = Depends(get_db)):
    # Verifica se a URL já existe para evitar duplicatas (opcional)
    existing = db.query(BookmarkModel).filter(BookmarkModel.url == bookmark.url).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="Este favorito com essa URL já está cadastrado."
        )

    # Cria o registro mapeado para o banco
    db_bookmark = BookmarkModel(
        title=bookmark.title,
        url=bookmark.url,
        description=bookmark.description
    )
    
    # Salva efetivamente no banco de dados do Render
    db.add(db_bookmark)
    db.commit()
    db.refresh(db_bookmark)
    
    return db_bookmark


# --- ROTA GET: Listar todos os bookmarks salvos ---
@app.get("/api/bookmarks", response_model=List[BookmarkResponse])
def list_bookmarks(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    bookmarks = db.query(BookmarkModel).offset(skip).limit(limit).all()
    return bookmarks

@app.post("/api/bookmarks")
def receive_bookmarks(data: dict):
    return {"success": True, "total": data.get("total")}

# Lista temporária em memória para acumular os dados raspados
banco_temporario = []

@app.get("/")
async def raiz():
    return {
        "status": "online",
        "mensagem": "API de raspagem de favoritos rodando!",
        "rotas": ["POST /api/bookmarks", "POST /importar-favoritos", "GET /baixar-favoritos"]
    }

# ROTA 1: Envia o arquivo HTML exportado do navegador
@app.post("/importar-favoritos")
async def importar_favoritos(file: UploadFile = File(...)):
    try:
        # Lê o conteúdo do arquivo HTML enviado
        conteúdo_html = await file.read()
        html_texto = conteúdo_html.decode("utf-8", errors="ignore")
        
        # Analisa o HTML para extrair todas as tags <a> (onde ficam os links salvos)
        soup = BeautifulSoup(html_texto, "html.parser")
        links_encontrados = []
        
        for link in soup.find_all("a"):
            url = link.get("href")
            nome = link.string
            if url and url.startswith("http"):
                links_encontrados.append({"nome": nome or "Sem nome", "url": url})
                
    except Exception as e:
        return {"status": "erro", "mensagem": f"Falha ao ler o arquivo HTML: {str(e)}"}

    # Executa a raspagem em cada link encontrado
    resultados_raspagem = []
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        for item in links_encontrados:
            url = item["url"]
            nome_original = item["nome"]
            try:
                resposta = await client.get(url)
                soup_pagina = BeautifulSoup(resposta.text, "html.parser")
                
                # Tenta pegar o título da página web acessada
                titulo_pagina = soup_pagina.title.string.strip() if soup_pagina.title and soup_pagina.title.string else "Sem título"
                
                resultados_raspagem.append({
                    "nome_favorito": nome_original,
                    "url": url,
                    "titulo_extraido": titulo_pagina,
                    "status_site": "sucesso"
                })
            except Exception as e:
                resultados_raspagem.append({
                    "nome_favorito": nome_original,
                    "url": url,
                    "erro": str(e),
                    "status_site": "falha"
                })

    # Salva o lote de dados raspados no banco temporário
    banco_temporario.append({
        "total_sites_lidos": len(resultados_raspagem),
        "dados": resultados_raspagem
    })

    return {
        "status": "sucesso",
        "mensagem": f"Raspagem concluída para {len(resultados_raspagem)} sites!",
        "amostra": resultados_raspagem[:5] # Mostra os 5 primeiros na resposta
    }

# ROTA 2: Baixa todos os dados acumulados em JSON
@app.get("/baixar-favoritos")
async def baixar_favoritos():
    return JSONResponse(
        content={"historico_raspagem": banco_temporario},
        headers={"Content-Disposition": "attachment; filename=favoritos_raspados.json"}
    )

# Lista em memória para guardar os favoritos recebidos
favoritos_recebidos_historico = []

@app.post("/api/bookmarks")
def receive_bookmarks(data: dict):
    # Imprime os dados recebidos diretamente nos logs do Render
    print("=== DADOS RECEBIDOS DA EXTENSÃO ===")
    print(data)
    print("===================================")
    
    banco_temporario.append(data)
    return {"success": True, "total": len(data.get("bookmarks", []))}
# Nova rota para visualizar pelo navegador
@app.get("/ver-favoritos")
def ver_favoritos():
    return {"total_envios": len(favoritos_recebidos_historico), "dados": favoritos_recebidos_historico}
