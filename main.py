import os
import httpx
import dateparser
import spacy
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

# --- CONFIGURAÇÃO E CARREGAMENTO DO MODELO DE ML (SpaCy) ---
try:
    nlp = spacy.load("pt_core_news_sm")
except OSError:
    print("Baixando modelo do SpaCy pt_core_news_sm...")
    os.system("python -m spacy download pt_core_news_sm")
    nlp = spacy.load("pt_core_news_sm")
    
# Palavras-chave semânticas para ajudar a validar se o trecho refere-se a um evento
PALAVRAS_EVENTO = [
    "show", "palestra", "webinar", "conferência", "workshop", "lançamento", 
    "festa", "reunião", "encontro", "concerto", "festival", "seminário", 
    "curso", "evento", "stand-up", "teatro", "exposição", "Live", "fórum"
]

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
    print(f">>> INICIANDO EXTRACAO DINAMICA PARA A URL: {url}")
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
            
            texto_limpo = response.text[:5000]
            if not texto_limpo or len(texto_limpo) < 50:
                return None, None
            
            # --- PROCESSAMENTO LINGUÍSTICO COM SPACY ---
            doc = nlp(texto_limpo)
            trechos_eventos = []
            data_detectada_str = None

            for sent in doc.sents:
                frase_txt = sent.text.strip()
                
                # Verifica se a frase possui uma entidade de tempo/data estatística
                entidades_tempo = [ent for ent in sent.ents if ent.label_ in ["DATE", "TIME"]]
                
                if entidades_tempo:
                    # Verifica se a frase tem estrutura de ação (contém verbos que indicam ocorrência/agenda)
                    tem_verbo = any(token.pos_ == "VERB" for token in sent)
                    
                    # Evita falsos positivos comuns (como rodapés de copyright: "© 2026")
                    eh_copyright = any(termo in frase_txt.lower() for termo in ["todos os direitos", "copyright", "todos os direitos reservados"])
                    
                    if tem_verbo and not eh_copyright and len(frase_txt) > 20:
                        trechos_eventos.append(frase_txt)
                        
                        # Captura a primeira data válida usando o dateparser na entidade do SpaCy
                        if not data_detectada_str:
                            for ent in entidades_tempo:
                                parsed_date = dateparser.parse(ent.text, languages=['pt', 'en'])
                                if parsed_date:
                                    data_detectada_str = parsed_date.strftime('%Y-%m-%d')
                                    break

            if trechos_eventos:
                texto_final_ml = "\n---\n".join(trechos_eventos[:10])
                return texto_final_ml, data_detectada_str

            # Fallback caso a estrutura estrita não pegue sentenças isoladas
            return texto_limpo[:1500], data_detectada_str

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
def processar_links(limite: int = 10, db: Session = Depends(get_db)):
    bookmarks_pendentes = db.query(BookmarkModel).filter(
        (BookmarkModel.analisado_ml == 0) | (BookmarkModel.analisado_ml == None)
    ).limit(limite).all()
    
    print(f">>> Encontrados {len(bookmarks_pendentes)} bookmarks pendentes para processar.")
    
    if not bookmarks_pendentes:
        return {"mensagem": "Nenhum bookmark pendente para processar.", "processados_nesta_execucao": 0}

    processados = 0
    for b in bookmarks_pendentes:
        print(f">>> Processando bookmark ID {b.id} com URL: {b.url}")
        texto, data_evento = extrair_texto_e_data_de_url(b.url)
        
        if texto:
            b.extracted_text = texto
            b.event_date = data_evento
            b.analisado_ml = 1
            processados += 1
        else:
            b.analisado_ml = -1
        
        db.commit()
        
    return {"status": "concluído", "processados_nesta_execucao": processados}

@app.get("/api/bookmarks/resetar-fila")
def resetar_fila(db: Session = Depends(get_db)):
    total_atualizados = db.query(BookmarkModel).update({BookmarkModel.analisado_ml: 0})
    db.commit()
    return {"mensagem": f"Sucesso! {total_atualizados} links resetados para a fila."}


@app.get("/api/debug-url")
def debug_url(url: str):
    import requests
    from bs4 import BeautifulSoup
    
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        # Tenta fazer a requisição com um timeout de 10 segundos
        resp = requests.get(url, headers=headers, timeout=10)
        
        soup = BeautifulSoup(resp.text, "html.parser")
        for s in soup(["script", "style", "nav", "footer", "header", "aside"]):
            s.decompose()
            
        texto_limpo = soup.get_text(separator=" ", strip=True)
        
        return {
            "status_http": resp.status_code,
            "tamanho_texto": len(texto_limpo),
            "amostra": texto_limpo[:500]
        }
    except Exception as e:
        # Retorna o erro exato que aconteceu no servidor
        return {
            "erro": str(e),
            "tipo_erro": type(e).__name__,
            "mensagem": "Falha ao conectar ou processar a URL."
        }
        
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
