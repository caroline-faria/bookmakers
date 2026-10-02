from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()

# Lista temporária em memória para acumular os favoritos recebidos
banco_temporario = []

# Rota raiz (para evitar Not Found ao abrir o link principal)
@app.get("/")
async def raiz():
    return {
        "status": "online",
        "mensagem": "API de favoritos rodando com sucesso!",
        "rotas_disponiveis": [
            "POST /receber-favoritos",
            "GET /baixar-favoritos"
        ]
    }

@app.post("/receber-favoritos")
async def receber_favoritos(request: Request):
    try:
        dados = await request.json()
    except Exception:
        return {"status": "erro", "mensagem": "O corpo da requisição não é um JSON válido."}
    
    # Adiciona os dados recebidos na lista
    banco_temporario.append(dados)
    
    print(f"Favoritos recebidos. Total acumulado: {len(banco_temporario)}")

    return {
        "status": "sucesso", 
        "mensagem": "Favoritos coletados com sucesso!",
        "dados_recebidos": dados
    }

@app.get("/baixar-favoritos")
async def baixar_favoritos():
    return JSONResponse(
        content={"favoritos_acumulados": banco_temporario},
        headers={"Content-Disposition": "attachment; filename=favoritos_backup.json"}
    )
