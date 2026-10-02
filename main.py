from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import json

app = FastAPI()

# Lista temporária em memória para acumular os favoritos recebidos
# (Lembre-se: se o Render reiniciar, essa lista zera)
banco_temporario = []

@app.post("/receber-favoritos")
async def receber_favoritos(request: Request):
    try:
        dados = await request.json()
    except Exception:
        return {"status": "erro", "mensagem": "O corpo da requisição não é um JSON válido."}
    
    # Adiciona os dados recebidos na nossa lista em memória
    banco_temporario.append(dados)
    
    print(f"Favoritos recebidos e armazenados temporariamente. Total de registros: {len(banco_temporario)}")

    return {
        "status": "sucesso", 
        "mensagem": "Favoritos coletados com sucesso!",
        "dados_recebidos": dados
    }

# NOVA ROTA: Para baixar os dados salvos em formato JSON
@app.get("/baixar-favoritos")
async def baixar_favoritos():
    # Retorna os dados formatados como um arquivo JSON para download direto no navegador
    return JSONResponse(
        content={"favoritos_acumulados": banco_temporario},
        headers={"Content-Disposition": "attachment; filename=favoritos_backup.json"}
    )
