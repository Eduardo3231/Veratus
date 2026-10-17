# Instagram agent skill (cópia fixa)

- Origem: https://github.com/Jakeschincariol/instagram-agent-skill, commit `d03c56b`, licença MIT (`instagram-agent-skill-LICENSE`).
- As 13 skills de `skills/` foram copiadas para `.claude/skills/ig-*` em 17/10/2026. Os comandos são `/ig-plan`, `/ig-reel`, `/ig-caption`, `/ig-carousel`, `/ig-story`, `/ig-reply`, `/ig-comment`, `/ig-dm`, `/ig-profile`, `/ig-audit`, `/ig-viral`, `/ig-repurpose` e `/ig-human`.
- Só escrevem rascunhos: não postam, não comentam e não mandam DM. Os scripts Python não acessam a rede.
- Única mudança: um aviso no topo de `ig-dm/SKILL.md` com a regra da Veratus (sem DM fria; só a quem escreveu ou comentou, na janela de 24 h).
- A voz que as skills leem (`~/.claude/instagram/voice.md`) é `social/instagram/voice.md`, ligada pelo hook de sessão.
- O `ruff.toml` exclui `.claude/` do lint, para a cópia continuar igual à original.
