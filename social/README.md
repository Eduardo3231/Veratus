# Operação social — Veratus

Pacote preparado para organizar Instagram, reaproveitar vídeos no TikTok e conduzir o interesse para o site e o WhatsApp.

## Arquivos

- `auditoria-e-estrategia.md`: diagnóstico, referências e sistema editorial.
- `../branding/profile-kit.md`: bio, destaques, respostas rápidas e configuração do perfil.
- `legendas-prontas.md`: nove legendas finais na ordem correta de postagem.
- `stories-14-dias.md`: roteiro diário de Stories.
- `medicao.csv`: acompanhamento do que gera visitas, conversas e vendas
  (`python -m veratus_agents.social_scheduler report --week AAAA-Wnn --csv`).
- `feed-preview.html`: prévia visual da grade 3 × 3.
- `assets/highlights/`: cinco capas de destaques.
- `instagram/voice.md` e `instagram/slop-ptbr.json`: voz da marca e léxico
  pt-BR das skills ig-* (o agendador usa o léxico como aviso na aprovação).
- `../veratus_agents/social_scheduler.py`: o agendador (validação, importação,
  aprovação semanal, publicação e métricas).

Ainda não existem (pacote do fundador): o calendário do mês
(`calendario-AAAA-MM.json`), as artes em `landing/social/posts/<mês>/`, os MP4
dos Reels e o manifesto `midia-aprovada.json`. Aposentados: `fila-publicacao.csv`
e `publishing-payloads.json` (fila manual antiga, com URLs que dão 404).

## Aplicação

1. Atualizar nome, bio, link e botão de contato.
2. Subir as cinco capas e criar os destaques na ordem definida.
3. Conferir o pacote com `python -m veratus_agents.social_scheduler validate <arquivo>`,
   aprovar as mídias com `approve-media` e importar com `import`.
4. Aprovar a semana (ou rejeitar item por item) antes de qualquer publicação.
5. As métricas saem em +24 h e +7 dias (Story em +20 h) com `insights`.

As artes precisam estar em `landing/social/posts/<mês>/` para que a Meta as busque
por HTTPS; hoje essa pasta não existe.

O pacote não presume valores, estoque, prazos ou características que ainda precisam ser confirmados para cada item.
