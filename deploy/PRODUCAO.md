# Produção

Leia este arquivo antes de acessar ou publicar na VPS. Ele é o procedimento
operacional atual, não o guia de provisionamento inicial em `deploy/README.md`.

## Ambiente atual

- VPS: `root@167.233.241.191`, com a chave local `%USERPROFILE%\.ssh\hetzner_ziggs`.
- Checkout: `/home/ziggs/ziggs`.
- Backend: `/home/ziggs/ziggs/backend`; serviço `ziggs-backend`.
- Bot em uso: `bot-v2`; serviço `ziggs-bot`.
- Frontend publicado: `/home/ziggs/ziggs/frontend/dist`, servido pelo Caddy sem restart.

## Backend

1. Rode os testes que cobrem a alteração localmente.
2. Publique somente os arquivos de backend alterados:

```powershell
powershell -ExecutionPolicy Bypass -File deploy/publish-backend.ps1 app/api/routes/render.py app/services/juicy_kill_image.py
```

3. Se a alteração inclui migration, use `-Migrate`; o script executa Alembic
   antes do restart:

```powershell
powershell -ExecutionPolicy Bypass -File deploy/publish-backend.ps1 -Migrate app/models/example.py alembic/versions/example.py
```

4. O script só conclui após `ziggs-backend` estar ativo e `/health` responder
   `{"status":"ok"...}`. Confira logs quando o comportamento depender de worker:

```powershell
ssh -o IdentitiesOnly=yes -i "$env:USERPROFILE\.ssh\hetzner_ziggs" root@167.233.241.191 "journalctl -u ziggs-backend -n 50 --no-pager"
```

O script aceita caminhos relativos a `backend/`. Ele envia arquivos a um
diretório temporário e usa `install`; não faz commit, pull, nem envia `.env`.
Isso permite hotfixes ainda não commitados sem copiar o worktree inteiro.

## Frontend e bot

O script acima não publica frontend nem bot. Para esses casos, siga as seções
"Atualizar código" de `deploy/README.md` e valide o serviço correspondente.
Não reinicie `ziggs-backend` por uma alteração somente de frontend; não reinicie
`ziggs-bot` por uma alteração somente de backend.

Quando ambos precisarem reiniciar, a ordem é obrigatória: reinicie o
`ziggs-backend`, aguarde o healthcheck retornar `ok` e só então reinicie o
`ziggs-bot`. O bot depende da API do backend para iniciar o trabalho.

## Regras

- Não publique sem testar a mudança localmente.
- Não copie o repositório inteiro, `data/`, `.env` ou diretórios de ambiente
  virtual para a VPS.
- Não declare deploy concluído sem o healthcheck e, quando aplicável, logs do
  worker afetado.
- Alterações já postadas no Discord não mudam retroativamente; trate isso como
  reparo separado, não como confirmação de que o deploy falhou.
