# Plano — Perfil customizado v2

Melhorias no perfil customizado (tema/avatar/banner): aplicação instantânea sem
reload, crop de imagem, limites de tamanho/dimensão, suporte a GIF animado e
sistema de conta main/alt. Escrito para ser executado por qualquer IA/dev em
workstreams independentes — cada um tem arquivos exatos, contrato de API e
critério de aceite. Leia `CLAUDE.md` antes.

## Estado atual (jul/2026) — leia antes de mexer

- **Backend:** `app/services/user_profile.py` (serviço) + `app/api/routes/user_profile.py` (rotas `/profile/*`). Upload passa por Pillow, limite único de 5 MB, downscale pra `_MAX_SIZE = {"avatar": (512,512), "banner": (1920,600)}`, salva SEMPRE como `{user_id}/{kind}.jpg` (JPEG q88). URL pública `/profile/image/{kind}/{user_id}?v={mtime}` (o `?v=` é cache-busting — não remova).
- **Frontend:** `ClaimsPanel.tsx` → componente interno `ProfileCustomize` (dropdown do usuário). Preview no dropdown JÁ atualiza na hora (`setProfile(await action())`); o que NÃO atualiza sem reload é a `PlayerProfilePage` aberta atrás.
- **PlayerProfilePage.tsx:** recebe `profile.custom_profile` (`{theme, avatar_url, banner_url}`) embutido no payload do perfil por `app/api/routes/players.py`, que chama `user_profile.get_public_customization(db, albion_player_id)`. Tema aplicado via `data-profile-theme` no wrapper (linha ~826). Banner: `<img className="absolute inset-0 h-full w-full object-cover opacity-25">`. Avatar: `<PlayerAvatar customUrl=...>`.
- **Registro:** `RegisteredCharacter` (`app/models/claims.py`) — 1 linha por personagem verificado, `user_id` FK, `albion_player_id` UNIQUE. Criado/upsertado por `app/services/claim_checker.py` (~linha 105). Um usuário pode ter N personagens.
- **Estilo do frontend:** Tailwind v4 (via `@tailwindcss/vite`) nos componentes novos, apesar do CLAUDE.md citar CSS puro — siga o que `ClaimsPanel.tsx` faz.
- **Migrações:** Alembic, última revision = `o1e6a9b4d8f3` (`backend/alembic/versions/o1e6a9b4d8f3_gold_price_snapshots.py`). Nova migração deve usar `down_revision = 'o1e6a9b4d8f3'` (confira antes — pode ter mudado).
- **Testes:** `backend/tests/test_*.py`, estilo função + `__main__` (roda sem pytest). Siga `tests/test_search_index.py` como modelo.

---

## Workstream A — Backend: upload com crop, limites e GIF

**Arquivos:** `app/services/user_profile.py`, `app/api/routes/user_profile.py`,
`backend/tests/test_profile_upload.py` (novo).

### A1. Novos limites (constantes no serviço)

```python
_MAX_BYTES = {"avatar": 25 * 1024 * 1024, "banner": 100 * 1024 * 1024}
_MIN_SIZE  = {"avatar": (128, 128), "banner": (320, 100)}   # dimensão MÍNIMA da fonte (banner baixo de propósito — muitos GIFs são pequenos)
_MAX_SIZE  = {"avatar": (512, 512), "banner": (1920, 600)}  # já existe, mantém
_MAX_GIF_FRAMES = 400  # acima disso, trunca (não rejeita)
```

Validação de dimensão mínima é sobre a imagem ORIGINAL (antes do crop). Erros
são `ProfileServiceError` com mensagem em PT (padrão atual), ex.:
`"imagem grande demais (limite 25 MB)"`, `"imagem muito pequena (mínimo 128×128)"`.

### A2. Crop server-side

O frontend manda o retângulo de crop como frações 0..1 da imagem original
(form fields junto do file): `crop_x, crop_y, crop_w, crop_h` (floats,
opcionais — ausentes = sem crop, comportamento atual). No serviço:

```python
def _save_image(kind, user_id, image_bytes, crop: tuple[float,float,float,float] | None) -> str:
    # 1. valida bytes (limite por kind)
    # 2. Image.open + load() — try/except vira ProfileServiceError (inclui
    #    DecompressionBombError do Pillow, que já limita ~178MP por padrão)
    # 3. valida _MIN_SIZE contra img.size
    # 4. se crop: converte frações → px (clamp em [0, w]×[0, h]; exige w,h > 0
    #    senão ProfileServiceError "crop inválido")
    # 5. despacha: GIF animado → _save_gif, resto → caminho JPEG atual
```

