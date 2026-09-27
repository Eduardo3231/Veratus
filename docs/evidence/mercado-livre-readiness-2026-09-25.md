# Mercado Livre — prontidão até o publish plan (25/09/2026)

**Resultado: `AUTH_REQUIRED`.** Não há credencial local e o estado em produção só pode ser lido pelo fundador. Nenhuma chamada foi feita à API do Mercado Livre e nenhuma escrita aconteceu.

## Sequência oficial

| Etapa | Estado | Evidência |
| --- | --- | --- |
| Credenciais | `MISSING` localmente (não há `.env`). **Produção: desconhecido.** | `POST /os/connections/refresh` local → `credentials: MISSING` |
| Autorização (OAuth) | `AUTH_REQUIRED` | `GET /integrations/mercado-livre/oauth/start` local → 503 `oauth_configuration_incomplete` |
| Conta | `NOT_TESTED` | bloqueada pela etapa anterior |
| Categoria | `CATEGORY_DISCOVERY_BLOCKED_BY_CREDENTIALS` | idem |
| Atributos | `NOT_TESTED` | idem |
| Busca remota por SKU | `NOT_TESTED` | idem |
| Sync somente leitura | não executado | idem |
| Prontidão | `ready_for_first_live_publish: false` | consolidação do Gerente Geral |
| Publish plan | nenhum | `GET /os/publish-plans` → `{"plans": []}` |

Em produção, `GET /integrations/mercado-livre/status` sem token responde **401**: a rota existe e está protegida. Saber se já há autorização exige o token de admin, e esta rodada não autenticou em rotas administrativas.

> A rota `/integrations/mercado-livre/oauth/status` citada no prompt **não existe**. O status real fica em `/integrations/mercado-livre/status`.

## Auditoria do OAuth frente à documentação oficial

A documentação oficial foi consultada em 25/09/2026. A busca no domínio `developers.mercadolivre.com.br` funcionou, mas a leitura automática da página retornou 403.

| Item | Código | Documentação oficial | Situação |
| --- | --- | --- | --- |
| URL de autorização | `https://auth.mercadolivre.com.br/authorization` com `response_type=code`, `client_id`, `redirect_uri` e `state` | OAuth 2.0 com código de autorização | OK |
| Troca e renovação | `POST https://api.mercadolibre.com/oauth/token` (`authorization_code` e `refresh_token`) | idem | OK |
| Vida do access token | renova 60 s antes de `expires_at` | 6 horas | OK |
| Refresh token | cada renovação grava o novo `refresh_token` | uso único; um novo vem a cada renovação | OK |
| `state` | `secrets.token_urlsafe(32)`, gravado como hash, TTL entre 60 e 1800 s (Blueprint: 600), consumo único e atômico | recomendado contra CSRF | OK |
| Armazenamento | payload cifrado com Fernet (`VERATUS_TOKEN_ENCRYPTION_KEY`) no PostgreSQL | — | OK |
| Logs e erros | erros normalizados só com código HTTP; falha de persistência loga apenas o tipo do erro | — | OK |
| PKCE | **não implementado** | opcional e recomendado; vale se o app tiver PKCE ligado | **Risco:** com PKCE ligado no app, a troca do código falha. |
| Concorrência de renovação | sem lock entre processos | refresh token de uso único | Baixo: com 2 processos, uma renovação simultânea pode derrubar uma leitura, sem apagar tokens |
| Escrita de anúncio | `listing_create/update/pause: MISSING` no cliente | — | A primeira escrita exige código novo, em rodada separada |

## Instruções para o fundador (credenciais só no painel, nunca no chat ou no Git)

1. **Crie a aplicação** no DevCenter do Mercado Livre (developers.mercadolivre.com.br → "Crie uma aplicação") usando a conta vendedora da Veratus.
   - Redirect URI exatamente: `https://veratus.onrender.com/integrations/mercado-livre/oauth/callback`
   - URL de notificações: `https://veratus.onrender.com/integrations/mercado-livre/notifications`
   - Deixe **PKCE desligado** até o fluxo suportá-lo.
   - Peça leitura e acesso offline (refresh). A escrita pode ficar para depois; ativá-la mais tarde exige nova autorização.
2. **Gere a chave de cifragem** no seu computador e cole o resultado direto no painel:
   `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
   Guarde-a de forma persistente. Trocar a chave torna ilegíveis os tokens já gravados.
3. **No Render** (serviço `veratus-leads` → Environment), preencha `MERCADO_LIVRE_CLIENT_ID`, `MERCADO_LIVRE_CLIENT_SECRET` e `VERATUS_TOKEN_ENCRYPTION_KEY`. `MERCADO_LIVRE_REDIRECT_URI` já vem do Blueprint. Salvar reinicia o serviço; essa ação é sua.
4. **Inicie a autorização** com o token de admin lido de uma variável de ambiente, não digitado no comando:

   ```powershell
   $h = @{ "X-Veratus-Admin-Token" = $env:VERATUS_ADMIN_TOKEN }
   (Invoke-RestMethod "https://veratus.onrender.com/integrations/mercado-livre/oauth/start?format=json" -Headers $h).authorization_url
   ```

   Abra a URL devolvida no navegador logado na conta vendedora e autorize.
5. **Confira:** `Invoke-RestMethod "https://veratus.onrender.com/integrations/mercado-livre/status" -Headers $h` deve responder `"status": "authorized"`, com `expires_at` e sem nenhum token.

As flags continuam em `false`: a leitura (conta, categoria, atributos, busca por SKU) não depende de `MERCADO_LIVRE_ENABLED` nem de `*_PUBLISH_ENABLED`.

## Próxima rodada (depois de `authorized`)

Rodar category discovery e attribute discovery para `arctic-white` e comparar campo a campo com o Product Master. Checar se a origem internacional (PRIVATE) e o prazo são aceitos nas regras de anúncio da conta. Gerar o publish plan e **parar**. A primeira escrita (1 SKU × 1 canal × 1 escrita × 1 leitura de volta) exige confirmação explícita do fundador e o cliente de escrita, que ainda não existe.
