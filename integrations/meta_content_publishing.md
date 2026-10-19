# Publicação social pela Meta

Publicar sai só pelo agendador social (`veratus_agents/social_scheduler.py`).
`integrations/meta_content_publisher.py` guarda as chamadas da API oficial
(contêiner, espera, publicação e leitura de volta) e não publica sozinho: a
opção antiga `--publish` foi desligada, porque pulava `SOCIAL_PUBLISH_ENABLED`
e a aprovação semanal.

## Pré-requisitos

1. Instagram profissional conectado a uma Página da Meta.
2. Aplicativo da Meta com permissão de publicação de conteúdo
   (`instagram_content_publish`, `pages_manage_posts`) e de métricas
   (`instagram_manage_insights`, `read_insights`).
3. Token e IDs só no ambiente (Render), nunca no repositório:
   `META_PAGE_ACCESS_TOKEN`, `META_IG_BUSINESS_ACCOUNT_ID`, `META_PAGE_ID`,
   `META_GRAPH_API_VERSION`.

## Fluxo

```bash
python -m veratus_agents.social_scheduler validate social/calendario-2026-11.json
python -m veratus_agents.social_scheduler approve-media landing/social/posts/2026-11/*.jpg --format IMAGE
python -m veratus_agents.social_scheduler import social/calendario-2026-11.json
python -m veratus_agents.social_scheduler request-approval
python -m veratus_agents.social_scheduler run-due   # prévia enquanto a flag estiver desligada
```

- Só sai mídia do manifesto `social/midia-aprovada.json` (o sha256 do que a URL
  devolve precisa bater com o aprovado).
- Reel: um MP4 (`video/mp4`) e, se quiser, uma capa JPEG no mesmo item.
- Story com figurinha (enquete, pergunta, link) vira tarefa no painel: a API
  não põe figurinha.
- Cada canal publicado fica gravado na hora; uma nova tentativa não repete o
  Instagram se só o Facebook falhou.

## Aposentado

`social/publishing-payloads.json` e `social/fila-publicacao.csv` eram a fila
manual antiga (URLs que hoje dão 404). Nada mais os lê para publicar; a CLI só
valida um arquivo de payloads se ele for passado com `--payloads`.

## Limites

- Música da biblioteca do Instagram e destaques do perfil: só no aplicativo.
- TikTok segue manual até a auditoria do app.
