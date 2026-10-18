# Graphify (cópia fixa)

- Origem: pacote `graphifyy==0.9.92` (PyPI), projeto Graphify-Labs/graphify, licença Apache-2.0 (`graphify-LICENSE`). A pasta `graphify/` é o que `graphify install` gera.
- Uso na Veratus: só o grafo do código, local e sem LLM (`graphify extract . --code-only` e `graphify cluster-only . --no-label`). Custo de token zero; nada sai da máquina.
- O hook de sessão instala a versão fixada e reconstrói o grafo em `graphify-out/` (fora do Git). `.graphifyignore` deixa de fora runtime, mídia, design e `founder/`.
- Para economizar token: `graphify query "pergunta"`, `graphify explain Simbolo` e `graphify affected arquivo.py` antes de abrir arquivos grandes.
