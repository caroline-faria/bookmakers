from fastapi import FastAPI, Request
from datetime import datetime

app = FastAPI()
@app.get("/")
async def raiz():
    return {"mensagem": "API de favoritos rodando com sucesso!"}

@app.post("/receber-favoritos")
async def receber_favoritos(request: Request):
    dados = await request.json()
    
    # Aqui você captura o carimbo de data/hora e a árvore de favoritos
    timestamp = dados.get("timestamp")
    favoritos = dados.get("favoritos")
    
    print(f"Dados recebidos em: {timestamp}")
    
    # Próximo passo: Salvar em um banco de dados ou processar com seu modelo
    # Exemplo: salvar_no_banco_ou_dataframe(favoritos)
    
    return {"status": "sucesso", "mensagem": "Favoritos coletados com sucesso!"}