**Por que crop no servidor e não no cliente:** cropar GIF animado no browser
exigiria decodificar + re-encodar GIF em JS (gifuct + encoder, pesado e
frágil). Com Pillow é um loop de frames. O cliente só ESCOLHE o retângulo.

### A3. GIF animado

Detectar: `img.format == "GIF" and getattr(img, "is_animated", False)`.

```python
def _save_gif(img, kind, crop_px, out_path):
    frames, durations = [], []
    for i, frame in enumerate(ImageSequence.Iterator(img)):
        if i >= _MAX_GIF_FRAMES: break
        f = frame.convert("RGBA")          # Pillow ≥9.1 já entrega frame composto
        if crop_px: f = f.crop(crop_px)
        f.thumbnail(_MAX_SIZE[kind], Image.LANCZOS)
        frames.append(f.convert("P", palette=Image.ADAPTIVE))
        durations.append(frame.info.get("duration", 100))
    frames[0].save(out_path, "GIF", save_all=True, append_images=frames[1:],
                   duration=durations, loop=img.info.get("loop", 0), disposal=2)
```

- Nome do arquivo agora varia: `{kind}.gif` ou `{kind}.jpg`. Ao salvar um,
  **delete o outro** (senão troca de gif→jpg deixa o gif órfão e
  `_remove_existing` só apaga o path novo). O path relativo já vai pro DB, e
  `FileResponse` infere o media type da extensão — nada a mudar no serve.
- GIF estático (1 frame) pode cair no caminho JPEG normal.

### A4. Rotas

- Adicionar aos endpoints de upload os form fields opcionais:
  `crop_x: float | None = Form(None)` etc. Repassar ao serviço.
- **Trocar `async def` por `def`** nos dois uploads (e ler via
  `file.file.read()`): re-encodar um GIF de 100 MB leva segundos e hoje
  bloquearia o event loop (asyncio roda battle_tracker etc. no mesmo loop).
  FastAPI roda rota sync em threadpool automaticamente — é a correção de uma
  linha.
- Validação de tamanho ANTES de decodificar (`len(data) > _MAX_BYTES[kind]`).

### A5. Teste (`tests/test_profile_upload.py`)

Estilo `test_search_index.py` (roda direto). Gerar imagens em memória com
Pillow. Casos mínimos: rejeita > limite; rejeita menor que `_MIN_SIZE`; crop
aplicado (verifica dimensão do output); GIF animado in → output `.gif` com
`is_animated=True` e nº de frames preservado; trocar gif→jpg não deixa órfão.

**Aceite A:** todos os testes passam; upload sem crop continua funcionando
igual (retrocompatível — o frontend velho não manda crop).

---

## Workstream B — Frontend: crop UI, validação e GIF

**Arquivos:** `frontend/src/components/CropModal.tsx` (novo),
`ClaimsPanel.tsx` (ProfileCustomize), `api.ts`, `src/i18n/index.ts`.

**Dependência nova (única): `react-easy-crop`** (~10 KB, pan/zoom/touch
prontos). Justificativa: crop com pointer events + pinch + clamp de bounds
hand-rolled é ~200 linhas de bug farm; a lib entrega o retângulo em px sobre
`<img>` nativo — que é exatamente o que preserva a animação do GIF no preview
(browser anima `<img src=blob:...>` sozinho, custo zero).

### B1. Fluxo de upload novo (em `ProfileCustomize`)

1. Usuário escolhe arquivo (`accept="image/png,image/jpeg,image/webp,image/gif"`).
2. **Validação client-side imediata** (feedback rápido; o servidor revalida —
   trust boundary é lá): `file.size` contra 25/100 MB; dimensões via
   `URL.createObjectURL` + `<img onLoad>` (`naturalWidth/Height`) contra o
   mínimo. Falhou → mensagem no `err` existente, nem abre o modal.
3. Abre `<CropModal>`:
   - `react-easy-crop` com `image={objectUrl}`, `aspect={1}` pra avatar,
     `aspect={16/5}` pra banner (1920×600), zoom por scroll/pinch.
   - GIF: o `<img>` interno da lib anima nativamente — requisito "ver o gif
     rodando no crop" sai de graça.
   - Confirmar → `onCropComplete` dá `croppedAreaPixels`; normalizar pelas
     dimensões naturais → frações 0..1.
4. Upload: `api.uploadProfileAvatar(file, crop)` — FormData ganha
   `crop_x/y/w/h`. Resposta já é o `MyProfile` novo → `setProfile` (preview
   do dropdown atualiza como hoje).

