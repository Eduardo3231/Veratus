# Superpowers (cópia fixa, 4 de 15 skills)

- Origem: https://github.com/obra/superpowers, commit `bb92a77`, licença MIT (`superpowers-LICENSE`, Jesse Vincent).
- Copiadas para `.claude/skills/` em 18/10/2026, porque o `/plugin` não existe nas sessões na nuvem:
  `systematic-debugging` (achar a causa antes de corrigir), `verification-before-completion` (prova antes de dizer "pronto"), `requesting-code-review` e `receiving-code-review` (revisão antes do commit e resposta técnica à revisão).
- Mudanças: em `systematic-debugging/SKILL.md`, as referências `superpowers:...` viraram o nome local da skill, e a que apontava para `test-driven-development` (não instalada) virou "escreva primeiro o teste pequeno que reproduz o erro".
- Ficaram de fora: `brainstorming`, `writing-plans`, `executing-plans`, `subagent-driven-development`, `dispatching-parallel-agents` e `using-git-worktrees` (o fluxo da Veratus já tem plano, trava e HANDOFF; worktree e subagentes em série gastam token), `test-driven-development` (o fundador pediu poucos testes, só os que provam a regra), `finishing-a-development-branch` (merge na `main` é deploy e precisa do fundador), `writing-skills` e `using-superpowers` (meta), `condition-based-waiting` e `defense-in-depth` (cobertos pelas regras de flag e do gate).
- O `ruff.toml` exclui `.claude/` do lint.
