# API de catálogo Veratus

API FastAPI local de consulta. Os nove modelos vêm de `catalog/products.json`, a mesma fonte usada pelo Sales Agent. Preço, estoque e condições comerciais não estão nesse catálogo. A integração opcional com Gemini permanece em `POST /gemini`. Os endpoints do Sales Agent estão no serviço Flask `integrations/webhook.py`, documentados em `integrations/agent_api.md`.

## Executar localmente

Na raiz do projeto:

```powershell
.\.venv\Scripts\python.exe -m pip install -r api\requirements.txt
.\.venv\Scripts\python.exe -m uvicorn api.main:app --reload --port 8000
```

A API estará disponível em `http://127.0.0.1:8000`. A documentação interativa fica em `http://127.0.0.1:8000/docs`.

## Endpoints

### `GET /health`

Verifica se a API está respondendo.

### `GET /produtos`

Retorna os nove modelos de `catalog/products.json`. A landing pode exibir uma seleção menor sem apagar o produto do Product Master.

### `GET /produtos/{id}`

Recebe o ID textual na URL, por exemplo `ocean-blue`, procura o produto no catálogo e retorna:

- `200` com o produto encontrado;
- `404` com a mensagem `Produto não encontrado` quando o ID não existe.

### `POST /gemini`

Envia `{ "input": "..." }` ao Gemini usando `GEMINI_API_KEY` e a ferramenta de pesquisa do Google. Configure a chave somente no ambiente do servidor; a API retorna `503` quando ela não está configurada.

```powershell
$env:GEMINI_API_KEY = "sua-chave"
Invoke-RestMethod -Method Post http://127.0.0.1:8000/gemini -ContentType "application/json" -Body '{"input":"Resuma a coleção Veratus."}'
```

## Caminho de uma requisição

1. O cliente envia uma requisição HTTP, por exemplo `GET /produtos/ocean-blue`.
2. O FastAPI encontra a função decorada com `@app.get("/produtos/{product_id}")`.
3. O valor `ocean-blue` é recebido no parâmetro `product_id`.
4. A função procura o item correspondente no catálogo compartilhado.
5. O dicionário do produto vira automaticamente uma resposta JSON.
6. Se não houver correspondência, `HTTPException` cria a resposta `404`.

## Testes

```powershell
.\.venv\Scripts\python.exe -m pytest api\test_main.py -q
```

Esta API não está incluída no serviço Flask do `render.yaml`. A página pública e o atendimento atual não dependem dela.
