# Publicação social pela Meta

O publicador usa o fluxo oficial em duas etapas: cria um contêiner de mídia, espera o processamento e publica no perfil profissional.

## Pré-requisitos

1. Instagram profissional conectado a uma Página da Meta.
2. Aplicativo da Meta com permissão de publicação de conteúdo.
3. Token e ID armazenados no ambiente, sem colocar valores no repositório:

```powershell
$env:META_PAGE_ACCESS_TOKEN="..."
$env:META_IG_BUSINESS_ACCOUNT_ID="..."
$env:META_GRAPH_API_VERSION="v25.0"
```

Use a versão da API liberada no aplicativo da Meta; o código permite alterá-la pela variável de ambiente.

## Validar a fila

```powershell
python integrations/meta_content_publisher.py
```

O comando consulta os arquivos públicos, confere o formato e não publica.

## Publicar uma peça

```powershell
python integrations/meta_content_publisher.py --order 1 --publish
```

O resultado fica registrado em `social/publishing-state.json`, com o identificador e o endereço retornados pela Meta. A fila deve ser enviada de 1 a 9 para formar a grade planejada.

## Limites desta automação

- O Instagram precisa estar conectado com as permissões necessárias.
- Os arquivos precisam estar disponíveis em `https://veratus.onrender.com/social/`.
- O Reel é publicado no feed e na aba de Reels.
- Música da biblioteca do Instagram deve ser escolhida no aplicativo quando necessário.
- TikTok precisa de conexão própria; o mesmo MP4 já está preparado para esse canal.
