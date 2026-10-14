from fastapi import FastAPI, UploadFile, File
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx
from bs4 import BeautifulSoup

app = FastAPI()

# Configuração do CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Ou coloque ["chrome-extension://lhadedhpmipelebihpenoefkakhenebl"]
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
    print("==================================="
    
    banco_temporario.append(data)
    return {"success": True, "total": len(data.get("bookmarks", []))}
# Nova rota para visualizar pelo navegador
@app.get("/ver-favoritos")
def ver_favoritos():
    return {"total_envios": len(favoritos_recebidos_historico), "dados": favoritos_recebidos_historico}
