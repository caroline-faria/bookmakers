from fastapi import FastAPI, Request
from datetime import datetime

app = FastAPI()
@app.get("/")
async def raiz():
    return {"mensagem": "API de favoritos rodando com sucesso!"}

# Lista temporária em memória para armazenar o que chegar (para testes)
banco_temporario = []

@app.post("/receber-favoritos")
async def receber_favoritos(request: Request):
    dados = await request.json()
    
    timestamp = dados.get("timestamp")
    favoritos = dados.get("favoritos")

    # Imprime os dados detalhados no console do Render
    print(f"--- DADOS RECEBIDOS ---")
    print(f"Timestamp: {timestamp}")
    print(f"Favoritos: {favoritos}")
    
    # Salva na lista temporária
    banco_temporario.append(dados)

    return {
        "status": "sucesso", 
        "mensagem": "Favoritos coletados com sucesso!",
        "total_recebido": len(favoritos) if favoritos else 0
    }

# Rota para você acessar no navegador e ver o que foi salvo
@app.get("/ver-favoritos")
async def ver_favoritos():
    return {"registros": banco_temporario}