### B2. api.ts

```ts
export interface CropRect { x: number; y: number; w: number; h: number } // frações 0..1
uploadProfileAvatar: (file: File, crop?: CropRect) => { ...form.append("crop_x", ...) }
```

### B3. i18n

Novas chaves (PT/EN/ES) em `src/i18n/index.ts`: `cropTitle`, `cropConfirm`,
`cropCancel`, `imgTooBig` (com placeholder do limite), `imgTooSmall`,
`setAsMain`, `mainAccount`, `altOfMain` (as 3 últimas são do workstream D).

**Aceite B:** enviar PNG retangular como avatar abre crop quadrado, resultado
aparece no dropdown sem reload; GIF animado anima no modal de crop e no
preview após upload; arquivo de 30 MB como avatar é recusado antes do modal.

---

## Workstream C — Aplicação instantânea na PlayerProfilePage

**Arquivos:** `ClaimsPanel.tsx`, `PlayerProfilePage.tsx`. Sem backend.

O dropdown já atualiza na hora; falta a página de perfil aberta atrás. Sem
estado global no projeto (CLAUDE.md) — não introduza um. Use CustomEvent:

1. `ClaimsPanel` (pai) tem a lista `registered`; passe
   `playerIds={registered.map(r => r.albion_player_id)}` pro
   `ProfileCustomize`.
2. Em `apply()` (ProfileCustomize), após `setProfile(next)`:
   ```ts
   window.dispatchEvent(new CustomEvent("ziggs:profile-updated",
     { detail: { profile: next, playerIds } }));
   ```
3. `PlayerProfilePage`: `useEffect` com listener; se o `albion_player_id` do
   perfil exibido ∈ `playerIds`, faz merge:
   ```ts
   setProfile(p => p ? { ...p, custom_profile: { ...p.custom_profile, ...detail.profile } } : p);
   ```
   Tema (`data-profile-theme`), banner e avatar re-renderizam sozinhos — as
   URLs novas já vêm com `?v={mtime}` novo, então o browser busca a imagem
   nova sem hard refresh.

Cuidado: o merge deve preservar campos extras de `custom_profile` que o
workstream D adiciona (`main_character`) — por isso spread do existente antes.

**Aceite C:** com o próprio perfil aberto, trocar tema/avatar/banner no
dropdown reflete na página em <1s, sem reload. Trocar customização NÃO afeta
página de perfil de outro jogador aberta.

---

## Workstream D — Conta main / alts

**Arquivos:** `app/models/claims.py`, nova migração alembic,
`app/api/routes/claims.py`, `app/services/claim_checker.py`,
`app/services/user_profile.py`, `ClaimsPanel.tsx`, `PlayerProfilePage.tsx`,
`api.ts`, i18n.

### D1. Modelo + migração

`RegisteredCharacter` ganha:
```python
is_main: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sa.false())
```
Migração alembic (`down_revision = 'o1e6a9b4d8f3'` — CONFIRA a head atual):
`add_column` + backfill: para cada `user_id`, marca `is_main=1` no registro
mais antigo (`MIN(registered_at)`) — todo usuário existente já sai com main
definida. SQL puro no upgrade é suficiente:
```sql
UPDATE registered_characters SET is_main = 1 WHERE id IN (
  SELECT MIN(id) FROM registered_characters GROUP BY user_id)
```

### D2. Regras

- Usuário com 1 personagem: ele é a main (invariante garantida pelo backfill
  + D3). Nenhuma mensagem de alt aparece em lugar nenhum.
- `claim_checker.py` (~linha 105, onde cria `RegisteredCharacter`): ao criar,
  `is_main = (nenhum outro RegisteredCharacter do user)` — primeiro
  personagem verificado vira main automaticamente. No ramo de upsert
  (personagem re-verificado por OUTRO user), o `is_main` antigo pertence ao
  dono anterior: zere `existing.is_main` e aplique a mesma regra do "é o
  primeiro?" pro novo dono. Cuidado com o caso do dono anterior ficar sem
  main: promova o registro mais antigo restante dele.
- Endpoint novo em `claims.py`:
  ```
  PUT /claims/main/{registered_id}  (require_user)
  → 404 se não é do user; senão is_main=False em todos do user, True no alvo.
  → devolve o mesmo shape do GET /claims/my
  ```
- `_reg_dict` ganha `"is_main": r.is_main`.

### D3. Exposição no perfil público

