from fastapi import FastAPI, Request
from datetime import datetime

app = FastAPI()
@app.get("/")
async def raiz():
    return {"mensagem": "API de favoritos rodando com sucesso!"}

# Lista temporária em memória para armazenar o que chegar (para testes)
banco_temporario = []

# Rota para você acessar no navegador e ver o que foi salvo
@app.get("/ver-favoritos")
async def ver_favoritos():
    return {"registros": banco_temporario}


@app.post("/receber-favoritos")
async def receber_favoritos(request: Request):
    # Tenta pegar o JSON bruto que chegou na requisição
    dados = await request.json()
    
    print("DADOS BRUTOS RECEBIDOS:", dados)

    # Retorna exatamente o que ela recebeu para você conferir na hora
    return {
        "status": "sucesso",
        "dados_que_a_api_recebeu": dados
    }
