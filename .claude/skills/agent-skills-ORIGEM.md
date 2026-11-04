# agent-skills (Addy Osmani): 25 skills de engenharia

- Origem: https://github.com/addyosmani/agent-skills, pasta `skills/`, commit `301b02b` (01/11/2026), licença MIT (`agent-skills-LICENSE`).
- Instaladas em 04/11/2026, como no original: `api-and-interface-design`, `browser-testing-with-devtools`, `ci-cd-and-automation`, `code-review-and-quality`, `code-simplification`, `constraint-driven-development`, `context-engineering`, `debugging-and-error-recovery`, `deprecation-and-migration`, `documentation-and-adrs`, `doubt-driven-development`, `frontend-ui-engineering`, `git-workflow-and-versioning`, `idea-refine`, `incremental-implementation`, `interview-me`, `observability-and-instrumentation`, `performance-optimization`, `planning-and-task-breakdown`, `security-and-hardening`, `shipping-and-launch`, `source-driven-development`, `spec-driven-development`, `test-driven-development`, `using-agent-skills`.
- Não instalados: `agents/`, `commands/`, `hooks/` e `plugin.json` do repositório (comandos e hooks de plugin; rodariam scripts a cada edição). Os dois scripts que vêm dentro dos skills são locais e curtos (`idea-refine/scripts/idea-refine.sh` cria `docs/ideas`; `constraint-driven-development/scripts/quality-gate.mjs` roda a checagem de qualidade, sem rede).
- **As regras da Veratus (`CLAUDE.md`, `AGENTS.md`) valem mais que qualquer skill.** Pontos em que eles se chocam:
  - `test-driven-development`: o fundador pediu poucos testes, um por regra; não escreva teste antes de cada função.
  - `git-workflow-and-versioning`, `ci-cd-and-automation` e `shipping-and-launch`: push só na branch da sessão; merge na `main` é deploy e precisa do fundador no momento; não criar cron nem webhook nos painéis.
  - `using-agent-skills`: não exige chamar skill antes de cada resposta (economia de token).
  - `interview-me` e `idea-refine`: o fundador prefere respostas curtas e objetivas; só use quando ele pedir para detalhar uma ideia.
  - `documentation-and-adrs`: decisões e rodadas vão para `docs/HANDOFF.md`.
- Custo: cada skill instalada põe a descrição na lista do início da sessão. Se o contexto apertar, apague as pastas que não usa.
- **Uso (decisão do fundador, 04/11/2026):** nenhuma skill foi removida; as regras e os fluxos delas só entram quando o fundador pedir. As "Regras que não mudam" do `CLAUDE.md` continuam valendo sempre.