`get_public_customization` (user_profile.py) já carrega o `RegisteredCharacter`
do personagem visto. Adicionar ao dict retornado:
```python
"is_main": reg.is_main,
"main_character": None if reg.is_main else (
    {"name": m.albion_player_name, "region": m.region}
    if (m := db.scalar(select(RegisteredCharacter).where(
        RegisteredCharacter.user_id == reg.user_id,
        RegisteredCharacter.is_main == True))) else None
),
```
Flui automático pro frontend via `players.py` → `custom_profile`.

### D4. Frontend

- `ClaimsPanel` lista de registrados: quando `registered.length > 1`, ícone
  estrela por personagem (`ti-star` preenchida na main, `ti-star` outline nas
  alts, clique chama `api.setMainCharacter(id)` e atualiza a lista com a
  resposta). Com 1 personagem, não mostra estrela.
- `PlayerProfilePage` header: se `profile.custom_profile?.main_character`,
  renderiza badge/linha discreta abaixo do nome:
  `{t("altOfMain")} <link>{main.name}</link>` — link navega
  `/${REGION_PREFIX[main.region]}/${encodeURIComponent(main.name)}` (mesmo
  padrão de navegação do ClaimsPanel linha ~334).
- Tipos: `CustomProfile` em `PlayerProfilePage.tsx` (linha ~90) e `MyProfile`
  não mudam de shape obrigatório — `main_character` é opcional.

**Aceite D:** usuário com 2 personagens vê estrelas no dropdown; trocar a main
persiste; perfil da alt mostra "alt de X" com link pro perfil da main; perfil
da main não mostra nada; usuário com 1 personagem não vê estrela nem mensagem.

---

## Status (jul/2026)

- **C — FEITO.** Evento `ziggs:profile-updated` disparado em `apply()`
  (ProfileCustomize) com `{profile, playerIds}`; listener + merge na
  PlayerProfilePage.
- **B — FEITO (frontend).** `CropModal.tsx` criado (react-easy-crop
  instalado), validação client-side de MB/dimensão mínima, GIF aceito e
  animando no crop, campos `crop_x/y/w/h` já são ENVIADOS no upload. O
  backend ainda os ignora (FastAPI descarta form fields não declarados) —
  o crop só terá efeito real quando A for implementado.
- **D — FEITO só o frontend.** Estrela de main no dropdown (aparece com 2+
  personagens; `is_main` chega undefined até o backend expor — todas
  outline, clique num PUT que ainda 404a é silenciosamente ignorado) e
  badge "Alt de X" no perfil (só renderiza quando `custom_profile.
  main_character` existir no payload). i18n PT/EN/ES completo.
- **A — FEITO.** `_save_image` com limites 25/100 MB, mínimos 128×128 /
  320×100, crop server-side (`_crop_box`) e GIF animado frame a frame
  (`_save_gif`, extensão .gif preservada, irmã .jpg/.gif apagada na troca).
  Rotas de upload viraram `def` sync (threadpool) com form fields
  `crop_x/y/w/h`. Testes: `tests/test_profile_upload.py` (7 casos).
- **D1–D3 — FEITO.** Coluna `is_main` (migração `p2a7c4d8f1b6`, backfill
  do mais antigo por usuário, aplicada no dev DB), lógica de registro
  extraída pra `claim_checker.register_character` (primeiro char = main,
  roubo promove a main do dono anterior — `tests/test_claims_main.py`,
  5 casos), `PUT /claims/main/{id}`, `is_main` no `_reg_dict` e
  `main_character` no `get_public_customization`.

**Plano completo — nada pendente.** Em prod, rodar `alembic upgrade head`.

## Ordem e paralelismo

- **A, C e D são independentes entre si** — podem rodar em paralelo.
- **B depende de A** (form fields de crop) — mas o backend é retrocompatível,
  então B pode começar pelo modal/validação e ligar o crop por último.
- Contratos fixos entre workstreams (não renegociar sem atualizar este doc):
  1. Form fields de crop: `crop_x, crop_y, crop_w, crop_h`, frações 0..1 da
     imagem original, opcionais.
  2. Evento: `"ziggs:profile-updated"`, detail `{ profile: MyProfile, playerIds: string[] }`.
  3. `custom_profile.main_character`: `{name: string, region: string} | null`.
  4. Limites: avatar 25 MB / mín 128×128; banner 100 MB / mín 320×100.

## Fora de escopo (decidido, não esquecido)

- Compressão/transcode de GIF pra WebP/MP4 animado (economizaria banda; fazer
  se banners de dezenas de MB virarem problema real de serving).
- Crop client-side de verdade (re-encode no browser) — servidor cobre.
- Estado global (zustand etc.) pra sincronizar perfil — CustomEvent basta pros
  2 pontos que existem.
