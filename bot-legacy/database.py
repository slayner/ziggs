import aiosqlite
import os
import time
import asyncio
import contextvars
from pathlib import Path
from contextlib import asynccontextmanager, contextmanager

# ==================== MULTI-TENANT: UM BANCO POR SERVIDOR ====================
# Cada servidor (guild) tem seu PRÓPRIO arquivo de banco em data/guild_<id>.db.
# Nada é compartilhado entre servidores. Um registro GLOBAL (data/registry.db)
# guarda apenas a lista de servidores ativados (/ativar).
#
# Qual banco usar é resolvido por um ContextVar setado no ponto de entrada
# (comando / interação / loop / listener). Se nenhum servidor estiver no contexto,
# _db() levanta erro DE PROPÓSITO — falha barulhenta em vez de vazar/misturar dados.
#
# BOT_DB_DIR permite isolar o diretório (usado por testes e pra apontar pra um
# volume persistente no host). Default: pasta 'data' DENTRO do app — fora dela
# (ex.: '../data') o host costuma negar escrita (PermissionError).
_DATA_DIR = os.getenv('BOT_DB_DIR') or os.path.join(os.path.dirname(__file__), 'data')
_GLOBAL_DB_PATH = os.path.join(_DATA_DIR, 'registry.db')
_GLOBAL_KEY = '__global__'

# Conexões por banco. CADA conexão aiosqlite roda em sua PRÓPRIA thread — então o
# nº de threads = (conexões abertas) × (servidores com pool vivo). Para escalar a
# MUITAS guildas o pool é LAZY: abre 1 conexão (baseline) e cresce sob demanda até
# _POOL_SIZE; conexões ociosas além de _POOL_MIN são fechadas após _POOL_IDLE_TTL.
# Em repouso → ~1 thread/servidor (não _POOL_SIZE). Env: BOT_DB_POOL_SIZE.
_POOL_SIZE = max(1, int(os.getenv('BOT_DB_POOL_SIZE') or 4))   # teto de conexões simultâneas/banco
_POOL_MIN = 1                # conexões mantidas vivas mesmo ociosas (baseline por banco)
_POOL_IDLE_TTL = 60.0        # s sem uso → conexões ALÉM do mínimo são fechadas (libera threads)
_pools: dict = {}            # key (guild_id | _GLOBAL_KEY) -> _ConnPool
_pools_lock = asyncio.Lock()
_econ_cache: dict = {}       # gid -> (expira_monotonic, cfg): cache curto de load_economy_config
_ECON_CACHE_TTL = 30.0       # s; invalidado imediatamente em update_economy_config

_current_guild: "contextvars.ContextVar" = contextvars.ContextVar('current_guild', default=None)


def set_current_guild(guild_id):
    """Define o servidor 'atual' deste contexto async (usado por _db())."""
    _current_guild.set(int(guild_id) if guild_id is not None else None)


def get_current_guild():
    return _current_guild.get()


@contextmanager
def using_guild(guild_id):
    """Roda um bloco no contexto de um servidor (para loops e listeners de gateway)."""
    token = _current_guild.set(int(guild_id) if guild_id is not None else None)
    try:
        yield
    finally:
        _current_guild.reset(token)


def _guild_db_path(guild_id) -> str:
    return os.path.join(_DATA_DIR, f'guild_{guild_id}.db')


class _ConnPool:
    """Pool LAZY de conexões para um banco. Abre 1 conexão (baseline, roda o schema)
    e cresce sob demanda até _POOL_SIZE sob concorrência real; conexões ociosas
    além de _POOL_MIN são fechadas após _POOL_IDLE_TTL. Como cada conexão aiosqlite
    é uma THREAD, isso mantém ~1 thread/servidor em repouso em vez de _POOL_SIZE."""
    __slots__ = ('path', 'schema_fn', 'sem', '_idle', 'open_count', '_lock', 'draining')

    def __init__(self, path: str, schema_fn):
        self.path = path
        self.schema_fn = schema_fn
        self.sem = asyncio.Semaphore(_POOL_SIZE)   # teto de conexões EM USO simultâneas
        self._idle: list = []                      # [(conn, last_release_monotonic)]
        self.open_count = 0                        # abertas (em uso + ociosas)
        self._lock = asyncio.Lock()
        self.draining = False

    async def _new_conn(self, run_schema: bool):
        Path(os.path.dirname(self.path)).mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(self.path)
        await conn.execute('PRAGMA journal_mode=WAL')     # leituras não travam escritas
        await conn.execute('PRAGMA synchronous=NORMAL')   # equilíbrio segurança/velocidade
        await conn.execute('PRAGMA busy_timeout=5000')    # espera (não erra) em contenção
        if run_schema:
            await self.schema_fn(conn)                    # cria/migra o schema 1x (idempotente)
        return conn

    async def warmup(self):
        """Cria a 1ª conexão (roda o schema). Chamado ao criar o pool / no /ativar."""
        async with self._lock:
            if self.open_count == 0:
                conn = await self._new_conn(run_schema=True)
                self.open_count += 1
                self._idle.append((conn, time.monotonic()))

    async def acquire(self):
        await self.sem.acquire()
        try:
            async with self._lock:
                if self._idle:
                    conn, _ = self._idle.pop()            # reusa a mais recente (LIFO)
                    return conn
                conn = await self._new_conn(run_schema=(self.open_count == 0))
                self.open_count += 1
                return conn
        except BaseException:
            self.sem.release()
            raise

    async def release(self, conn, broken: bool = False):
        async with self._lock:
            if broken or self.draining:
                self.open_count -= 1
                try:
                    await conn.close()
                except Exception:
                    pass
            else:
                self._idle.append((conn, time.monotonic()))
                await self._trim_locked()
        self.sem.release()

    async def _trim_locked(self):
        """Fecha conexões ociosas ALÉM de _POOL_MIN que passaram do TTL."""
        if len(self._idle) <= _POOL_MIN:
            return
        now = time.monotonic()
        fresh = [(c, ts) for (c, ts) in self._idle if (now - ts) < _POOL_IDLE_TTL]
        stale = [(c, ts) for (c, ts) in self._idle if (now - ts) >= _POOL_IDLE_TTL]
        need = max(0, _POOL_MIN - len(fresh))
        keep_stale = stale[len(stale) - need:] if need else []
        to_close = stale[:len(stale) - len(keep_stale)]
        for c, _ in to_close:
            self.open_count -= 1
            try:
                await c.close()
            except Exception:
                pass
        self._idle = fresh + keep_stale

    async def close_all(self):
        """Fecha as conexões ociosas já; as em uso fecham ao serem devolvidas."""
        async with self._lock:
            self.draining = True
            idle, self._idle = self._idle, []
            for c, _ in idle:
                self.open_count -= 1
                try:
                    await c.close()
                except Exception:
                    pass


async def _get_pool(key, path: str, schema_fn) -> "_ConnPool":
    pool = _pools.get(key)
    if pool is not None:
        return pool
    async with _pools_lock:
        pool = _pools.get(key)
        if pool is not None:
            return pool
        pool = _ConnPool(path, schema_fn)
        await pool.warmup()
        _pools[key] = pool
        return pool


@asynccontextmanager
async def _borrow(key, path: str, schema_fn):
    """Empresta/devolve uma conexão de um pool lazy (uso interno)."""
    pool = await _get_pool(key, path, schema_fn)
    conn = await pool.acquire()
    conn.row_factory = None        # reset ao default (tupla)
    broken = False
    try:
        yield conn
    finally:
        try:
            await conn.rollback()  # não deixa transação pendente vazar pro próximo
        except Exception:
            broken = True          # rollback falhou → conexão suspeita, descarta
        await pool.release(conn, broken=broken)


def _db():
    """Empresta uma conexão do banco DO SERVIDOR atual (ContextVar).
    Levanta RuntimeError se nenhum servidor estiver no contexto."""
    gid = _current_guild.get()
    if gid is None:
        raise RuntimeError(
            "Acesso ao banco sem servidor no contexto. Use set_current_guild()/"
            "using_guild() no ponto de entrada (comando/interação/loop/listener)."
        )
    return _borrow(gid, _guild_db_path(gid), _init_schema)


def _global_db():
    """Empresta uma conexão do REGISTRO GLOBAL (lista de servidores ativados)."""
    return _borrow(_GLOBAL_KEY, _GLOBAL_DB_PATH, _init_global_schema)


async def close_guild_pool(guild_id) -> None:
    """Fecha o pool de um servidor (libera as threads de conexão) — usado quando o
    servidor é desativado. Em repouso, o bot não segura recursos de guildas inativas."""
    key = int(guild_id)
    async with _pools_lock:
        pool = _pools.pop(key, None)
    if pool is not None:
        await pool.close_all()
    _econ_cache.pop(key, None)


async def close_all_pools() -> None:
    """Fecha todos os pools (encerramento gracioso)."""
    async with _pools_lock:
        pools = list(_pools.values())
        _pools.clear()
    for p in pools:
        try:
            await p.close_all()
        except Exception:
            pass
    _econ_cache.clear()


async def _init_global_schema(db):
    """Schema do registro global: só a lista de servidores ativados."""
    await db.execute('''
        CREATE TABLE IF NOT EXISTS activated_servers (
            guild_id     INTEGER PRIMARY KEY,
            activated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    await db.commit()


async def init_database():
    """Prepara o registro global. Os bancos POR SERVIDOR são criados no /ativar
    (ou no 1º acesso) — começando sempre do zero, sem compartilhar nada."""
    Path(_DATA_DIR).mkdir(parents=True, exist_ok=True)
    await _get_pool(_GLOBAL_KEY, _GLOBAL_DB_PATH, _init_global_schema)


async def _init_schema(db):
        # Cria/migra TODAS as tabelas de UM servidor (rodado por banco-de-servidor).
        # (WAL/synchronous/busy_timeout já são aplicados em cada conexão do pool.)

        # Tabela de configurações do relógio UTC
        await db.execute('''
            CREATE TABLE IF NOT EXISTS utc_clock (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                category_id INTEGER NOT NULL,
                category_name TEXT NOT NULL
            )
        ''')

        # Tabela de configuração de canal de nodes
        await db.execute('''
            CREATE TABLE IF NOT EXISTS node_calendar (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                channel_id INTEGER NOT NULL,
                message_id INTEGER
            )
        ''')

        # Tabela de eventos de nodes
        await db.execute('''
            CREATE TABLE IF NOT EXISTS node_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id INTEGER NOT NULL,
                node_type TEXT NOT NULL,
                map_name TEXT NOT NULL,
                spawn_time TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Migração: adicionar novas colunas se não existirem (spawn_timestamp e added_by)
        for migration_sql in [
            "ALTER TABLE node_events ADD COLUMN spawn_timestamp INTEGER DEFAULT 0",
            "ALTER TABLE node_events ADD COLUMN added_by TEXT DEFAULT 'Desconhecido'",
            # ID de quem scoutou (p/ o botão de remover filtrar 'só os meus')
            "ALTER TABLE node_events ADD COLUMN added_by_id INTEGER",
        ]:
            try:
                await db.execute(migration_sql)
                await db.commit()
            except Exception:
                pass  # Coluna já existe

        # Tabela de servidores ativados
        await db.execute('''
            CREATE TABLE IF NOT EXISTS activated_servers (
                guild_id INTEGER PRIMARY KEY,
                activated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Tabela de canais limitados
        await db.execute('''
            CREATE TABLE IF NOT EXISTS limited_channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                UNIQUE(guild_id, channel_id)
            )
        ''')

        # Tabela de whitelist para canais limitados (comandos permitidos)
        await db.execute('''
            CREATE TABLE IF NOT EXISTS limited_channel_whitelist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                command_name TEXT NOT NULL,
                UNIQUE(guild_id, channel_id, command_name)
            )
        ''')

        # Log permanente de todos os nodes já adicionados (nunca é deletado)
        await db.execute('''
            CREATE TABLE IF NOT EXISTS node_events_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                node_type       TEXT    NOT NULL,
                map_name        TEXT    NOT NULL,
                added_by        TEXT    NOT NULL,
                added_by_id     INTEGER NOT NULL,
                spawn_timestamp INTEGER NOT NULL,
                spawn_utc       TEXT    NOT NULL,
                logged_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Lista CURADA de mapas (Black Zone) usada no seletor do botão de node.
        # Por servidor (banco isolado); a staff adiciona/remove com /addnodemap.
        await db.execute('''
            CREATE TABLE IF NOT EXISTS node_maps (
                name     TEXT PRIMARY KEY COLLATE NOCASE,
                added_by INTEGER,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Mapas EMBUTIDOS (Black Zone) escondidos do seletor via /removenodemap.
        await db.execute('''
            CREATE TABLE IF NOT EXISTS node_map_exclusions (
                name        TEXT PRIMARY KEY COLLATE NOCASE,
                excluded_by INTEGER,
                excluded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # TIPOS de node POR SERVIDOR (nome · emoji · peso do scout). Cada staff define
        # os seus em /setup → Nodes. `name` é o identificador guardado nos node_events
        # (ex.: "Couro 8.4"); `weight` (0..1+) é o peso no pagamento do scout. Os 4
        # padrões são semeados UMA vez por servidor (flag nodes_seeded em economy_config).
        await db.execute('''
            CREATE TABLE IF NOT EXISTS node_defs (
                name       TEXT PRIMARY KEY COLLATE NOCASE,
                emoji      TEXT,
                weight     REAL NOT NULL DEFAULT 1.0,
                sort       INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # ======================== ECONOMY ========================

        # Config singleton: cargos, canais e percentuais
        await db.execute('''
            CREATE TABLE IF NOT EXISTS economy_config (
                id                     INTEGER PRIMARY KEY CHECK (id = 1),
                role_council           INTEGER,
                role_caller            INTEGER,
                role_member            INTEGER,
                role_content_creator   INTEGER,
                role_logistic          INTEGER,
                role_offcd             INTEGER,
                role_bomb              INTEGER,
                role_bomb_leader       INTEGER,
                role_looter            INTEGER,
                voice_cta              INTEGER,
                channel_events         INTEGER,
                channel_logger         INTEGER,
                channel_economylogs    INTEGER,
                channel_zergregear     INTEGER,
                channel_bombregear     INTEGER,
                channel_massinfo       INTEGER,
                channel_looterchat     INTEGER,
                channel_bombleaderchat INTEGER,
                channel_tabsell        INTEGER,
                massinfo_message_id    INTEGER,
                looterchat_message_id  INTEGER,
                splits_message_id      INTEGER,
                looter_threshold       INTEGER DEFAULT 15,
                guild_tax_percent      INTEGER DEFAULT 10,
                node_scout_percent     INTEGER DEFAULT 5,
                logger_percent         INTEGER DEFAULT 5,
                role_trial             INTEGER,
                voice_waitingroom      INTEGER,
                trial_percent          INTEGER DEFAULT 20,
                role_battlemount       INTEGER,
                role_logger            INTEGER,
                channel_battlemountchat INTEGER,
                bm_threshold           INTEGER DEFAULT 20,
                bm_max_debuff          INTEGER DEFAULT 15,
                bm_prio                TEXT,
                bm_debuff              TEXT,
                battlemount_message_id INTEGER,
                channel_logreview      INTEGER,   -- canal SÓ-staff p/ revisão dos logs
                guild_ingame_name      TEXT,      -- nome da guilda no jogo (filtro de loot)
                nodes_seeded           INTEGER DEFAULT 0  -- node_defs padrões já semeados?
            )
        ''')
        # Migrações para bases antigas
        for migration_sql in [
            "ALTER TABLE economy_config ADD COLUMN channel_massinfo INTEGER",
            "ALTER TABLE economy_config ADD COLUMN massinfo_message_id INTEGER",
            "ALTER TABLE economy_config ADD COLUMN role_offcd INTEGER",
            "ALTER TABLE economy_config ADD COLUMN role_bomb INTEGER",
            "ALTER TABLE economy_config ADD COLUMN role_bomb_leader INTEGER",
            "ALTER TABLE economy_config ADD COLUMN role_looter INTEGER",
            "ALTER TABLE economy_config ADD COLUMN channel_looterchat INTEGER",
            "ALTER TABLE economy_config ADD COLUMN channel_bombleaderchat INTEGER",
            "ALTER TABLE economy_config ADD COLUMN channel_tabsell INTEGER",
            "ALTER TABLE economy_config ADD COLUMN looterchat_message_id INTEGER",
            "ALTER TABLE economy_config ADD COLUMN splits_message_id INTEGER",
            "ALTER TABLE economy_config ADD COLUMN looter_threshold INTEGER DEFAULT 15",
            # Cargo Trial + waiting room + desconto de participação dos trials
            "ALTER TABLE economy_config ADD COLUMN role_trial INTEGER",
            "ALTER TABLE economy_config ADD COLUMN voice_waitingroom INTEGER",
            "ALTER TABLE economy_config ADD COLUMN trial_percent INTEGER DEFAULT 20",
            # Battlemounts: cargo BM, cargo logger e canal do battlemountchat
            "ALTER TABLE economy_config ADD COLUMN role_battlemount INTEGER",
            "ALTER TABLE economy_config ADD COLUMN role_logger INTEGER",
            "ALTER TABLE economy_config ADD COLUMN channel_battlemountchat INTEGER",
            # Battlemounts: threshold, budget (max debuff), prioridade e custos
            "ALTER TABLE economy_config ADD COLUMN bm_threshold INTEGER DEFAULT 20",
            "ALTER TABLE economy_config ADD COLUMN bm_max_debuff INTEGER DEFAULT 15",
            "ALTER TABLE economy_config ADD COLUMN bm_prio TEXT",
            "ALTER TABLE economy_config ADD COLUMN bm_debuff TEXT",
            "ALTER TABLE economy_config ADD COLUMN battlemount_message_id INTEGER",
            # % da tab pro grupo de loggers (logs reagidas com ✅)
            "ALTER TABLE economy_config ADD COLUMN logger_percent INTEGER DEFAULT 5",
            # Loot-log: canal só-staff p/ revisão e nome da guilda no jogo (filtro)
            "ALTER TABLE economy_config ADD COLUMN channel_logreview INTEGER",
            "ALTER TABLE economy_config ADD COLUMN guild_ingame_name TEXT",
            # Mentoria: categoria BASE dos tickets (cadeia em mentoria_categories)
            "ALTER TABLE economy_config ADD COLUMN mentoria_category_id INTEGER",
            # Cargos: mentor e officer (usados pela permissão do /trial)
            "ALTER TABLE economy_config ADD COLUMN role_mentor INTEGER",
            "ALTER TABLE economy_config ADD COLUMN role_officer INTEGER",
            # Cargo lead: acesso livre a TODOS os comandos com permissão
            "ALTER TABLE economy_config ADD COLUMN role_lead INTEGER",
            # Mentoria: canal de FÓRUM onde cada membro/trial ganha um POST
            "ALTER TABLE economy_config ADD COLUMN mentoria_forum_id INTEGER",
            # Energia da guilda: canal de alertas + limite de "energia baixa"
            "ALTER TABLE economy_config ADD COLUMN channel_energyalerts INTEGER",
            "ALTER TABLE economy_config ADD COLUMN energy_alert_threshold INTEGER DEFAULT 50",
            # TeamSpeak: endereço + senha (opcional)
            "ALTER TABLE economy_config ADD COLUMN teamspeak_address TEXT",
            "ALTER TABLE economy_config ADD COLUMN teamspeak_password TEXT",
            # Battleboard: canal onde o link albionbb das batalhas do CTA é postado
            "ALTER TABLE economy_config ADD COLUMN channel_battleboard INTEGER",
            # Sheets POR SERVIDOR: webhook /exec do Apps Script + secret (cada guild o seu)
            "ALTER TABLE economy_config ADD COLUMN sheets_webhook_url TEXT",
            "ALTER TABLE economy_config ADD COLUMN sheets_secret TEXT",
            # Recrutamento: canal do painel (parent das threads), cargo de recrutador,
            # id da msg do painel (p/ reusar) e cooldown (horas) após uma rejeição.
            "ALTER TABLE economy_config ADD COLUMN channel_recruitment INTEGER",
            "ALTER TABLE economy_config ADD COLUMN role_recruiter INTEGER",   # legado (1 cargo)
            "ALTER TABLE economy_config ADD COLUMN recruiter_roles TEXT",     # CSV de IDs (vários)
            "ALTER TABLE economy_config ADD COLUMN recruitment_message_id INTEGER",
            "ALTER TABLE economy_config ADD COLUMN recruitment_cooldown_hours INTEGER DEFAULT 24",
            # Cargo TEMPORÁRIO dado ao candidato quando ele abre o ticket (libera salas
            # de recrutamento). Só é concedido a quem NÃO tem nenhum cargo, e é removido
            # assim que o ticket é aprovado/recusado.
            "ALTER TABLE economy_config ADD COLUMN role_recruitment INTEGER",
            # Flag: os tipos de node padrão já foram semeados neste servidor?
            "ALTER TABLE economy_config ADD COLUMN nodes_seeded INTEGER DEFAULT 0",
            # Canal de voz MÃE do sistema de salas temporárias (clonada conforme enche)
            "ALTER TABLE economy_config ADD COLUMN voice_temp_mother INTEGER",
        ]:
            try:
                await db.execute(migration_sql)
                await db.commit()
            except Exception:
                pass
        # Garantir que a linha singleton exista
        await db.execute('INSERT OR IGNORE INTO economy_config (id) VALUES (1)')

        # Semear os tipos de node PADRÃO uma única vez por servidor (depois disso a
        # staff gerencia em /setup → Nodes; apagar todos NÃO re-semeia). INSERT OR
        # IGNORE preserva qualquer custom que já exista com o mesmo nome.
        cur = await db.execute('SELECT COALESCE(nodes_seeded, 0) FROM economy_config WHERE id = 1')
        row = await cur.fetchone()
        if not (row and row[0]):
            defaults = [
                ("Couro 8.4",   "🐂", 1.0, 0),
                ("Minério 8.4", "⛏️", 1.0, 1),
                ("Fibra 8.4",   "🥀", 1.0, 2),
                ("Madeira 8.4", "🪵", 1.0, 3),
            ]
            await db.executemany(
                'INSERT OR IGNORE INTO node_defs (name, emoji, weight, sort) VALUES (?, ?, ?, ?)',
                defaults)
            await db.execute('UPDATE economy_config SET nodes_seeded = 1 WHERE id = 1')
            await db.commit()

        # Saldos dos usuários (prata + energia da guilda)
        await db.execute('''
            CREATE TABLE IF NOT EXISTS user_balances (
                user_id      INTEGER PRIMARY KEY,
                balance      INTEGER NOT NULL DEFAULT 0,
                total_earned INTEGER NOT NULL DEFAULT 0,
                energy       INTEGER NOT NULL DEFAULT 0
            )
        ''')

        # Log de energia (dedup): cada lançamento da log do jogo aplicado 1x só.
        await db.execute('''
            CREATE TABLE IF NOT EXISTS energy_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                ts         TEXT    NOT NULL,
                player     TEXT    NOT NULL,
                reason     TEXT,
                amount     INTEGER NOT NULL,
                user_id    INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(ts, player, amount)
            )
        ''')

        # Whitelist de energia: quem cuida da energia da guilda (saldo muito afetado)
        # é IGNORADO por completo no processamento das logs de energia.
        await db.execute('''
            CREATE TABLE IF NOT EXISTS energy_whitelist (
                user_id  INTEGER PRIMARY KEY,
                added_by INTEGER,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Banco da guild (singleton)
        await db.execute('''
            CREATE TABLE IF NOT EXISTS guild_bank (
                id      INTEGER PRIMARY KEY CHECK (id = 1),
                balance INTEGER NOT NULL DEFAULT 0
            )
        ''')
        await db.execute('INSERT OR IGNORE INTO guild_bank (id, balance) VALUES (1, 0)')

        # Log permanente de mudanças de cargo (somente cargos configurados)
        await db.execute('''
            CREATE TABLE IF NOT EXISTS role_change_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                user_name  TEXT    NOT NULL,
                role_id    INTEGER NOT NULL,
                role_name  TEXT    NOT NULL,
                action     TEXT    NOT NULL,      -- 'added' | 'removed'
                changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # ======================== CTA EVENTS ========================
        # Schemas vazios agora; serão preenchidos pelo sistema de CTA (Fase 4/5).
        # Os comandos /attendance, /lowattendance e /leaderboard (campo attendance)
        # já consultam estas tabelas — retornam 0 enquanto não houver dados.

        await db.execute('''
            CREATE TABLE IF NOT EXISTS cta_events (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                caller_id        INTEGER NOT NULL,
                caller_name      TEXT    NOT NULL,
                started_at       TIMESTAMP NOT NULL,
                ended_at         TIMESTAMP,           -- NULL = ainda em andamento
                had_regear       INTEGER DEFAULT 0,
                repair_value     INTEGER DEFAULT 0,
                tab_location     TEXT,
                lootlogger_done  INTEGER,
                split_finalized  INTEGER DEFAULT 0,
                event_thread_id  INTEGER,
                logger_thread_id INTEGER,
                regear_thread_id INTEGER,
                event_message_id INTEGER,
                created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        await db.execute('''
            CREATE TABLE IF NOT EXISTS cta_attendance (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id          INTEGER NOT NULL,
                user_id           INTEGER NOT NULL,
                user_name         TEXT    NOT NULL,
                snapshots_present INTEGER NOT NULL DEFAULT 0,
                snapshots_total   INTEGER NOT NULL DEFAULT 0,
                percent           INTEGER,             -- preenchido no /end (efetivo, já c/ desconto trial)
                base_percent      INTEGER,             -- participação base (antes do desconto trial)
                is_trial          INTEGER DEFAULT 0,   -- 1 se a pessoa era trial no fim do CTA
                silver_received   INTEGER DEFAULT 0,   -- preenchido na finalização do split
                UNIQUE(event_id, user_id),
                FOREIGN KEY (event_id) REFERENCES cta_events(id)
            )
        ''')

        # Migrações: total_snapshots e guild_id em cta_events
        for migration_sql in [
            "ALTER TABLE cta_events ADD COLUMN total_snapshots INTEGER DEFAULT 0",
            "ALTER TABLE cta_events ADD COLUMN guild_id INTEGER",
            "ALTER TABLE cta_events ADD COLUMN announcement_channel_id INTEGER",
            "ALTER TABLE cta_events ADD COLUMN announcement_message_id INTEGER",
            "ALTER TABLE cta_events ADD COLUMN looters_drawn INTEGER DEFAULT 0",
            # Ponto 3: distinguir quem estava na zerg de quem foi alistado depois
            "ALTER TABLE cta_attendance ADD COLUMN enlisted INTEGER DEFAULT 0",
            "ALTER TABLE cta_attendance ADD COLUMN enlisted_by INTEGER",
            # Desconto de participação dos trials (base + flag)
            "ALTER TABLE cta_attendance ADD COLUMN base_percent INTEGER",
            "ALTER TABLE cta_attendance ADD COLUMN is_trial INTEGER DEFAULT 0",
            # Ponto 4: integração com a planilha (comp escolhida + página criada)
            "ALTER TABLE cta_events ADD COLUMN comp TEXT",
            "ALTER TABLE cta_events ADD COLUMN sheet_page TEXT",
            # Ponto 7: link direto (deep link) para a página da planilha do CTA
            "ALTER TABLE cta_events ADD COLUMN sheet_url TEXT",
            # Exclusão agendada da planilha do CTA (em andamento: +2h após /callout)
            "ALTER TABLE cta_events ADD COLUMN sheet_delete_at TIMESTAMP",
            # Move Call to Arms -> Waiting Room já feito (1x) p/ CTA agendado
            "ALTER TABLE cta_events ADD COLUMN pre_start_moved INTEGER DEFAULT 0",
            # Split definido? (distingue "definido como 0" de "nunca definido")
            "ALTER TABLE cta_events ADD COLUMN split_defined INTEGER DEFAULT 0",
            # ID da mensagem de aviso "10 min p/ começar" (apagada quando inicia)
            "ALTER TABLE cta_events ADD COLUMN prestart_msg_id INTEGER",
            # Painel de massa ao vivo (parties+líderes) postado quando o CTA inicia.
            "ALTER TABLE cta_events ADD COLUMN startboard_msg_id INTEGER",
            # Mensagem livre que o caller digita no /cta (exibida no mass-info)
            "ALTER TABLE cta_events ADD COLUMN cta_message TEXT",
            # Loot-log: id da mensagem canônica (auto-reconciliada) no canal da staff
            "ALTER TABLE cta_events ADD COLUMN logreview_msg_id INTEGER",
            # Loot-log: thread PRIVADA (só-logística) c/ o reconciliado + prazo de
            # exclusão da thread PÚBLICA de logger (30 min após o evento).
            "ALTER TABLE cta_events ADD COLUMN logreview_thread_id INTEGER",
            "ALTER TABLE cta_events ADD COLUMN logger_thread_delete_at TIMESTAMP",
            # Ponto 4 (alterar registro): linha L:O onde o bot escreveu as roles,
            # pra conseguir limpá-las depois mesmo que o nome seja movido.
            "ALTER TABLE cta_function_logs ADD COLUMN sheet_row INTEGER",
            # Energia da guilda: saldo de energia por usuário (base antiga).
            "ALTER TABLE user_balances ADD COLUMN energy INTEGER NOT NULL DEFAULT 0",
            # Battleboard: link albionbb das batalhas (manual /bb ou auto-descoberto no callout)
            "ALTER TABLE cta_events ADD COLUMN battleboard_url TEXT",
            # Battleboard: total de cura (healing) por player nas batalhas
            "ALTER TABLE cta_battle_players ADD COLUMN healing INTEGER DEFAULT 0",
            # Battleboard: vincular player a user_id quando possível
            "ALTER TABLE cta_battle_players ADD COLUMN user_id INTEGER",
            # Battleboard: timestamp de quando o registro foi criado (a partir de 16/06/2026)
            "ALTER TABLE cta_battle_players ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP",
            # Split: URL do PRINT da tab (substitui a pergunta do lootlogger). Vira link
            # "[print da tab](url)" no invoice (NÃO set_image).
            "ALTER TABLE cta_events ADD COLUMN tab_image_url TEXT",
            # Split: BYTES do print da tab — anexado (spoiler) ao invoice na finalização.
            "ALTER TABLE cta_events ADD COLUMN tab_image_blob BLOB",
            # Mass-info: /liberarfuncoes — 1 = ignora os gates de vagas/party/marcador
            # na escolha de funções deste CTA (válvula manual do caller).
            "ALTER TABLE cta_events ADD COLUMN functions_released INTEGER DEFAULT 0",
        ]:
            try:
                await db.execute(migration_sql)
                await db.commit()
            except Exception:
                pass  # coluna já existe

        # ======================== Limpeza de dados históricos de batalhas ========================
        # Remove registros de batalhas anteriores a hoje (reset para começar do zero)
        try:
            from datetime import date, timedelta
            today = date.today().isoformat()
            await db.execute(
                '''DELETE FROM cta_battle_players
                   WHERE created_at < datetime(?)''',
                (today,),
            )
            await db.commit()
        except Exception:
            pass  # Tabela pode não existir ainda ou já foi limpa

        # ======================== CTA FUNCTION LOGS ========================
        # Log das funções que cada usuário declarou via mass-info embed
        await db.execute('''
            CREATE TABLE IF NOT EXISTS cta_function_logs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id    INTEGER NOT NULL,
                user_id     INTEGER NOT NULL,
                user_name   TEXT    NOT NULL,
                function1   TEXT    NOT NULL,
                function2   TEXT    NOT NULL,
                function3   TEXT    NOT NULL,
                synced_sheets INTEGER DEFAULT 0,  -- 1 = enviado pro Sheets
                sheet_row   INTEGER,             -- linha L:O onde o bot escreveu
                logged_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (event_id) REFERENCES cta_events(id)
            )
        ''')

        # Battleboard: players que apareceram na(s) batalha(s) do CTA (via /bb).
        await db.execute('''
            CREATE TABLE IF NOT EXISTS cta_battle_players (
                event_id INTEGER NOT NULL,
                player   TEXT    NOT NULL,
                guild    TEXT,
                kills    INTEGER DEFAULT 0,
                deaths   INTEGER DEFAULT 0,
                healing  INTEGER DEFAULT 0,
                UNIQUE(event_id, player),
                FOREIGN KEY (event_id) REFERENCES cta_events(id)
            )
        ''')

        # MVPs por batalha: um por tipo ('dps' = mais kills, 'healer' = mais healing)
        await db.execute('''
            CREATE TABLE IF NOT EXISTS cta_battle_mvps (
                event_id INTEGER NOT NULL,
                player   TEXT    NOT NULL,
                kind     TEXT    NOT NULL, -- 'dps' | 'healer'
                UNIQUE(event_id, player, kind),
                FOREIGN KEY (event_id) REFERENCES cta_events(id)
            )
        ''')

        # ======================== CTA PUNISHMENTS (Ponto 4) ========================
        # Punições para quem estava no split mas NÃO registrou na planilha.
        # amount é calculado a partir do percent do jogador no split.
        await db.execute('''
            CREATE TABLE IF NOT EXISTS cta_punishments (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id    INTEGER NOT NULL,
                user_id     INTEGER NOT NULL,
                user_name   TEXT    NOT NULL,
                percent     INTEGER NOT NULL DEFAULT 0,
                amount      INTEGER NOT NULL DEFAULT 0,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(event_id, user_id),
                FOREIGN KEY (event_id) REFERENCES cta_events(id)
            )
        ''')

        # ======================== CTA EVENT NODES (Fase 5.1) ========================
        # Quais nodes próximos foram REALMENTE capturados durante o CTA.
        # Apenas os capturados pagam o scout no split.
        await db.execute('''
            CREATE TABLE IF NOT EXISTS cta_event_nodes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id    INTEGER NOT NULL,
                node_log_id INTEGER NOT NULL,
                captured    INTEGER NOT NULL DEFAULT 0,
                UNIQUE(event_id, node_log_id),
                FOREIGN KEY (event_id)    REFERENCES cta_events(id),
                FOREIGN KEY (node_log_id) REFERENCES node_events_log(id)
            )
        ''')

        # Ledger de pagamentos do split (Fase 5): UMA linha por crédito feito na
        # finalização — participantes, scouts, loggers e o imposto do banco. Serve
        # pra ESTORNAR o valor EXATO ao apagar o evento (/deleteevent), sem recalcular
        # (imune a mudança de config: tax/scout/logger %). kind ∈ participant | scout
        # | logger | guild_bank; user_id é NULL p/ guild_bank.
        await db.execute('''
            CREATE TABLE IF NOT EXISTS cta_payouts (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id   INTEGER NOT NULL,
                kind       TEXT    NOT NULL,
                user_id    INTEGER,
                amount     INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (event_id) REFERENCES cta_events(id)
            )
        ''')
        await db.execute(
            'CREATE INDEX IF NOT EXISTS idx_cta_payouts_event ON cta_payouts(event_id)')

        # ======================== REGEARS ========================
        await db.execute('''
            CREATE TABLE IF NOT EXISTS regears (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                user_name   TEXT    NOT NULL,
                guild_id    INTEGER NOT NULL,
                channel_id  INTEGER NOT NULL,
                message_id  INTEGER NOT NULL UNIQUE,
                image_url   TEXT    NOT NULL,
                status      TEXT    NOT NULL DEFAULT 'pending',  -- pending|paid|denied|removed
                value       INTEGER DEFAULT 0,
                handled_by  INTEGER,            -- id de quem aprovou/negou/pagou
                handled_at  TIMESTAMP,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # ======================== LOOTER LOTTERY (Ponto 2) ========================
        # Cada usuário que clica no botão do looterchat entra no sorteio de looters
        # do CTA. Quantos são sorteados = (pings no mass-info) // looter_threshold.
        await db.execute('''
            CREATE TABLE IF NOT EXISTS cta_looter_entries (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id     INTEGER NOT NULL,
                user_id      INTEGER NOT NULL,
                user_name    TEXT    NOT NULL,
                drawn        INTEGER NOT NULL DEFAULT 0,   -- 1 = sorteado
                also_in_zerg INTEGER NOT NULL DEFAULT 0,   -- 1 = também pingou na zerg
                entered_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(event_id, user_id),
                FOREIGN KEY (event_id) REFERENCES cta_events(id)
            )
        ''')

        # ======================== TAB AUCTIONS (Ponto 5) ========================
        # Leilão de tabs no canal channel_tabsell. Qualquer um posta uma foto →
        # vira um leilão em 'setup'. Council/logistic definem valor inicial e de
        # arremate e iniciam o bid (10 min). Após 10 min (ou ao bater o arremate)
        # o comprador é definido. Botão de reroll refaz o leilão.
        await db.execute('''
            CREATE TABLE IF NOT EXISTS tab_auctions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id        INTEGER NOT NULL,
                channel_id      INTEGER NOT NULL,
                message_id      INTEGER NOT NULL UNIQUE,
                poster_id       INTEGER NOT NULL,
                poster_name     TEXT    NOT NULL,
                image_url       TEXT    NOT NULL,
                initial_value   INTEGER,            -- valor inicial (mínimo do 1º lance)
                buyout_value    INTEGER,            -- valor de arremate (compra imediata)
                status          TEXT NOT NULL DEFAULT 'setup',  -- setup|bidding|finished|cancelled
                ends_at         TIMESTAMP,          -- fim dos 10 min (ISO UTC)
                ping_message_id INTEGER,            -- msg de ping a ser deletada depois
                winner_id       INTEGER,
                winner_name     TEXT,
                winning_bid     INTEGER,
                started_by      INTEGER,            -- quem iniciou o bid
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        await db.execute('''
            CREATE TABLE IF NOT EXISTS tab_bids (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                auction_id  INTEGER NOT NULL,
                user_id     INTEGER NOT NULL,
                user_name   TEXT    NOT NULL,
                amount      INTEGER NOT NULL,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (auction_id) REFERENCES tab_auctions(id)
            )
        ''')

        # Ponto 4: cadastro de jogadores (Discord -> nick do jogo)
        await db.execute('''
            CREATE TABLE IF NOT EXISTS registrations (
                user_id              INTEGER PRIMARY KEY,
                nick                 TEXT    NOT NULL,
                registered_by        INTEGER,
                registered_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                mentoria_channel_id  INTEGER,            -- canal de mentoria do usuário
                mentoria_delete_at   TIMESTAMP,          -- prazo de exclusão (7d) ou NULL
                mentoria_perms_snapshot TEXT             -- JSON das perms p/ restaurar (oculto)
            )
        ''')
        # Migração p/ bases antigas (registrations criada ANTES do loop acima)
        for _m in [
            "ALTER TABLE registrations ADD COLUMN mentoria_channel_id INTEGER",
            "ALTER TABLE registrations ADD COLUMN mentoria_delete_at TIMESTAMP",
            "ALTER TABLE registrations ADD COLUMN mentoria_perms_snapshot TEXT",
        ]:
            try:
                await db.execute(_m)
                await db.commit()
            except Exception:
                pass

        # Tabela de aliases: permite múltiplas contas (nicks) ligadas a um mesmo
        # usuário Discord. Cada nick é único e aponta para um user_id.
        await db.execute('''
            CREATE TABLE IF NOT EXISTS registration_aliases (
                nick TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                registered_by INTEGER,
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES registrations(user_id)
            )
        ''')

        # Mentoria: cadeia de categorias dos tickets (base + extensões a cada 50 salas)
        await db.execute('''
            CREATE TABLE IF NOT EXISTS mentoria_categories (
                category_id INTEGER PRIMARY KEY,
                position    INTEGER NOT NULL DEFAULT 0,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Parte 4: dedupe de PMs de escalação (1 PM por CTA+nome+função)
        await db.execute('''
            CREATE TABLE IF NOT EXISTS cta_assignment_notifications (
                event_id    INTEGER NOT NULL,
                name_norm   TEXT    NOT NULL,
                role_norm   TEXT    NOT NULL,
                user_id     INTEGER,
                notified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (event_id, name_norm, role_norm)
            )
        ''')

        # Líder ATUAL (notificado) de cada party por CTA — pra detectar troca de
        # party leader e avisar o antigo (passe a pt) + o novo. 1 linha por (CTA, party).
        await db.execute('''
            CREATE TABLE IF NOT EXISTS cta_party_leaders (
                event_id   INTEGER NOT NULL,
                party      INTEGER NOT NULL,
                name_norm  TEXT,
                user_id    INTEGER,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (event_id, party)
            )
        ''')

        # Opt-out de PMs por tipo (ex.: 'escalacao') — quem não quer mais receber
        await db.execute('''
            CREATE TABLE IF NOT EXISTS pm_optouts (
                user_id   INTEGER NOT NULL,
                pm_type   TEXT    NOT NULL,
                opted_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, pm_type)
            )
        ''')

        # Loggers: autores de logs reagidas com ✅ na thread de logger do evento
        await db.execute('''
            CREATE TABLE IF NOT EXISTS cta_event_loggers (
                event_id   INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                user_id    INTEGER NOT NULL,
                added_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (event_id, message_id)
            )
        ''')

        # Loot-log (Fase 1): submissões de .csv de logger por CTA (1 por pessoa).
        # Guardamos só metadados + hash; o arquivo cru vai arquivado na thread da staff.
        await db.execute('''
            CREATE TABLE IF NOT EXISTS cta_log_submissions (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id       INTEGER NOT NULL,
                submitter_id   INTEGER NOT NULL,
                submitter_nick TEXT,
                file_name      TEXT,
                file_hash      TEXT,
                row_count      INTEGER DEFAULT 0,
                submitted_at   TIMESTAMP,
                UNIQUE(event_id, submitter_id),
                FOREIGN KEY (event_id) REFERENCES cta_events(id)
            )
        ''')

        # Loot-log (Fase 2): eventos de COLETA já normalizados por submissão,
        # dentro da janela do CTA. Usados na reconciliação (dedup + testemunhas).
        await db.execute('''
            CREATE TABLE IF NOT EXISTS cta_log_events (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id        INTEGER NOT NULL,
                submitter_id    INTEGER NOT NULL,
                ts              TEXT,      -- timestamp UTC ISO da coleta
                item_id         TEXT,
                item_name       TEXT,
                quantity        INTEGER DEFAULT 1,
                looted_by       TEXT,
                looted_by_guild TEXT,
                looted_by_alliance TEXT,
                looted_from     TEXT,
                looted_from_guild TEXT,
                looted_from_alliance TEXT,
                FOREIGN KEY (event_id) REFERENCES cta_events(id)
            )
        ''')
        await db.execute(
            'CREATE INDEX IF NOT EXISTS idx_logevents_event ON cta_log_events(event_id)'
        )
        for migration_sql in [
            "ALTER TABLE cta_log_events ADD COLUMN looted_by_alliance TEXT",
            "ALTER TABLE cta_log_events ADD COLUMN looted_from_alliance TEXT",
            "ALTER TABLE cta_log_events ADD COLUMN looted_from_guild TEXT",
        ]:
            try:
                await db.execute(migration_sql)
                await db.commit()
            except Exception:
                pass  # Coluna já existe

        # Battlemounts: fila de sorteio por CTA (jogador escolhe 3-5 montarias)
        await db.execute('''
            CREATE TABLE IF NOT EXISTS cta_bm_entries (
                event_id       INTEGER NOT NULL,
                user_id        INTEGER NOT NULL,
                user_name      TEXT,
                mounts         TEXT    NOT NULL,   -- JSON com as 3-5 montarias escolhidas
                drawn          INTEGER DEFAULT 0,
                assigned_mount TEXT,
                entered_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (event_id, user_id)
            )
        ''')

        # Gate de funções da planilha: NOME da função (NOCASE) → cargo(s) do Discord
        # que podem escolhê-la. Função sem linha aqui = aberta a todos.
        await db.execute('''
            CREATE TABLE IF NOT EXISTS sheet_role_gates (
                role_name       TEXT    NOT NULL COLLATE NOCASE,
                discord_role_id INTEGER NOT NULL,
                added_by        INTEGER,
                added_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (role_name, discord_role_id)
            )
        ''')

        # ===== Recrutamento — ticket clássico (1 thread privada por candidato) =====
        # Status: open|approved|rejected|cancelled|closed. (Colunas current_step/nick/
        # login_image_url são legado de um fluxo antigo de perguntas — mantidas inertes.)
        await db.execute('''
            CREATE TABLE IF NOT EXISTS recruitment_tickets (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id        INTEGER NOT NULL,
                user_id         INTEGER NOT NULL,
                thread_id       INTEGER,
                status          TEXT NOT NULL DEFAULT 'open',
                current_step    INTEGER NOT NULL DEFAULT 0,
                nick            TEXT,
                login_image_url TEXT,
                decided_by      INTEGER,
                decided_at      TIMESTAMP,
                reject_reason   TEXT,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                closed_at       TIMESTAMP,
                delete_at       TIMESTAMP                       -- apagar a thread em (aprovado: +4h)
            )
        ''')
        # Migração p/ bancos que criaram recruitment_tickets antes destas colunas.
        for _m in [
            "ALTER TABLE recruitment_tickets ADD COLUMN login_image_url TEXT",
            "ALTER TABLE recruitment_tickets ADD COLUMN delete_at TIMESTAMP",
        ]:
            try:
                await db.execute(_m)
                await db.commit()
            except Exception:
                pass

        # Índices nas colunas mais consultadas (queries por evento são as mais quentes).
        for index_sql in [
            "CREATE INDEX IF NOT EXISTS idx_recruit_tickets_user ON recruitment_tickets(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_recruit_tickets_thread ON recruitment_tickets(thread_id)",
            "CREATE INDEX IF NOT EXISTS idx_attendance_event ON cta_attendance(event_id)",
            "CREATE INDEX IF NOT EXISTS idx_funclogs_event   ON cta_function_logs(event_id)",
            "CREATE INDEX IF NOT EXISTS idx_eventnodes_event ON cta_event_nodes(event_id)",
            "CREATE INDEX IF NOT EXISTS idx_nodelog_spawn    ON node_events_log(spawn_timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_ctaevents_ended  ON cta_events(ended_at)",
            # attendance por USUÁRIO: leaderboard, /perfil, ranking, contagem.
            "CREATE INDEX IF NOT EXISTS idx_attendance_user  ON cta_attendance(user_id)",
            # KDA de battleboard por nick (/perfil) — busca case-insensitive.
            "CREATE INDEX IF NOT EXISTS idx_battleplayers_player ON cta_battle_players(player COLLATE NOCASE)",
            # tempo de casa / cargo mais antigo: filtra por usuário no log de cargos.
            "CREATE INDEX IF NOT EXISTS idx_rolelog_user     ON role_change_log(user_id)",
        ]:
            try:
                await db.execute(index_sql)
            except Exception:
                pass

        await db.commit()

async def save_utc_clock(category_id: int, category_name: str):
    """Salva a configuração do relógio UTC no banco de dados"""
    async with _db() as db:
        # Verificar se já existe um registro
        cursor = await db.execute('SELECT id FROM utc_clock WHERE id = 1')
        exists = await cursor.fetchone()

        if exists:
            # Atualizar registro existente
            await db.execute(
                'UPDATE utc_clock SET category_id = ?, category_name = ? WHERE id = 1',
                (category_id, category_name)
            )
        else:
            # Inserir novo registro
            await db.execute(
                'INSERT INTO utc_clock (id, category_id, category_name) VALUES (1, ?, ?)',
                (category_id, category_name)
            )

        await db.commit()

async def load_utc_clock():
    """Carrega a configuração do relógio UTC do banco de dados"""
    async with _db() as db:
        cursor = await db.execute('SELECT category_id, category_name FROM utc_clock WHERE id = 1')
        result = await cursor.fetchone()

        if result:
            return result[0], result[1]  # (category_id, category_name)
        return None, None

async def delete_utc_clock():
    """Deleta a configuração do relógio UTC do banco de dados"""
    async with _db() as db:
        await db.execute('DELETE FROM utc_clock WHERE id = 1')
        await db.commit()

# ==================== NODE CALENDAR ====================

async def save_node_calendar(channel_id: int, message_id: int = None):
    """Salva a configuração do calendário de nodes"""
    async with _db() as db:
        cursor = await db.execute('SELECT id FROM node_calendar WHERE id = 1')
        exists = await cursor.fetchone()

        if exists:
            await db.execute(
                'UPDATE node_calendar SET channel_id = ?, message_id = ? WHERE id = 1',
                (channel_id, message_id)
            )
        else:
            await db.execute(
                'INSERT INTO node_calendar (id, channel_id, message_id) VALUES (1, ?, ?)',
                (channel_id, message_id)
            )

        await db.commit()

async def load_node_calendar():
    """Carrega a configuração do calendário de nodes"""
    async with _db() as db:
        cursor = await db.execute('SELECT channel_id, message_id FROM node_calendar WHERE id = 1')
        result = await cursor.fetchone()

        if result:
            return result[0], result[1]  # (channel_id, message_id)
        return None, None

async def add_node_event(
    channel_id: int,
    node_type: str,
    map_name: str,
    spawn_timestamp: int,
    added_by: str,
    added_by_id: int = None,
):
    """Adiciona um evento de node usando Unix timestamp"""
    from datetime import datetime, timezone
    dt = datetime.fromtimestamp(spawn_timestamp, tz=timezone.utc)
    spawn_time_str = dt.strftime('%H:%M')  # manter coluna legada NOT NULL satisfeita

    async with _db() as db:
        await db.execute(
            '''INSERT INTO node_events
               (channel_id, node_type, map_name, spawn_time, spawn_timestamp, added_by, added_by_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (channel_id, node_type, map_name, spawn_time_str, spawn_timestamp, added_by, added_by_id)
        )
        await db.commit()

async def get_removable_nodes(channel_id: int, only_user_id: int = None,
                              only_user_name: str = None):
    """Nodes do canal p/ o seletor de remoção. Se `only_user_id` for dado, traz só os
    daquele usuário (casa por id; cai no nome p/ registros antigos sem id). Sem ele
    (staff), traz todos. Mesmo formato de get_node_events."""
    q = '''SELECT id, node_type, map_name, spawn_timestamp, added_by
           FROM node_events WHERE channel_id = ? AND spawn_timestamp > 0'''
    params = [channel_id]
    if only_user_id is not None:
        q += ' AND (added_by_id = ? OR (added_by_id IS NULL AND added_by = ?))'
        params += [only_user_id, only_user_name or '']
    q += ' ORDER BY spawn_timestamp ASC'
    async with _db() as db:
        cursor = await db.execute(q, tuple(params))
        return await cursor.fetchall()

async def get_node_events(channel_id: int):
    """Carrega todos os eventos de nodes de um canal, ordenados por spawn_timestamp"""
    async with _db() as db:
        cursor = await db.execute(
            '''SELECT id, node_type, map_name, spawn_timestamp, added_by
               FROM node_events
               WHERE channel_id = ?
               ORDER BY spawn_timestamp ASC''',
            (channel_id,)
        )
        return await cursor.fetchall()

async def find_duplicate_node(channel_id: int, node_type: str, map_name: str,
                              spawn_timestamp: int, window_seconds: int = 7200):
    """Procura um node IGUAL (mesmo tipo + mesmo mapa) com horário a MENOS de
    `window_seconds` (padrão 2h) do informado. Retorna (added_by, spawn_timestamp)
    do mais próximo, ou None se não houver — usado p/ barrar duplicados."""
    low  = spawn_timestamp - window_seconds
    high = spawn_timestamp + window_seconds
    async with _db() as db:
        cursor = await db.execute(
            '''SELECT added_by, spawn_timestamp FROM node_events
               WHERE channel_id = ? AND node_type = ? AND map_name = ?
                 AND spawn_timestamp > ? AND spawn_timestamp < ?
               ORDER BY ABS(spawn_timestamp - ?) ASC LIMIT 1''',
            (channel_id, node_type, map_name, low, high, spawn_timestamp)
        )
        row = await cursor.fetchone()
    return (row[0], row[1]) if row else None

# ---- Mapas (Black Zone) curados do seletor de node ----

async def add_node_map(name: str, added_by: int = None) -> bool:
    """Adiciona um mapa à lista (Black Zone). True se foi novo."""
    name = (name or '').strip()
    if not name:
        return False
    async with _db() as db:
        cur = await db.execute(
            'INSERT OR IGNORE INTO node_maps (name, added_by) VALUES (?, ?)',
            (name, added_by)
        )
        await db.commit()
        return cur.rowcount > 0

async def remove_node_map(name: str) -> bool:
    """Remove um mapa da lista. True se removeu algo."""
    async with _db() as db:
        cur = await db.execute('DELETE FROM node_maps WHERE name = ?', ((name or '').strip(),))
        await db.commit()
        return cur.rowcount > 0

async def get_node_maps() -> list:
    """Mapas EXTRAS (custom) do seletor, em ordem alfabética."""
    async with _db() as db:
        cursor = await db.execute('SELECT name FROM node_maps ORDER BY name COLLATE NOCASE ASC')
        return [r[0] for r in await cursor.fetchall()]

async def exclude_node_map(name: str, excluded_by: int = None) -> bool:
    """Esconde um mapa EMBUTIDO (Black Zone) do seletor. True se foi novo."""
    name = (name or '').strip()
    if not name:
        return False
    async with _db() as db:
        cur = await db.execute(
            'INSERT OR IGNORE INTO node_map_exclusions (name, excluded_by) VALUES (?, ?)',
            (name, excluded_by)
        )
        await db.commit()
        return cur.rowcount > 0

async def unexclude_node_map(name: str) -> bool:
    """Volta a mostrar um mapa embutido antes escondido. True se desfez algo."""
    async with _db() as db:
        cur = await db.execute(
            'DELETE FROM node_map_exclusions WHERE name = ?', ((name or '').strip(),)
        )
        await db.commit()
        return cur.rowcount > 0

async def get_excluded_node_maps() -> list:
    """Nomes dos mapas embutidos atualmente escondidos do seletor."""
    async with _db() as db:
        cursor = await db.execute('SELECT name FROM node_map_exclusions')
        return [r[0] for r in await cursor.fetchall()]

# ---------------- TIPOS de node por servidor (nome · emoji · peso) ----------------

async def get_node_defs() -> list:
    """Tipos de node do servidor: lista de {name, emoji, weight}, ordenada por
    sort e nome (a ordem que a staff vê/escolhe)."""
    async with _db() as db:
        cur = await db.execute(
            'SELECT name, emoji, weight FROM node_defs ORDER BY sort ASC, name COLLATE NOCASE ASC')
        rows = await cur.fetchall()
    return [{"name": r[0], "emoji": r[1], "weight": float(r[2]) if r[2] is not None else 1.0}
            for r in rows]

async def add_node_def(name: str, emoji: str = None, weight: float = 1.0) -> str:
    """Cria/atualiza um tipo de node. Retorna 'added' (novo) ou 'updated' (já existia,
    emoji/peso atualizados). Mantém o `sort` no fim p/ novos. '' se nome vazio."""
    name = (name or '').strip()
    if not name:
        return ''
    try:
        weight = float(weight)
    except (TypeError, ValueError):
        weight = 1.0
    emoji = (emoji or '').strip() or None
    async with _db() as db:
        cur = await db.execute('SELECT 1 FROM node_defs WHERE name = ?', (name,))
        exists = await cur.fetchone()
        if exists:
            await db.execute(
                'UPDATE node_defs SET emoji = ?, weight = ? WHERE name = ?',
                (emoji, weight, name))
            await db.commit()
            return 'updated'
        cur = await db.execute('SELECT COALESCE(MAX(sort), -1) + 1 FROM node_defs')
        nxt = (await cur.fetchone())[0] or 0
        await db.execute(
            'INSERT INTO node_defs (name, emoji, weight, sort) VALUES (?, ?, ?, ?)',
            (name, emoji, weight, nxt))
        await db.commit()
        return 'added'

async def remove_node_def(name: str) -> bool:
    """Apaga um tipo de node. True se removeu algo."""
    async with _db() as db:
        cur = await db.execute('DELETE FROM node_defs WHERE name = ?', ((name or '').strip(),))
        await db.commit()
        return cur.rowcount > 0

async def log_node_event(
    node_type: str,
    map_name: str,
    added_by: str,
    added_by_id: int,
    spawn_timestamp: int,
):
    """Registra permanentemente um node no log histórico."""
    from datetime import datetime, timezone
    dt       = datetime.fromtimestamp(spawn_timestamp, tz=timezone.utc)
    spawn_utc = dt.strftime('%Y-%m-%d %H:%M UTC')

    async with _db() as db:
        await db.execute(
            '''INSERT INTO node_events_log
               (node_type, map_name, added_by, added_by_id, spawn_timestamp, spawn_utc)
               VALUES (?, ?, ?, ?, ?, ?)''',
            (node_type, map_name, added_by, added_by_id, spawn_timestamp, spawn_utc)
        )
        await db.commit()

async def delete_expired_node_events(channel_id: int, cutoff_timestamp: int):
    """Remove eventos cujo spawn_timestamp já passou (antes do cutoff)"""
    async with _db() as db:
        await db.execute(
            '''DELETE FROM node_events
               WHERE channel_id = ? AND spawn_timestamp > 0 AND spawn_timestamp < ?''',
            (channel_id, cutoff_timestamp)
        )
        await db.commit()

async def delete_node_event(event_id: int):
    """Deleta um evento de node"""
    async with _db() as db:
        await db.execute('DELETE FROM node_events WHERE id = ?', (event_id,))
        await db.commit()

async def delete_node_calendar():
    """Deleta a configuração do calendário de nodes"""
    async with _db() as db:
        await db.execute('DELETE FROM node_calendar WHERE id = 1')
        await db.execute('DELETE FROM node_events')
        await db.commit()

# ==================== ACTIVATED SERVERS ====================

async def activate_server(guild_id: int):
    """Ativa um servidor: registra no GLOBAL e cria o banco dele do zero."""
    async with _global_db() as db:
        await db.execute(
            'INSERT OR IGNORE INTO activated_servers (guild_id) VALUES (?)',
            (guild_id,)
        )
        await db.commit()
    # Cria o banco do servidor (schema do zero) já — isolado dos demais.
    await _get_pool(int(guild_id), _guild_db_path(guild_id), _init_schema)

async def deactivate_server(guild_id: int):
    """Desativa um servidor (remove do registro global). O banco dele é preservado."""
    async with _global_db() as db:
        await db.execute('DELETE FROM activated_servers WHERE guild_id = ?', (guild_id,))
        await db.commit()
    # Libera as threads/conexões do banco desse servidor (o arquivo é preservado).
    await close_guild_pool(guild_id)

async def is_server_activated(guild_id: int) -> bool:
    """Verifica se um servidor foi ativado (consulta o registro GLOBAL)."""
    async with _global_db() as db:
        cursor = await db.execute(
            'SELECT guild_id FROM activated_servers WHERE guild_id = ?',
            (guild_id,)
        )
        result = await cursor.fetchone()
        return result is not None

async def get_activated_guild_ids() -> list:
    """IDs de todos os servidores ativados (usado pelos loops para iterar por servidor)."""
    async with _global_db() as db:
        cursor = await db.execute('SELECT guild_id FROM activated_servers')
        return [r[0] for r in await cursor.fetchall()]

# ==================== LIMITED CHANNELS ====================

async def add_limited_channel(guild_id: int, channel_id: int):
    """Adiciona um canal à lista de canais limitados"""
    async with _db() as db:
        try:
            await db.execute(
                'INSERT INTO limited_channels (guild_id, channel_id) VALUES (?, ?)',
                (guild_id, channel_id)
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False  # Canal já estava na lista

async def remove_limited_channel(guild_id: int, channel_id: int):
    """Remove um canal da lista de canais limitados"""
    async with _db() as db:
        await db.execute(
            'DELETE FROM limited_channels WHERE guild_id = ? AND channel_id = ?',
            (guild_id, channel_id)
        )
        await db.commit()

async def is_channel_limited(guild_id: int, channel_id: int) -> bool:
    """Verifica se um canal está na lista de limitados"""
    async with _db() as db:
        cursor = await db.execute(
            'SELECT id FROM limited_channels WHERE guild_id = ? AND channel_id = ?',
            (guild_id, channel_id)
        )
        result = await cursor.fetchone()
        return result is not None

async def get_limited_channels(guild_id: int):
    """Retorna todos os canais limitados de um servidor"""
    async with _db() as db:
        cursor = await db.execute(
            'SELECT channel_id FROM limited_channels WHERE guild_id = ? ORDER BY channel_id',
            (guild_id,)
        )
        return await cursor.fetchall()

# ==================== LIMITED CHANNEL WHITELIST ====================

async def add_whitelisted_command(guild_id: int, channel_id: int, command_name: str):
    """Adiciona um comando à whitelist de um canal limitado"""
    async with _db() as db:
        try:
            await db.execute(
                'INSERT INTO limited_channel_whitelist (guild_id, channel_id, command_name) VALUES (?, ?, ?)',
                (guild_id, channel_id, command_name)
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False

async def remove_whitelisted_command(guild_id: int, channel_id: int, command_name: str):
    """Remove um comando da whitelist de um canal limitado"""
    async with _db() as db:
        await db.execute(
            'DELETE FROM limited_channel_whitelist WHERE guild_id = ? AND channel_id = ? AND command_name = ?',
            (guild_id, channel_id, command_name)
        )
        await db.commit()

async def is_command_whitelisted(guild_id: int, channel_id: int, command_name: str) -> bool:
    """Verifica se um comando está na whitelist para um canal limitado"""
    async with _db() as db:
        cursor = await db.execute(
            'SELECT id FROM limited_channel_whitelist WHERE guild_id = ? AND channel_id = ? AND command_name = ?',
            (guild_id, channel_id, command_name)
        )
        result = await cursor.fetchone()
        return result is not None

async def get_whitelisted_commands(guild_id: int, channel_id: int):
    """Retorna todos os comandos whitelists para um canal"""
    async with _db() as db:
        cursor = await db.execute(
            'SELECT command_name FROM limited_channel_whitelist WHERE guild_id = ? AND channel_id = ? ORDER BY command_name',
            (guild_id, channel_id)
        )
        return [row[0] for row in await cursor.fetchall()]

# ==================== ECONOMY CONFIG ====================

ECONOMY_CONFIG_FIELDS = [
    'role_council', 'role_caller', 'role_member',
    'role_content_creator', 'role_logistic',
    'role_offcd', 'role_bomb',
    'voice_cta',
    'channel_events', 'channel_logger', 'channel_economylogs',
    'channel_zergregear', 'channel_bombregear', 'channel_massinfo',
    'channel_bombleaderchat', 'channel_tabsell', 'channel_logreview',
    'massinfo_message_id', 'splits_message_id',
    'guild_tax_percent', 'node_scout_percent', 'logger_percent',
    'role_trial', 'voice_waitingroom', 'trial_percent',
    'guild_ingame_name', 'mentoria_category_id',
    'role_mentor', 'role_officer', 'role_lead', 'mentoria_forum_id',
    'channel_energyalerts', 'energy_alert_threshold',
    'teamspeak_address', 'teamspeak_password',
    'channel_battleboard',
    'sheets_webhook_url', 'sheets_secret',
    'channel_recruitment', 'role_recruiter', 'recruiter_roles',
    'recruitment_message_id', 'recruitment_cooldown_hours',
    'role_recruitment',
    'voice_temp_mother',
]

ECONOMY_ROLE_FIELDS = [
    'role_council', 'role_caller', 'role_member',
    'role_content_creator', 'role_logistic',
    'role_offcd', 'role_bomb',
]

# ==================== GATE DE FUNÇÕES DA PLANILHA ====================
# Mapeia o NOME de uma função da planilha (case-insensitive) aos cargos do Discord
# que podem escolhê-la. Função sem linha aqui = aberta a todos.

async def add_role_gate(role_name: str, discord_role_id: int, added_by: int = None) -> bool:
    """Exige `discord_role_id` para a função `role_name`. True se inseriu (era novo)."""
    role_name = (role_name or '').strip()
    if not role_name or not discord_role_id:
        return False
    async with _db() as db:
        cur = await db.execute(
            'INSERT OR IGNORE INTO sheet_role_gates (role_name, discord_role_id, added_by) '
            'VALUES (?, ?, ?)',
            (role_name, int(discord_role_id), added_by),
        )
        await db.commit()
        return cur.rowcount > 0


async def remove_role_gate(role_name: str, discord_role_id: int = None) -> int:
    """Remove gate(s). Sem `discord_role_id` → limpa TODOS (função volta a ser aberta).
    Retorna quantas linhas saíram."""
    role_name = (role_name or '').strip()
    if not role_name:
        return 0
    async with _db() as db:
        if discord_role_id is None:
            cur = await db.execute(
                'DELETE FROM sheet_role_gates WHERE role_name = ? COLLATE NOCASE',
                (role_name,))
        else:
            cur = await db.execute(
                'DELETE FROM sheet_role_gates WHERE role_name = ? COLLATE NOCASE '
                'AND discord_role_id = ?',
                (role_name, int(discord_role_id)))
        await db.commit()
        return cur.rowcount


async def get_role_gates() -> dict:
    """Todos os gates: {role_name_lower: set(discord_role_id)}. {} se nada gated."""
    async with _db() as db:
        cur = await db.execute('SELECT role_name, discord_role_id FROM sheet_role_gates')
        rows = await cur.fetchall()
    out: dict = {}
    for name, rid in rows:
        out.setdefault((name or '').strip().lower(), set()).add(int(rid))
    return out

async def load_economy_config() -> dict:
    """Configuração de economia (cache curto por servidor; sempre um dict com todos
    os campos). É chamada em quase todo comando/loop — o cache evita reler a mesma
    linha dezenas de vezes. Invalidado na escrita (update_economy_config)."""
    gid = _current_guild.get()
    now = time.monotonic()
    hit = _econ_cache.get(gid)
    if hit and hit[0] > now:
        return dict(hit[1])                 # cópia: chamadores podem mutar à vontade
    cols = ", ".join(ECONOMY_CONFIG_FIELDS)
    async with _db() as db:
        cursor = await db.execute(f'SELECT {cols} FROM economy_config WHERE id = 1')
        row = await cursor.fetchone()
    cfg = ({k: None for k in ECONOMY_CONFIG_FIELDS} if not row
           else dict(zip(ECONOMY_CONFIG_FIELDS, row)))
    _econ_cache[gid] = (now + _ECON_CACHE_TTL, cfg)
    return dict(cfg)

async def update_economy_config(updates: dict):
    """
    Atualiza campos da configuração. Aceita apenas chaves em ECONOMY_CONFIG_FIELDS.
    `updates` é um dict {campo: valor}. Valores None são ignorados.
    """
    safe = {k: v for k, v in updates.items() if k in ECONOMY_CONFIG_FIELDS and v is not None}
    if not safe:
        return
    set_clause = ", ".join(f"{k} = ?" for k in safe.keys())
    values = tuple(safe.values())
    async with _db() as db:
        await db.execute(f'UPDATE economy_config SET {set_clause} WHERE id = 1', values)
        await db.commit()
    _econ_cache.pop(_current_guild.get(), None)   # invalida o cache deste servidor

async def get_configured_role_ids() -> set:
    """Retorna o conjunto de IDs de cargos configurados (apenas os definidos)."""
    cfg = await load_economy_config()
    return {cfg[k] for k in ECONOMY_ROLE_FIELDS if cfg.get(k) is not None}

# ==================== REGISTRO DE JOGADORES (Ponto 4) ====================

async def register_user(user_id: int, nick: str, registered_by: int = None) -> None:
    """Cadastra (ou atualiza) o nick de jogo de um usuário Discord."""
    nick = (nick or '').strip()
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    async with _db() as db:
        await db.execute(
            '''INSERT INTO registrations (user_id, nick, registered_by, registered_at, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                   nick = excluded.nick,
                   registered_by = excluded.registered_by,
                   updated_at = excluded.updated_at''',
            (user_id, nick, registered_by, now, now),
        )
        # Também grava no mapa de aliases (nick -> user_id). Se o nick já existia
        # associado a outro user, sobrescreve (transferindo histórico).
        try:
            await db.execute(
                '''INSERT INTO registration_aliases (nick, user_id, registered_by, registered_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(nick) DO UPDATE SET
                       user_id = excluded.user_id,
                       registered_by = excluded.registered_by,
                       updated_at = excluded.updated_at''',
                (nick, user_id, registered_by, now, now),
            )
        except Exception:
            pass
        # Vincula registros históricos de battleboard a este user_id (se o nick apareceu antes)
        try:
            await db.execute('UPDATE cta_battle_players SET user_id = ? WHERE LOWER(player) = LOWER(?)', (user_id, nick))
        except Exception:
            pass
        await db.commit()

async def get_registration(user_id: int) -> dict | None:
    """Retorna o cadastro de um usuário (dict) ou None se não cadastrado."""
    async with _db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            'SELECT * FROM registrations WHERE user_id = ?', (user_id,)
        )
        row = await cursor.fetchone()
    return dict(row) if row else None

async def get_registration_by_nick(nick: str) -> dict | None:
    """Retorna o cadastro pelo NICK de jogo (case-insensitive), ou None."""
    nick = (nick or '').strip()
    if not nick:
        return None
    async with _db() as db:
        db.row_factory = aiosqlite.Row
        # Procura primeiro nos aliases (nick -> user_id), depois no registro principal
        cursor = await db.execute('SELECT * FROM registration_aliases WHERE LOWER(nick) = LOWER(?)', (nick,))
        row = await cursor.fetchone()
        if row:
            return dict(row)
        cursor = await db.execute('SELECT * FROM registrations WHERE LOWER(nick) = LOWER(?)', (nick,))
        row = await cursor.fetchone()
    return dict(row) if row else None


async def get_nicks_for_user(user_id: int) -> list:
    """Retorna todos os nicks associados a um user_id (inclui o nick principal)."""
    out = []
    async with _db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute('SELECT nick FROM registration_aliases WHERE user_id = ?', (user_id,))
        rows = await cursor.fetchall()
        out.extend([r[0] for r in rows])
        cursor = await db.execute('SELECT nick FROM registrations WHERE user_id = ?', (user_id,))
        row = await cursor.fetchone()
        if row:
            primary = row[0]
            if primary and primary not in out:
                out.append(primary)
    return out


async def get_user_battle_kda(user_id: int):
    """Soma kills/mortes de TODAS as contas associadas a `user_id` (via cta_battle_players.user_id)."""
    async with _db() as db:
        cursor = await db.execute(
            '''SELECT COALESCE(SUM(kills), 0), COALESCE(SUM(deaths), 0), COUNT(DISTINCT event_id)
               FROM cta_battle_players WHERE user_id = ?''', (user_id,)
        )
        row = await cursor.fetchone()
    return (int(row[0]), int(row[1]), int(row[2])) if row else (0, 0, 0)


async def get_user_mvp_count(user_id: int) -> int:
    """Conta MVPs (qualquer tipo) para todas as nicks associadas ao user_id."""
    nicks = await get_nicks_for_user(user_id)
    if not nicks:
        return 0
    placeholders = ','.join('?' for _ in nicks)
    sql = f'SELECT COUNT(*) FROM cta_battle_mvps WHERE LOWER(player) IN ({placeholders})'
    async with _db() as db:
        cursor = await db.execute(sql, tuple(n.lower() for n in nicks))
        row = await cursor.fetchone()
    return int(row[0]) if row else 0


async def remove_all_registrations_for_user(user_id: int) -> bool:
    """Remove todas as contas (aliases + registro principal) associadas a `user_id`.
    Também desconecta cta_battle_players que apontavam para esse user (user_id -> NULL).
    Retorna True se alguma linha foi removida."""
    async with _db() as db:
        cur1 = await db.execute('DELETE FROM registration_aliases WHERE user_id = ?', (user_id,))
        cur2 = await db.execute('DELETE FROM registrations WHERE user_id = ?', (user_id,))
        await db.execute('UPDATE cta_battle_players SET user_id = NULL WHERE user_id = ?', (user_id,))
        await db.commit()
        return (cur1.rowcount + cur2.rowcount) > 0

async def get_all_registrations() -> list[dict]:
    """Retorna todos os cadastros (lista de dicts)."""
    async with _db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute('SELECT * FROM registrations')
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]

async def get_registered_user_ids() -> set:
    """Conjunto de user_ids cadastrados (consulta leve)."""
    async with _db() as db:
        cursor = await db.execute('SELECT user_id FROM registrations')
        rows = await cursor.fetchall()
    return {r[0] for r in rows}

async def unregister_user(user_id: int) -> bool:
    """Remove o cadastro de um usuário. True se removeu algo."""
    async with _db() as db:
        cursor = await db.execute('DELETE FROM registrations WHERE user_id = ?', (user_id,))
        await db.commit()
        return cursor.rowcount > 0

# ---------------- Mentoria: cadeia de categorias + tickets ----------------
async def set_mentoria_chain_base(category_id: int) -> None:
    """Reseta a cadeia de categorias de mentoria deixando só a base (posição 0)."""
    async with _db() as db:
        await db.execute('DELETE FROM mentoria_categories')
        await db.execute(
            'INSERT INTO mentoria_categories (category_id, position) VALUES (?, 0)',
            (category_id,),
        )
        await db.commit()

async def add_mentoria_category(category_id: int, position: int) -> None:
    """Adiciona uma categoria-extensão à cadeia."""
    async with _db() as db:
        await db.execute(
            '''INSERT OR REPLACE INTO mentoria_categories (category_id, position)
               VALUES (?, ?)''',
            (category_id, position),
        )
        await db.commit()

async def get_mentoria_categories() -> list:
    """IDs das categorias de mentoria, em ordem (base primeiro)."""
    async with _db() as db:
        cursor = await db.execute(
            'SELECT category_id FROM mentoria_categories ORDER BY position ASC'
        )
        return [r[0] for r in await cursor.fetchall()]

async def set_mentoria_channel(user_id: int, channel_id) -> None:
    """Define (ou limpa, com None) o canal de mentoria de um usuário cadastrado."""
    async with _db() as db:
        await db.execute(
            'UPDATE registrations SET mentoria_channel_id = ? WHERE user_id = ?',
            (channel_id, user_id),
        )
        await db.commit()

async def set_mentoria_delete_at(user_id: int, delete_at_iso) -> None:
    """Agenda (ou cancela, com None) a exclusão do canal de mentoria (grace de 7d)."""
    async with _db() as db:
        await db.execute(
            'UPDATE registrations SET mentoria_delete_at = ? WHERE user_id = ?',
            (delete_at_iso, user_id),
        )
        await db.commit()

async def set_mentoria_perms_snapshot(user_id: int, snapshot_json) -> None:
    """Guarda (ou limpa, com None) o JSON das permissões do canal antes de ocultar."""
    async with _db() as db:
        await db.execute(
            'UPDATE registrations SET mentoria_perms_snapshot = ? WHERE user_id = ?',
            (snapshot_json, user_id),
        )
        await db.commit()

async def upsert_registration_row(user_id: int, nick: str) -> None:
    """Garante a row de registrations (user_id+nick) SEM tocar nas colunas de
    mentoria. O /register agora é do bot novo (backend Postgres), mas o post de
    mentoria e o grace continuam vivendo AQUI — e os setters são UPDATE-only:
    sem essa row, set_mentoria_channel/set_mentoria_delete_at não fazem nada e
    o usuário novo nunca ganharia post (nem grace)."""
    async with _db() as db:
        await db.execute(
            '''INSERT INTO registrations (user_id, nick)
               VALUES (?, ?)
               ON CONFLICT(user_id) DO UPDATE
               SET nick = excluded.nick, updated_at = CURRENT_TIMESTAMP''',
            (user_id, nick),
        )
        await db.commit()

async def get_due_mentoria_deletions(now_iso: str) -> list:
    """
    Cadastros cujo prazo de 7 dias (fora da guilda) venceu — para apagar o canal
    de mentoria (se houver) E transferir o saldo pro guild bank.
    """
    async with _db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            '''SELECT * FROM registrations
               WHERE mentoria_delete_at IS NOT NULL
                 AND mentoria_delete_at <= ?''',
            (now_iso,),
        )
        return [dict(r) for r in await cursor.fetchall()]

# ---- Dedupe de PMs de escalação (Parte 4) ----

async def was_assignment_notified(event_id: int, name_norm: str, role_norm: str) -> bool:
    """True se já mandamos PM para este (CTA, nome, função)."""
    async with _db() as db:
        cursor = await db.execute(
            '''SELECT 1 FROM cta_assignment_notifications
               WHERE event_id = ? AND name_norm = ? AND role_norm = ?''',
            (event_id, name_norm, role_norm),
        )
        return await cursor.fetchone() is not None

async def mark_assignment_notified(event_id: int, name_norm: str, role_norm: str,
                                   user_id: int = None) -> None:
    """Marca (CTA, nome, função) como já notificado (idempotente)."""
    async with _db() as db:
        await db.execute(
            '''INSERT OR IGNORE INTO cta_assignment_notifications
               (event_id, name_norm, role_norm, user_id) VALUES (?, ?, ?, ?)''',
            (event_id, name_norm, role_norm, user_id),
        )
        await db.commit()

async def get_party_leader(event_id: int, party: int):
    """Líder atualmente NOTIFICADO da party: dict {'name_norm', 'user_id'} ou None."""
    async with _db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            'SELECT name_norm, user_id FROM cta_party_leaders WHERE event_id = ? AND party = ?',
            (event_id, party),
        )
        row = await cur.fetchone()
    return dict(row) if row else None

async def set_party_leader(event_id: int, party: int, name_norm: str,
                           user_id: int = None) -> None:
    """Registra/atualiza (sobrescreve) o líder notificado de uma party."""
    async with _db() as db:
        await db.execute(
            '''INSERT OR REPLACE INTO cta_party_leaders
               (event_id, party, name_norm, user_id, updated_at)
               VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)''',
            (event_id, party, name_norm, user_id),
        )
        await db.commit()

# ---- Opt-out de PMs por tipo (Parte 4) ----

async def add_pm_optout(user_id: int, pm_type: str) -> None:
    """Marca que `user_id` não quer mais receber PMs do tipo `pm_type`."""
    async with _db() as db:
        await db.execute(
            'INSERT OR IGNORE INTO pm_optouts (user_id, pm_type) VALUES (?, ?)',
            (user_id, pm_type),
        )
        await db.commit()

async def is_pm_opted_out(user_id: int, pm_type: str) -> bool:
    async with _db() as db:
        cursor = await db.execute(
            'SELECT 1 FROM pm_optouts WHERE user_id = ? AND pm_type = ?',
            (user_id, pm_type),
        )
        return await cursor.fetchone() is not None

# ==================== ROLE CHANGE LOG ====================

async def log_role_change(
    user_id: int,
    user_name: str,
    role_id: int,
    role_name: str,
    action: str,
):
    """Registra uma mudança de cargo (action: 'added' ou 'removed')."""
    async with _db() as db:
        await db.execute(
            '''INSERT INTO role_change_log
               (user_id, user_name, role_id, role_name, action)
               VALUES (?, ?, ?, ?, ?)''',
            (user_id, user_name, role_id, role_name, action)
        )
        await db.commit()

async def get_role_acquired_at(user_id: int, role_id: int):
    """
    Retorna o timestamp (string ISO) de quando o usuário recebeu o cargo pela última vez,
    ou None se não houver registro de 'added' para esse cargo.
    """
    async with _db() as db:
        cursor = await db.execute(
            '''SELECT changed_at FROM role_change_log
               WHERE user_id = ? AND role_id = ? AND action = 'added'
               ORDER BY changed_at DESC LIMIT 1''',
            (user_id, role_id)
        )
        row = await cursor.fetchone()
        return row[0] if row else None

# ==================== USER BALANCES ====================

async def get_user_balance(user_id: int) -> tuple:
    """
    Retorna (balance, total_earned). Cria a linha do usuário se ainda não existir.
    """
    async with _db() as db:
        await db.execute(
            'INSERT OR IGNORE INTO user_balances (user_id, balance, total_earned) VALUES (?, 0, 0)',
            (user_id,)
        )
        await db.commit()
        cursor = await db.execute(
            'SELECT balance, total_earned FROM user_balances WHERE user_id = ?',
            (user_id,)
        )
        row = await cursor.fetchone()
    return (row[0], row[1]) if row else (0, 0)

# ==================== ENERGIA DA GUILDA ====================
async def get_user_energy(user_id: int) -> int:
    """Saldo de energia do usuário (cria a linha se não existir)."""
    async with _db() as db:
        await db.execute(
            'INSERT OR IGNORE INTO user_balances (user_id, balance, total_earned, energy) '
            'VALUES (?, 0, 0, 0)', (user_id,)
        )
        await db.commit()
        cursor = await db.execute(
            'SELECT energy FROM user_balances WHERE user_id = ?', (user_id,)
        )
        row = await cursor.fetchone()
    return int(row[0]) if row and row[0] is not None else 0

async def add_energy(user_id: int, amount: int) -> int:
    """Soma `amount` (com sinal) ao saldo de energia. Retorna o novo saldo."""
    async with _db() as db:
        await db.execute(
            'INSERT OR IGNORE INTO user_balances (user_id, balance, total_earned, energy) '
            'VALUES (?, 0, 0, 0)', (user_id,)
        )
        await db.execute(
            'UPDATE user_balances SET energy = energy + ? WHERE user_id = ?',
            (amount, user_id)
        )
        cursor = await db.execute(
            'SELECT energy FROM user_balances WHERE user_id = ?', (user_id,)
        )
        row = await cursor.fetchone()
        await db.commit()
    return int(row[0]) if row else 0

async def set_energy(user_id: int, value: int) -> None:
    """Define o saldo de energia do usuário (ajuste manual)."""
    async with _db() as db:
        await db.execute(
            'INSERT OR IGNORE INTO user_balances (user_id, balance, total_earned, energy) '
            'VALUES (?, 0, 0, 0)', (user_id,)
        )
        await db.execute(
            'UPDATE user_balances SET energy = ? WHERE user_id = ?', (value, user_id)
        )
        await db.commit()

async def record_energy_entry(ts: str, player: str, reason: str, amount: int,
                              user_id: int) -> bool:
    """
    Registra um lançamento da log de energia (dedup por ts+player+amount).
    Retorna True se foi NOVO (deve ser aplicado), False se já tinha sido visto.
    """
    async with _db() as db:
        cursor = await db.execute(
            '''INSERT OR IGNORE INTO energy_log (ts, player, reason, amount, user_id)
               VALUES (?, ?, ?, ?, ?)''',
            (ts, player, reason, amount, user_id),
        )
        await db.commit()
        return cursor.rowcount > 0

async def get_low_energy(threshold: int) -> list:
    """Usuários com energia <= threshold (user_id, energy), do menor pro maior."""
    async with _db() as db:
        cursor = await db.execute(
            'SELECT user_id, energy FROM user_balances WHERE energy <= ? ORDER BY energy ASC',
            (threshold,)
        )
        return [(r[0], r[1]) for r in await cursor.fetchall()]

async def has_energy_history(user_id: int) -> bool:
    """True se o usuário já apareceu em alguma log de energia (energy_log).

    Usado p/ decidir se o saldo de energia aparece no /balance — quem nunca mexeu
    com energia não tem o campo poluindo o embed.
    """
    async with _db() as db:
        cursor = await db.execute(
            'SELECT 1 FROM energy_log WHERE user_id = ? LIMIT 1', (user_id,)
        )
        row = await cursor.fetchone()
    return row is not None

# ==================== WHITELIST DE ENERGIA ====================

async def add_energy_whitelist(user_id: int, added_by: int = None) -> bool:
    """Adiciona o usuário à whitelist de energia. True se foi NOVO."""
    async with _db() as db:
        cursor = await db.execute(
            'INSERT OR IGNORE INTO energy_whitelist (user_id, added_by) VALUES (?, ?)',
            (user_id, added_by)
        )
        await db.commit()
        return cursor.rowcount > 0

async def remove_energy_whitelist(user_id: int) -> bool:
    """Remove o usuário da whitelist de energia. True se estava lá."""
    async with _db() as db:
        cursor = await db.execute(
            'DELETE FROM energy_whitelist WHERE user_id = ?', (user_id,)
        )
        await db.commit()
        return cursor.rowcount > 0

async def is_energy_whitelisted(user_id: int) -> bool:
    async with _db() as db:
        cursor = await db.execute(
            'SELECT 1 FROM energy_whitelist WHERE user_id = ? LIMIT 1', (user_id,)
        )
        row = await cursor.fetchone()
    return row is not None

async def get_energy_whitelist() -> list:
    """Lista de user_ids na whitelist de energia."""
    async with _db() as db:
        cursor = await db.execute('SELECT user_id FROM energy_whitelist')
        return [r[0] for r in await cursor.fetchall()]

async def add_user_money(user_id: int, amount: int):
    """
    Adiciona `amount` ao saldo do usuário E ao total_earned (entrada de dinheiro).
    """
    if amount <= 0:
        return
    async with _db() as db:
        await db.execute(
            'INSERT OR IGNORE INTO user_balances (user_id, balance, total_earned) VALUES (?, 0, 0)',
            (user_id,)
        )
        await db.execute(
            '''UPDATE user_balances
               SET balance = balance + ?, total_earned = total_earned + ?
               WHERE user_id = ?''',
            (amount, amount, user_id)
        )
        await db.commit()

async def revert_user_money(user_id: int, amount: int) -> int:
    """
    ESTORNO de um crédito feito por add_user_money: subtrai `amount` de balance
    E de total_earned (o inverso exato de add_user_money). O balance PODE ficar
    negativo (o usuário talvez já tenha gasto a prata). Retorna o valor estornado.
    """
    if amount <= 0:
        return 0
    async with _db() as db:
        await db.execute(
            'INSERT OR IGNORE INTO user_balances (user_id, balance, total_earned) VALUES (?, 0, 0)',
            (user_id,)
        )
        await db.execute(
            '''UPDATE user_balances
               SET balance = balance - ?, total_earned = total_earned - ?
               WHERE user_id = ?''',
            (amount, amount, user_id)
        )
        await db.commit()
    return amount

async def remove_user_money(user_id: int, amount: int, allow_negative: bool = False) -> int:
    """
    Remove `amount` do saldo. Não afeta total_earned.

    Por padrão é clampeado pelo saldo disponível (não fica negativo). Com
    `allow_negative=True` subtrai o valor cheio — o saldo PODE ficar negativo
    (usado pela staff p/ punição/empréstimo). Retorna a quantia removida.
    """
    if amount <= 0:
        return 0
    async with _db() as db:
        await db.execute(
            'INSERT OR IGNORE INTO user_balances (user_id, balance, total_earned) VALUES (?, 0, 0)',
            (user_id,)
        )
        cursor = await db.execute(
            'SELECT balance FROM user_balances WHERE user_id = ?',
            (user_id,)
        )
        row = await cursor.fetchone()
        current = row[0] if row else 0
        actual = amount if allow_negative else min(current, amount)
        if actual <= 0:
            return 0
        await db.execute(
            'UPDATE user_balances SET balance = balance - ? WHERE user_id = ?',
            (actual, user_id)
        )
        await db.commit()
    return actual

async def transfer_money(from_id: int, to_id: int, amount: int) -> bool:
    """
    Transfere `amount` de `from_id` para `to_id`.
    O destinatário recebe no balance E no total_earned (entrada).
    Retorna False se o remetente não tem saldo suficiente.
    """
    if amount <= 0:
        return False
    async with _db() as db:
        cursor = await db.execute(
            'SELECT balance FROM user_balances WHERE user_id = ?', (from_id,)
        )
        row = await cursor.fetchone()
        from_balance = row[0] if row else 0
        if from_balance < amount:
            return False

        await db.execute(
            'INSERT OR IGNORE INTO user_balances (user_id, balance, total_earned) VALUES (?, 0, 0)',
            (to_id,)
        )
        await db.execute(
            'UPDATE user_balances SET balance = balance - ? WHERE user_id = ?',
            (amount, from_id)
        )
        await db.execute(
            '''UPDATE user_balances
               SET balance = balance + ?, total_earned = total_earned + ?
               WHERE user_id = ?''',
            (amount, amount, to_id)
        )
        await db.commit()
    return True

# ==================== GUILD BANK ====================

async def get_guild_bank_balance() -> int:
    async with _db() as db:
        cursor = await db.execute('SELECT balance FROM guild_bank WHERE id = 1')
        row = await cursor.fetchone()
    return row[0] if row else 0

async def add_guild_bank(amount: int):
    if amount <= 0:
        return
    async with _db() as db:
        await db.execute(
            'UPDATE guild_bank SET balance = balance + ? WHERE id = 1',
            (amount,)
        )
        await db.commit()

async def forfeit_balance_to_bank(user_id: int) -> int:
    """
    Move TODO o saldo atual do usuário para o guild bank (mantém total_earned).
    Atômico. Retorna o valor transferido (0 se não havia saldo).
    """
    async with _db() as db:
        cursor = await db.execute(
            'SELECT balance FROM user_balances WHERE user_id = ?', (user_id,)
        )
        row = await cursor.fetchone()
        amount = int(row[0]) if row and row[0] else 0
        if amount <= 0:
            return 0
        await db.execute(
            'UPDATE user_balances SET balance = 0 WHERE user_id = ?', (user_id,)
        )
        await db.execute(
            'UPDATE guild_bank SET balance = balance + ? WHERE id = 1', (amount,)
        )
        await db.commit()
    return amount

async def remove_guild_bank(amount: int, allow_negative: bool = False) -> int:
    """
    Remove `amount` do banco da guild. Por padrão clampeado (não fica negativo).
    Com `allow_negative=True` o banco PODE ficar negativo (decisão da staff).
    Retorna a quantia efetivamente removida.
    """
    if amount <= 0:
        return 0
    async with _db() as db:
        cursor = await db.execute('SELECT balance FROM guild_bank WHERE id = 1')
        row = await cursor.fetchone()
        current = row[0] if row else 0
        actual = amount if allow_negative else min(current, amount)
        if actual <= 0:
            return 0
        await db.execute(
            'UPDATE guild_bank SET balance = balance - ? WHERE id = 1',
            (actual,)
        )
        await db.commit()
    return actual

async def get_economy_overview() -> dict:
    """
    Retorna um snapshot da economia:
      { user_count, user_balances_sum, guild_bank, total }
    """
    async with _db() as db:
        c1 = await db.execute(
            'SELECT COUNT(*), COALESCE(SUM(balance), 0) FROM user_balances'
        )
        user_count, user_sum = await c1.fetchone()
        c2 = await db.execute('SELECT balance FROM guild_bank WHERE id = 1')
        row = await c2.fetchone()
        bank = row[0] if row else 0
    return {
        'user_count':         user_count,
        'user_balances_sum':  user_sum,
        'guild_bank':         bank,
        'total':              user_sum + bank,
    }

# ==================== ATTENDANCE / CTA EVENTS ====================

# >= 50% de presença = "participou do evento"
ATTENDANCE_THRESHOLD = 50

async def get_total_events_count(within_days: int = None) -> int:
    """Total de eventos finalizados. within_days=None → todos os tempos."""
    async with _db() as db:
        if within_days is None:
            cursor = await db.execute(
                'SELECT COUNT(*) FROM cta_events WHERE ended_at IS NOT NULL'
            )
        else:
            cursor = await db.execute(
                f"SELECT COUNT(*) FROM cta_events "
                f"WHERE ended_at IS NOT NULL "
                f"AND ended_at >= datetime('now', '-{int(within_days)} days')"
            )
        row = await cursor.fetchone()
    return row[0] if row else 0

async def get_user_attendance_count(user_id: int, within_days: int = None) -> int:
    """Quantidade de eventos que o usuário participou (>= threshold)."""
    sql = '''
        SELECT COUNT(*) FROM cta_attendance a
        JOIN cta_events e ON e.id = a.event_id
        WHERE a.user_id = ? AND a.percent >= ? AND e.ended_at IS NOT NULL
    '''
    params = [user_id, ATTENDANCE_THRESHOLD]
    if within_days is not None:
        sql += f" AND e.ended_at >= datetime('now', '-{int(within_days)} days')"

    async with _db() as db:
        cursor = await db.execute(sql, params)
        row = await cursor.fetchone()
    return row[0] if row else 0

async def get_user_attendance_rank(user_id: int):
    """
    Posição do usuário no ranking de attendance (lifetime).
    Retorna (rank, count) ou (None, 0) se o usuário nunca participou.
    """
    async with _db() as db:
        cursor = await db.execute('''
            WITH counts AS (
                SELECT a.user_id, COUNT(*) AS cnt
                FROM cta_attendance a
                JOIN cta_events e ON e.id = a.event_id
                WHERE a.percent >= ? AND e.ended_at IS NOT NULL
                GROUP BY a.user_id
            ),
            ranked AS (
                SELECT user_id, cnt,
                       RANK() OVER (ORDER BY cnt DESC) AS rnk
                FROM counts
            )
            SELECT rnk, cnt FROM ranked WHERE user_id = ?
        ''', (ATTENDANCE_THRESHOLD, user_id))
        row = await cursor.fetchone()
    return (row[0], row[1]) if row else (None, 0)

async def get_user_top_caller(user_id: int):
    """
    Caller com quem o usuário mais participou de eventos.
    Retorna (caller_id, caller_name, count) ou None.
    """
    async with _db() as db:
        cursor = await db.execute('''
            SELECT e.caller_id, e.caller_name, COUNT(*) AS cnt
            FROM cta_attendance a
            JOIN cta_events e ON e.id = a.event_id
            WHERE a.user_id = ? AND a.percent >= ? AND e.ended_at IS NOT NULL
            GROUP BY e.caller_id, e.caller_name
            ORDER BY cnt DESC
            LIMIT 1
        ''', (user_id, ATTENDANCE_THRESHOLD))
        row = await cursor.fetchone()
    return row if row else None

async def get_user_last_event_attended(user_id: int):
    """
    Timestamp (string ISO) do último evento que o usuário participou.
    Retorna None se nunca participou.
    """
    async with _db() as db:
        cursor = await db.execute('''
            SELECT e.ended_at FROM cta_attendance a
            JOIN cta_events e ON e.id = a.event_id
            WHERE a.user_id = ? AND a.percent >= ? AND e.ended_at IS NOT NULL
            ORDER BY e.ended_at DESC LIMIT 1
        ''', (user_id, ATTENDANCE_THRESHOLD))
        row = await cursor.fetchone()
    return row[0] if row else None

async def get_user_oldest_role_acquired(user_id: int, role_ids: list):
    """
    Timestamp do log 'added' MAIS ANTIGO para qualquer um dos role_ids passados.
    Retorna None se não houver nenhum log (= cargos eram pré-existentes ao bot).
    """
    if not role_ids:
        return None
    placeholders = ','.join('?' * len(role_ids))
    async with _db() as db:
        cursor = await db.execute(
            f'''SELECT MIN(changed_at) FROM role_change_log
                WHERE user_id = ? AND action = 'added' AND role_id IN ({placeholders})''',
            (user_id, *role_ids)
        )
        row = await cursor.fetchone()
    return row[0] if row and row[0] else None

async def get_membership_since(user_id: int, role_ids: list):
    """
    Início do período CONTÍNUO mais recente em que o usuário manteve ao menos um
    dos `role_ids` (ex.: [membro, trial]). A TROCA entre eles NÃO quebra a
    continuidade — só zera se a pessoa ficou sem NENHUM dos dois. Assim o tempo
    de casa não reseta quando o bot troca membro↔trial.

    Reconstrói a linha do tempo a partir do role_change_log. Retorna o ISO do
    início do streak atual, ou None se não houver logs (cargo pré-existente).
    """
    if not role_ids:
        return None
    placeholders = ','.join('?' * len(role_ids))
    async with _db() as db:
        cursor = await db.execute(
            f'''SELECT changed_at, action, role_id FROM role_change_log
                WHERE user_id = ? AND role_id IN ({placeholders})
                ORDER BY changed_at ASC,
                         CASE action WHEN 'added' THEN 0 ELSE 1 END ASC,
                         id ASC''',
            (user_id, *role_ids),
        )
        rows = await cursor.fetchall()
    if not rows:
        return None
    held = set()
    streak_start = None
    for changed_at, action, role_id in rows:
        if action == 'added':
            was_empty = (len(held) == 0)
            held.add(role_id)
            if was_empty:
                streak_start = changed_at      # 0 → ≥1: começa um novo streak
        elif action == 'removed':
            held.discard(role_id)
            if not held:
                streak_start = None            # zerou: streak quebrou
    return streak_start

# ==================== LEADERBOARD ====================

# Quem aparece no leaderboard: economicamente ATIVO — já arrecadou algo OU tem
# saldo não-zero (inclui saldo NEGATIVO de punição/empréstimo). Ordena pelo SALDO
# ATUAL (não pela prata histórica), pra refletir o que a pessoa realmente tem.
_LEADERBOARD_FILTER = '(total_earned > 0 OR balance <> 0)'


async def get_leaderboard_page(offset: int, limit: int):
    """
    Página do leaderboard ordenada pelo SALDO ATUAL (balance) DESC.
    Cada linha: (user_id, balance, total_earned, attendance_count).
    """
    async with _db() as db:
        cursor = await db.execute(f'''
            SELECT
                ub.user_id,
                ub.balance,
                ub.total_earned,
                COALESCE((
                    SELECT COUNT(*) FROM cta_attendance a
                    JOIN cta_events e ON e.id = a.event_id
                    WHERE a.user_id = ub.user_id
                      AND a.percent >= ?
                      AND e.ended_at IS NOT NULL
                ), 0) AS attendance_count
            FROM user_balances ub
            WHERE {_LEADERBOARD_FILTER}
            ORDER BY ub.balance DESC, ub.user_id ASC
            LIMIT ? OFFSET ?
        ''', (ATTENDANCE_THRESHOLD, limit, offset))
        rows = await cursor.fetchall()
    return [tuple(r) for r in rows]

async def get_leaderboard_count() -> int:
    """Quantos usuários entram no leaderboard (ativos: arrecadaram OU têm saldo ≠ 0)."""
    async with _db() as db:
        cursor = await db.execute(
            f'SELECT COUNT(*) FROM user_balances WHERE {_LEADERBOARD_FILTER}'
        )
        row = await cursor.fetchone()
    return row[0] if row else 0


# ---------------- Leaderboards alternativos: attendance / mvps / kills / deaths
async def get_attendance_leaderboard_count() -> int:
    async with _db() as db:
        cursor = await db.execute(
            '''SELECT COUNT(*) FROM (
                 SELECT a.user_id FROM cta_attendance a
                 JOIN cta_events e ON e.id = a.event_id
                 WHERE a.percent >= ? AND e.ended_at IS NOT NULL
                 GROUP BY a.user_id
             )''', (ATTENDANCE_THRESHOLD,)
        )
        row = await cursor.fetchone()
    return int(row[0]) if row else 0


async def get_attendance_leaderboard_page(offset: int, limit: int):
    async with _db() as db:
        cursor = await db.execute(
            '''SELECT user_id, COUNT(*) AS cnt FROM cta_attendance a
               JOIN cta_events e ON e.id = a.event_id
               WHERE a.percent >= ? AND e.ended_at IS NOT NULL
               GROUP BY user_id
               ORDER BY cnt DESC, user_id ASC
               LIMIT ? OFFSET ?''', (ATTENDANCE_THRESHOLD, limit, offset)
        )
        rows = await cursor.fetchall()
    return [tuple(r) for r in rows]


async def get_attendance_rank_for_user(user_id: int):
    async with _db() as db:
        cursor = await db.execute('''
            WITH counts AS (
                SELECT a.user_id, COUNT(*) AS cnt
                FROM cta_attendance a
                JOIN cta_events e ON e.id = a.event_id
                WHERE a.percent >= ? AND e.ended_at IS NOT NULL
                GROUP BY a.user_id
            ), ranked AS (
                SELECT user_id, cnt, RANK() OVER (ORDER BY cnt DESC) AS rnk FROM counts
            )
            SELECT rnk, cnt FROM ranked WHERE user_id = ?
        ''', (ATTENDANCE_THRESHOLD, user_id))
        row = await cursor.fetchone()
    return (int(row[0]), int(row[1])) if row else (None, 0)


async def get_mvp_leaderboard_count() -> int:
    async with _db() as db:
        cursor = await db.execute(
            '''SELECT COUNT(*) FROM (
                 SELECT COALESCE(a.user_id, r.user_id, player) AS owner_key
                 FROM (
                   SELECT DISTINCT player FROM cta_battle_mvps
                 ) bp
                 LEFT JOIN registration_aliases a ON LOWER(a.nick) = LOWER(bp.player)
                 LEFT JOIN registrations r ON LOWER(r.nick) = LOWER(bp.player)
                 GROUP BY owner_key
             )''')
        row = await cursor.fetchone()
    return int(row[0]) if row else 0


async def get_mvp_leaderboard_page(offset: int, limit: int):
    async with _db() as db:
        cursor = await db.execute(
            '''WITH mapped AS (
                 SELECT player,
                        COALESCE(a.user_id, r.user_id) AS user_id
                 FROM cta_battle_mvps m
                 LEFT JOIN registration_aliases a ON LOWER(a.nick) = LOWER(m.player)
                 LEFT JOIN registrations r ON LOWER(r.nick) = LOWER(m.player)
             ), owners AS (
                 SELECT COALESCE(user_id, player) AS owner_key, COUNT(*) AS cnt
                 FROM mapped
                 GROUP BY owner_key
             )
             SELECT owner_key, cnt FROM owners
             ORDER BY cnt DESC, owner_key ASC
             LIMIT ? OFFSET ?''', (limit, offset)
        )
        rows = await cursor.fetchall()
    # owner_key can be INTEGER (user_id) or TEXT (player name)
    return [tuple(r) for r in rows]


async def get_mvp_rank_for_player(player_name: str):
    # Backwards-compatible: compute rank for a PLAYER name (when unlinked)
    async with _db() as db:
        cursor = await db.execute(
            '''WITH mapped AS (
                 SELECT player, COALESCE(a.user_id, r.user_id) AS user_id
                 FROM cta_battle_mvps m
                 LEFT JOIN registration_aliases a ON LOWER(a.nick) = LOWER(m.player)
                 LEFT JOIN registrations r ON LOWER(r.nick) = LOWER(m.player)
             ), owners AS (
                 SELECT COALESCE(user_id, player) AS owner_key, COUNT(*) AS cnt
                 FROM mapped
                 GROUP BY owner_key
             ), ranked AS (
                 SELECT owner_key, cnt, RANK() OVER (ORDER BY cnt DESC) AS rnk FROM owners
             )
             SELECT rnk, cnt FROM ranked WHERE owner_key = ? COLLATE NOCASE
            ''', (player_name,)
        )
        row = await cursor.fetchone()
    return (int(row[0]), int(row[1])) if row else (None, 0)


async def get_mvp_rank_for_user(user_id: int):
    async with _db() as db:
        cursor = await db.execute(
            '''WITH mapped AS (
                 SELECT player, COALESCE(a.user_id, r.user_id) AS user_id
                 FROM cta_battle_mvps m
                 LEFT JOIN registration_aliases a ON LOWER(a.nick) = LOWER(m.player)
                 LEFT JOIN registrations r ON LOWER(r.nick) = LOWER(m.player)
             ), owners AS (
                 SELECT COALESCE(user_id, player) AS owner_key, COUNT(*) AS cnt
                 FROM mapped
                 GROUP BY owner_key
             ), ranked AS (
                 SELECT owner_key, cnt, RANK() OVER (ORDER BY cnt DESC) AS rnk FROM owners
             )
             SELECT rnk, cnt FROM ranked WHERE owner_key = ?
            ''', (user_id,)
        )
        row = await cursor.fetchone()
    return (int(row[0]), int(row[1])) if row else (None, 0)


async def get_kills_leaderboard_count() -> int:
    async with _db() as db:
        cursor = await db.execute(
            '''SELECT COUNT(*) FROM (
                 SELECT COALESCE(user_id, player) AS owner_key
                 FROM (
                   SELECT player, COALESCE(user_id, (SELECT user_id FROM registration_aliases WHERE LOWER(nick)=LOWER(player))) AS user_id
                   FROM cta_battle_players
                 )
                 GROUP BY owner_key
             )''')
        row = await cursor.fetchone()
    return int(row[0]) if row else 0


async def get_kills_leaderboard_page(offset: int, limit: int):
    async with _db() as db:
        cursor = await db.execute(
            '''WITH mapped AS (
                 SELECT player, COALESCE(cp.user_id, a.user_id, r.user_id) AS user_id, cp.kills
                 FROM cta_battle_players cp
                 LEFT JOIN registration_aliases a ON LOWER(a.nick) = LOWER(cp.player)
                 LEFT JOIN registrations r ON LOWER(r.nick) = LOWER(cp.player)
             ), owners AS (
                 SELECT COALESCE(user_id, player) AS owner_key, COALESCE(SUM(kills),0) AS kills
                 FROM mapped
                 GROUP BY owner_key
             )
             SELECT owner_key, kills FROM owners
             ORDER BY kills DESC, owner_key ASC
             LIMIT ? OFFSET ?''', (limit, offset)
        )
        rows = await cursor.fetchall()
    return [tuple(r) for r in rows]


async def get_kills_rank_for_player(player_name: str):
    # Backwards-compatible: rank for an unlinked player name
    async with _db() as db:
        cursor = await db.execute(
            '''WITH mapped AS (
                 SELECT player, COALESCE(cp.user_id, a.user_id, r.user_id) AS user_id, cp.kills
                 FROM cta_battle_players cp
                 LEFT JOIN registration_aliases a ON LOWER(a.nick) = LOWER(cp.player)
                 LEFT JOIN registrations r ON LOWER(r.nick) = LOWER(cp.player)
             ), owners AS (
                 SELECT COALESCE(user_id, player) AS owner_key, COALESCE(SUM(kills),0) AS kills
                 FROM mapped
                 GROUP BY owner_key
             ), ranked AS (
                 SELECT owner_key, kills, RANK() OVER (ORDER BY kills DESC) AS rnk FROM owners
             )
             SELECT rnk, kills FROM ranked WHERE owner_key = ? COLLATE NOCASE
            ''', (player_name,)
        )
        row = await cursor.fetchone()
    return (int(row[0]), int(row[1])) if row else (None, 0)


async def get_kills_rank_for_user(user_id: int):
    async with _db() as db:
        cursor = await db.execute(
            '''WITH mapped AS (
                 SELECT player, COALESCE(cp.user_id, a.user_id, r.user_id) AS user_id, cp.kills
                 FROM cta_battle_players cp
                 LEFT JOIN registration_aliases a ON LOWER(a.nick) = LOWER(cp.player)
                 LEFT JOIN registrations r ON LOWER(r.nick) = LOWER(cp.player)
             ), owners AS (
                 SELECT COALESCE(user_id, player) AS owner_key, COALESCE(SUM(kills),0) AS kills
                 FROM mapped
                 GROUP BY owner_key
             ), ranked AS (
                 SELECT owner_key, kills, RANK() OVER (ORDER BY kills DESC) AS rnk FROM owners
             )
             SELECT rnk, kills FROM ranked WHERE owner_key = ?
            ''', (user_id,)
        )
        row = await cursor.fetchone()
    return (int(row[0]), int(row[1])) if row else (None, 0)


async def get_deaths_leaderboard_count() -> int:
    async with _db() as db:
        cursor = await db.execute(
            '''SELECT COUNT(*) FROM (
                 SELECT COALESCE(user_id, player) AS owner_key
                 FROM (
                   SELECT player, COALESCE(user_id, (SELECT user_id FROM registration_aliases WHERE LOWER(nick)=LOWER(player))) AS user_id
                   FROM cta_battle_players
                 )
                 GROUP BY owner_key
             )''')
        row = await cursor.fetchone()
    return int(row[0]) if row else 0


async def get_deaths_leaderboard_page(offset: int, limit: int):
    async with _db() as db:
        cursor = await db.execute(
            '''WITH mapped AS (
                 SELECT player, COALESCE(cp.user_id, a.user_id, r.user_id) AS user_id, cp.deaths
                 FROM cta_battle_players cp
                 LEFT JOIN registration_aliases a ON LOWER(a.nick) = LOWER(cp.player)
                 LEFT JOIN registrations r ON LOWER(r.nick) = LOWER(cp.player)
             ), owners AS (
                 SELECT COALESCE(user_id, player) AS owner_key, COALESCE(SUM(deaths),0) AS deaths
                 FROM mapped
                 GROUP BY owner_key
             )
             SELECT owner_key, deaths FROM owners
             ORDER BY deaths DESC, owner_key ASC
             LIMIT ? OFFSET ?''', (limit, offset)
        )
        rows = await cursor.fetchall()
    return [tuple(r) for r in rows]


async def get_deaths_rank_for_player(player_name: str):
    # Backwards-compatible: rank for an unlinked player name
    async with _db() as db:
        cursor = await db.execute(
            '''WITH mapped AS (
                 SELECT player, COALESCE(cp.user_id, a.user_id, r.user_id) AS user_id, cp.deaths
                 FROM cta_battle_players cp
                 LEFT JOIN registration_aliases a ON LOWER(a.nick) = LOWER(cp.player)
                 LEFT JOIN registrations r ON LOWER(r.nick) = LOWER(cp.player)
             ), owners AS (
                 SELECT COALESCE(user_id, player) AS owner_key, COALESCE(SUM(deaths),0) AS deaths
                 FROM mapped
                 GROUP BY owner_key
             ), ranked AS (
                 SELECT owner_key, deaths, RANK() OVER (ORDER BY deaths DESC) AS rnk FROM owners
             )
             SELECT rnk, deaths FROM ranked WHERE owner_key = ? COLLATE NOCASE
            ''', (player_name,)
        )
        row = await cursor.fetchone()
    return (int(row[0]), int(row[1])) if row else (None, 0)


async def get_deaths_rank_for_user(user_id: int):
    async with _db() as db:
        cursor = await db.execute(
            '''WITH mapped AS (
                 SELECT player, COALESCE(cp.user_id, a.user_id, r.user_id) AS user_id, cp.deaths
                 FROM cta_battle_players cp
                 LEFT JOIN registration_aliases a ON LOWER(a.nick) = LOWER(cp.player)
                 LEFT JOIN registrations r ON LOWER(r.nick) = LOWER(cp.player)
             ), owners AS (
                 SELECT COALESCE(user_id, player) AS owner_key, COALESCE(SUM(deaths),0) AS deaths
                 FROM mapped
                 GROUP BY owner_key
             ), ranked AS (
                 SELECT owner_key, deaths, RANK() OVER (ORDER BY deaths DESC) AS rnk FROM owners
             )
             SELECT rnk, deaths FROM ranked WHERE owner_key = ?
            ''', (user_id,)
        )
        row = await cursor.fetchone()
    return (int(row[0]), int(row[1])) if row else (None, 0)

async def create_cta_event(
    caller_id: int,
    caller_name: str,
    started_at_iso: str,
    guild_id: int,
    announcement_channel_id: int = None,
    announcement_message_id: int = None,
    comp: str = None,
    message: str = None,
) -> int:
    """Cria um novo evento CTA. Retorna o event_id."""
    async with _db() as db:
        c = await db.execute(
            '''INSERT INTO cta_events
               (caller_id, caller_name, started_at, guild_id,
                announcement_channel_id, announcement_message_id, comp, cta_message)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            (caller_id, caller_name, started_at_iso, guild_id,
             announcement_channel_id, announcement_message_id, comp, message)
        )
        await db.commit()
        return c.lastrowid

async def get_active_event():
    """
    Retorna o evento ativo (ended_at IS NULL) como dict ou None.
    Só pode existir 1 evento ativo por vez.
    """
    async with _db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            '''SELECT * FROM cta_events
               WHERE ended_at IS NULL
               ORDER BY id DESC LIMIT 1'''
        )
        row = await cursor.fetchone()
    return dict(row) if row else None

async def get_event_by_id(event_id: int):
    async with _db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute('SELECT * FROM cta_events WHERE id = ?', (event_id,))
        row = await cursor.fetchone()
    return dict(row) if row else None

async def get_recent_unfinalized_event_ids(limit: int = 5) -> list:
    """IDs dos eventos encerrados mas com split NÃO finalizado (mais recentes).
    Usado pelo loop que mantém os embeds/botões vivos."""
    async with _db() as db:
        cursor = await db.execute(
            '''SELECT id FROM cta_events
               WHERE ended_at IS NOT NULL
                 AND split_finalized = 0
                 AND event_message_id IS NOT NULL
               ORDER BY id DESC LIMIT ?''',
            (limit,),
        )
        return [r[0] for r in await cursor.fetchall()]

async def get_due_sheet_deletions(now_iso: str) -> list[dict]:
    """
    Eventos cuja planilha tem exclusão agendada já vencida
    (sheet_delete_at <= agora) e que ainda têm página (sheet_page) pra apagar.
    Usado pelo loop de limpeza: CTAs em andamento têm a planilha apagada 2h
    após o /callout.
    """
    async with _db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            '''SELECT * FROM cta_events
               WHERE sheet_delete_at IS NOT NULL
                 AND sheet_delete_at <= ?
                 AND sheet_page IS NOT NULL''',
            (now_iso,)
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]

async def add_snapshot(event_id: int, present_user_data: list):
    """
    Registra um snapshot: incrementa total_snapshots do evento e
    snapshots_present de cada usuário presente. Cria a linha se não existir.

    present_user_data: lista de (user_id, user_name).
    """
    async with _db() as db:
        # Incrementa total_snapshots do evento
        await db.execute(
            'UPDATE cta_events SET total_snapshots = total_snapshots + 1 WHERE id = ?',
            (event_id,)
        )
        # Garante linha para cada usuário e incrementa
        for uid, uname in present_user_data:
            await db.execute(
                '''INSERT OR IGNORE INTO cta_attendance
                   (event_id, user_id, user_name, snapshots_present, snapshots_total)
                   VALUES (?, ?, ?, 0, 0)''',
                (event_id, uid, uname)
            )
            await db.execute(
                '''UPDATE cta_attendance
                   SET snapshots_present = snapshots_present + 1,
                       user_name = ?
                   WHERE event_id = ? AND user_id = ?''',
                (uname, event_id, uid)
            )
        await db.commit()

async def end_cta_event(event_id: int, had_regear: bool):
    """Finaliza o evento: seta ended_at e had_regear, calcula percent de cada participante."""
    async with _db() as db:
        # Buscar total_snapshots
        cursor = await db.execute(
            'SELECT total_snapshots FROM cta_events WHERE id = ?', (event_id,)
        )
        row = await cursor.fetchone()
        total = row[0] if row else 0

        # Atualizar evento
        from datetime import datetime, timezone
        ended_iso = datetime.now(timezone.utc).isoformat()
        await db.execute(
            'UPDATE cta_events SET ended_at = ?, had_regear = ? WHERE id = ?',
            (ended_iso, 1 if had_regear else 0, event_id)
        )

        # Atualizar snapshots_total + percent de cada attendance.
        # base_percent = participação crua (sem desconto). O desconto de trial é
        # aplicado depois, pela cog (que conhece quem tem o cargo Trial).
        if total > 0:
            await db.execute(
                '''UPDATE cta_attendance
                   SET snapshots_total = ?,
                       base_percent = CAST(snapshots_present * 100.0 / ? AS INTEGER),
                       percent = CAST(snapshots_present * 100.0 / ? AS INTEGER)
                   WHERE event_id = ?''',
                (total, total, total, event_id)
            )
        else:
            await db.execute(
                'UPDATE cta_attendance SET snapshots_total = 0, base_percent = 0, percent = 0 WHERE event_id = ?',
                (event_id,)
            )
        await db.commit()

async def mark_event_trials(event_id: int, trial_user_ids) -> None:
    """Marca is_trial=1 para os user_ids dados (trials) e 0 para os demais do evento."""
    ids = list(trial_user_ids or [])
    async with _db() as db:
        # zera todos
        await db.execute('UPDATE cta_attendance SET is_trial = 0 WHERE event_id = ?', (event_id,))
        if ids:
            ph = ",".join("?" for _ in ids)
            await db.execute(
                f'UPDATE cta_attendance SET is_trial = 1 WHERE event_id = ? AND user_id IN ({ph})',
                (event_id, *ids),
            )
        await db.commit()

async def recompute_trial_discount(event_id: int, trial_percent: int) -> None:
    """
    Recalcula percent a partir de base_percent aplicando o desconto de trial:
      · is_trial=1  -> percent = base_percent * (100 - trial_percent) / 100
      · is_trial=0  -> percent = base_percent
    Linhas SEM base_percent (ajuste manual via /attendance) ficam intactas.
    """
    perc = max(0, min(100, int(trial_percent or 0)))
    async with _db() as db:
        await db.execute(
            '''UPDATE cta_attendance
               SET percent = CASE
                   WHEN base_percent IS NULL THEN percent
                   WHEN is_trial = 1 THEN CAST(base_percent * (100 - ?) / 100.0 AS INTEGER)
                   ELSE base_percent
               END
               WHERE event_id = ?''',
            (perc, event_id),
        )
        await db.commit()

async def get_open_trial_event_ids() -> list:
    """IDs de eventos ABERTOS (encerrados mas split não finalizado) que têm trials."""
    async with _db() as db:
        cursor = await db.execute(
            '''SELECT DISTINCT a.event_id
               FROM cta_attendance a
               JOIN cta_events e ON e.id = a.event_id
               WHERE a.is_trial = 1
                 AND e.ended_at IS NOT NULL
                 AND e.split_finalized = 0'''
        )
        return [row[0] for row in await cursor.fetchall()]

async def get_event_attendances(event_id: int):
    """Retorna lista de (user_id, user_name, percent, silver_received) para o evento."""
    async with _db() as db:
        cursor = await db.execute(
            '''SELECT user_id, user_name, COALESCE(percent, 0) AS percent, silver_received
               FROM cta_attendance
               WHERE event_id = ?
               ORDER BY percent DESC, user_name ASC''',
            (event_id,)
        )
        return [tuple(r) for r in await cursor.fetchall()]

# ---------------- Battleboard (/bb) ----------------
async def add_battle_player(event_id: int, player: str, guild: str,
                            kills: int = 0, deaths: int = 0, healing: int = 0) -> None:
    """Registra/acumula um player visto na batalha do CTA (dedup por evento+player)."""
    # Tenta vincular o player a um user_id conhecido (caso o nick já esteja registrado)
    reg = await get_registration_by_nick(player)
    user_id = reg.get('user_id') if reg else None
    async with _db() as db:
        await db.execute(
            '''INSERT INTO cta_battle_players (event_id, player, guild, kills, deaths, healing, user_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(event_id, player) DO UPDATE SET
                 guild   = excluded.guild,
                 kills   = kills + excluded.kills,
                 deaths  = deaths + excluded.deaths,
                 healing = healing + excluded.healing,
                 user_id = COALESCE(user_id, excluded.user_id)''',
            (event_id, player, guild, kills, deaths, healing, user_id),
        )
        await db.commit()


async def add_battle_mvp(event_id: int, player: str, kind: str) -> None:
    """Marca um MVP para um player numa batalha (kind: 'dps' ou 'healer')."""
    if kind not in ('dps', 'healer'):
        return
    async with _db() as db:
        try:
            await db.execute(
                'INSERT OR IGNORE INTO cta_battle_mvps (event_id, player, kind) VALUES (?, ?, ?)',
                (event_id, player, kind),
            )
            await db.commit()
        except Exception:
            pass


async def get_player_mvp_count(player_name: str) -> int:
    """Retorna o número total de MVPs (de qualquer tipo) ganhos por `player_name`."""
    name = (player_name or '').strip()
    if not name:
        return 0
    async with _db() as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM cta_battle_mvps WHERE player = ? COLLATE NOCASE",
            (name,),
        )
        row = await cursor.fetchone()
    return int(row[0]) if row else 0

async def get_battle_players(event_id: int) -> list:
    """Players (dicts) que apareceram na(s) batalha(s) do CTA."""
    async with _db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            'SELECT * FROM cta_battle_players WHERE event_id = ? ORDER BY kills DESC, player ASC',
            (event_id,),
        )
        return [dict(r) for r in await cursor.fetchall()]

async def get_player_battle_kda(player_name: str):
    """Soma abates/mortes do jogador (por nick) em TODAS as batalhas registradas
    (battleboards do /bb). Retorna (kills, deaths, eventos)."""
    name = (player_name or '').strip()
    if not name:
        return (0, 0, 0)
    async with _db() as db:
        cursor = await db.execute(
            '''SELECT COALESCE(SUM(kills), 0), COALESCE(SUM(deaths), 0),
                      COUNT(DISTINCT event_id)
               FROM cta_battle_players WHERE player = ? COLLATE NOCASE''',
            (name,),
        )
        row = await cursor.fetchone()
    return (int(row[0]), int(row[1]), int(row[2])) if row else (0, 0, 0)

async def get_event_function_user_ids(event_id: int) -> set:
    """user_ids que registraram funções na planilha (mass-info) deste CTA."""
    async with _db() as db:
        cursor = await db.execute(
            'SELECT DISTINCT user_id FROM cta_function_logs WHERE event_id = ?',
            (event_id,),
        )
        return {r[0] for r in await cursor.fetchall()}

async def get_enlisted_user_ids(event_id: int) -> set:
    """user_ids ADICIONADOS ao split após o evento (enlisted=1) — pra marcar no embed
    quem entrou via late-attend (estava fora da call)."""
    async with _db() as db:
        cursor = await db.execute(
            'SELECT user_id FROM cta_attendance WHERE event_id = ? AND enlisted = 1',
            (event_id,)
        )
        return {r[0] for r in await cursor.fetchall()}

# ---- Loggers (logs reagidas com ✅) ----

async def get_event_by_logger_thread(thread_id: int) -> dict | None:
    """Encontra o evento pela thread de logger (pra creditar a log reagida)."""
    async with _db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            'SELECT * FROM cta_events WHERE logger_thread_id = ?', (thread_id,)
        )
        row = await cursor.fetchone()
    return dict(row) if row else None

async def add_event_logger(event_id: int, message_id: int, user_id: int) -> None:
    """Credita o autor de uma log reagida com ✅ (1 crédito por mensagem)."""
    async with _db() as db:
        await db.execute(
            '''INSERT INTO cta_event_loggers (event_id, message_id, user_id)
               VALUES (?, ?, ?)
               ON CONFLICT(event_id, message_id) DO UPDATE SET user_id = excluded.user_id''',
            (event_id, message_id, user_id),
        )
        await db.commit()

async def remove_event_logger_message(event_id: int, message_id: int) -> bool:
    """Remove o crédito de uma mensagem (quando o ✅ é tirado). True se removeu."""
    async with _db() as db:
        cursor = await db.execute(
            'DELETE FROM cta_event_loggers WHERE event_id = ? AND message_id = ?',
            (event_id, message_id),
        )
        await db.commit()
        return cursor.rowcount > 0

async def get_event_loggers(event_id: int) -> list:
    """user_ids DISTINTOS creditados como loggers do evento (logs reagidas)."""
    async with _db() as db:
        cursor = await db.execute(
            'SELECT DISTINCT user_id FROM cta_event_loggers WHERE event_id = ?',
            (event_id,),
        )
        return [r[0] for r in await cursor.fetchall()]

# ---------------- Loot-log: submissões de .csv (Fase 1) ----------------
async def get_log_submission(event_id: int, submitter_id: int) -> dict | None:
    """Submissão de um usuário para um CTA (ou None)."""
    async with _db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            'SELECT * FROM cta_log_submissions WHERE event_id = ? AND submitter_id = ?',
            (event_id, submitter_id),
        )
        row = await cursor.fetchone()
    return dict(row) if row else None

async def add_log_submission(event_id: int, submitter_id: int, submitter_nick: str,
                             file_name: str, file_hash: str, row_count: int) -> bool:
    """
    Registra (ou substitui) a submissão de log de um usuário para um CTA.
    1 submissão por (event_id, submitter_id) — reenvio sobrescreve.
    Retorna True se foi REENVIO (já existia), False se foi a primeira.
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    async with _db() as db:
        cursor = await db.execute(
            'SELECT 1 FROM cta_log_submissions WHERE event_id = ? AND submitter_id = ?',
            (event_id, submitter_id),
        )
        was_update = (await cursor.fetchone()) is not None
        await db.execute(
            '''INSERT INTO cta_log_submissions
                 (event_id, submitter_id, submitter_nick, file_name, file_hash, row_count, submitted_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(event_id, submitter_id) DO UPDATE SET
                 submitter_nick = excluded.submitter_nick,
                 file_name      = excluded.file_name,
                 file_hash      = excluded.file_hash,
                 row_count      = excluded.row_count,
                 submitted_at   = excluded.submitted_at''',
            (event_id, submitter_id, submitter_nick, file_name, file_hash, row_count, now),
        )
        await db.commit()
    return was_update

async def get_log_submissions(event_id: int) -> list:
    """Todas as submissões de log de um CTA (dicts), mais recentes primeiro."""
    async with _db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            'SELECT * FROM cta_log_submissions WHERE event_id = ? ORDER BY submitted_at DESC',
            (event_id,),
        )
        return [dict(r) for r in await cursor.fetchall()]

async def replace_log_events(event_id: int, submitter_id: int, rows: list) -> int:
    """
    Substitui os eventos de coleta de UM submissor neste CTA (reenvio sobrescreve).
    `rows`: lista de dicts {ts, item_id, item_name, quantity, looted_by,
    looted_by_guild, looted_by_alliance, looted_from, looted_from_guild,
    looted_from_alliance}. Retorna quantos foram inseridos.
    """
    async with _db() as db:
        await db.execute(
            'DELETE FROM cta_log_events WHERE event_id = ? AND submitter_id = ?',
            (event_id, submitter_id),
        )
        if rows:
            await db.executemany(
                '''INSERT INTO cta_log_events
                     (event_id, submitter_id, ts, item_id, item_name, quantity,
                      looted_by, looted_by_guild, looted_by_alliance,
                      looted_from, looted_from_guild, looted_from_alliance)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                [(event_id, submitter_id, r.get('ts'), r.get('item_id'),
                  r.get('item_name'), int(r.get('quantity') or 1),
                  r.get('looted_by'), r.get('looted_by_guild'),
                  r.get('looted_by_alliance'),
                  r.get('looted_from'), r.get('looted_from_guild'),
                  r.get('looted_from_alliance'))
                 for r in rows],
            )
        await db.commit()
    return len(rows)

async def get_log_events(event_id: int) -> list:
    """Todos os eventos de coleta normalizados deste CTA (de todos os submissores)."""
    async with _db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            'SELECT * FROM cta_log_events WHERE event_id = ?', (event_id,)
        )
        return [dict(r) for r in await cursor.fetchall()]

async def get_due_logger_thread_deletions(now_iso: str) -> list:
    """Eventos cuja thread PÚBLICA de logger já passou dos 30 min (a apagar)."""
    async with _db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            '''SELECT * FROM cta_events
               WHERE logger_thread_delete_at IS NOT NULL
                 AND logger_thread_delete_at <= ?
                 AND logger_thread_id IS NOT NULL''',
            (now_iso,),
        )
        return [dict(r) for r in await cursor.fetchall()]

async def update_attendance_percent(event_id: int, user_id: int, user_name: str, percent: int):
    """
    Define o percent diretamente para um participante (botão de alterar/adicionar).
    Cria a linha se não existir.
    """
    percent = max(0, min(100, int(percent)))
    async with _db() as db:
        await db.execute(
            '''INSERT OR IGNORE INTO cta_attendance
               (event_id, user_id, user_name, snapshots_present, snapshots_total, percent)
               VALUES (?, ?, ?, 0, 0, ?)''',
            (event_id, user_id, user_name, percent)
        )
        # base_percent = NULL marca como ajuste MANUAL: valor é final (o desconto
        # de trial e o recompute do /settrialperc não mexem nessa linha).
        await db.execute(
            '''UPDATE cta_attendance
               SET percent = ?, base_percent = NULL, user_name = ?
               WHERE event_id = ? AND user_id = ?''',
            (percent, user_name, event_id, user_id)
        )
        await db.commit()

async def delete_attendance(event_id: int, user_id: int) -> bool:
    """Remove um participante do evento. Retorna True se algo foi removido."""
    async with _db() as db:
        cursor = await db.execute(
            'DELETE FROM cta_attendance WHERE event_id = ? AND user_id = ?',
            (event_id, user_id)
        )
        await db.commit()
        return cursor.rowcount > 0


# ==================== SPLITS / ALISTAR (Ponto 3) ====================

async def get_unfinalized_splits() -> list:
    """
    CTAs já ENCERRADOS (ended_at != NULL) mas com split ainda NÃO finalizado.
    Fonte da embed-tracker de splits no bombleaderchat. Lista de dicts.
    """
    async with _db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            '''SELECT * FROM cta_events
               WHERE ended_at IS NOT NULL
                 AND COALESCE(split_finalized, 0) = 0
               ORDER BY ended_at DESC'''
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def enlist_member(event_id: int, user_id: int, user_name: str,
                        enlisted_by: int, percent: int = 100) -> bool:
    """
    Marca um membro como "alistado/vouched" (enlisted=1) num CTA encerrado.

    · Se o usuário NÃO está na attendance (estava fora da zerg): entra com
      percent cheio (default 100) e enlisted=1 — passa a receber silver.
    · Se JÁ está na attendance (estava na zerg) e ainda não foi vouched: apenas
      marca enlisted=1, MANTENDO o percent real — assim ele sai da lista de
      "não registraram" / é dispensado da punição, sem mexer na share dele.
    · Se já está vouched (enlisted=1): não faz nada.

    Retorna True se alistou/vouched agora, False se já estava vouched.
    """
    percent = max(0, min(100, int(percent)))
    async with _db() as db:
        cur = await db.execute(
            'SELECT enlisted FROM cta_attendance WHERE event_id = ? AND user_id = ?',
            (event_id, user_id),
        )
        row = await cur.fetchone()
        if row is not None and (row[0] or 0) == 1:
            return False  # já vouched/alistado
        if row is None:
            await db.execute(
                '''INSERT INTO cta_attendance
                   (event_id, user_id, user_name, snapshots_present, snapshots_total,
                    percent, enlisted, enlisted_by)
                   VALUES (?, ?, ?, 0, 0, ?, 1, ?)''',
                (event_id, user_id, user_name, percent, enlisted_by),
            )
        else:
            await db.execute(
                '''UPDATE cta_attendance
                   SET enlisted = 1, enlisted_by = ?, user_name = ?
                   WHERE event_id = ? AND user_id = ?''',
                (enlisted_by, user_name, event_id, user_id),
            )
        await db.commit()
        return True


async def unenlist_member(event_id: int, user_id: int) -> bool:
    """
    Desfaz o alistamento (enlisted=0).
    · Quem estava na zerg (snapshots_present>0): apenas remove o "voto"
      (enlisted=0), MANTENDO a presença — nunca apaga um membro da zerg.
    · Quem foi alistado de fora (snapshots_present=0): remove a linha inteira.
    Retorna True se alguma coisa mudou.
    """
    async with _db() as db:
        cur = await db.execute(
            '''UPDATE cta_attendance SET enlisted = 0, enlisted_by = NULL
               WHERE event_id = ? AND user_id = ? AND enlisted = 1
                 AND snapshots_present > 0''',
            (event_id, user_id),
        )
        if cur.rowcount > 0:
            await db.commit()
            return True
        cur = await db.execute(
            '''DELETE FROM cta_attendance
               WHERE event_id = ? AND user_id = ? AND enlisted = 1
                 AND snapshots_present = 0''',
            (event_id, user_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def get_enlisted_members(event_id: int) -> list:
    """Lista de dicts dos membros alistados (enlisted=1) de um CTA."""
    async with _db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            '''SELECT user_id, user_name, COALESCE(percent, 0) AS percent, enlisted_by
               FROM cta_attendance
               WHERE event_id = ? AND COALESCE(enlisted, 0) = 1
               ORDER BY user_name ASC''',
            (event_id,),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_attendance_counts(event_id: int) -> tuple:
    """Retorna (zerg_count, enlisted_count) das attendances de um evento."""
    async with _db() as db:
        cursor = await db.execute(
            '''SELECT
                 SUM(CASE WHEN COALESCE(enlisted, 0) = 0 THEN 1 ELSE 0 END),
                 SUM(CASE WHEN COALESCE(enlisted, 0) = 1 THEN 1 ELSE 0 END)
               FROM cta_attendance WHERE event_id = ?''',
            (event_id,),
        )
        row = await cursor.fetchone()
    if not row:
        return (0, 0)
    return (row[0] or 0, row[1] or 0)


# ==================================================================
# Ponto 4 — quem registrou (ping) vs quem estava no split
# ==================================================================
async def get_function_logger_ids(event_id: int) -> set:
    """Conjunto de user_ids que registraram funções (pingaram) no mass-info."""
    async with _db() as db:
        cursor = await db.execute(
            'SELECT DISTINCT user_id FROM cta_function_logs WHERE event_id = ?',
            (event_id,),
        )
        rows = await cursor.fetchall()
    return {r[0] for r in rows}


async def get_function_log_names(event_id: int) -> set:
    """Nomes (de planilha) distintos que registraram funções no mass-info.
    Usado pra contar 'presentes na planilha' unindo com os escalados (que vêm
    da escalação por nome)."""
    async with _db() as db:
        cursor = await db.execute(
            'SELECT DISTINCT user_name FROM cta_function_logs WHERE event_id = ?',
            (event_id,),
        )
        rows = await cursor.fetchall()
    return {(r[0] or '').strip() for r in rows if (r[0] or '').strip()}


async def get_function_log_users(event_id: int) -> list:
    """(user_id, user_name) distintos que registraram funções no evento — usado
    pelo /poke pra DM-ar quem se registrou (cruzando o nick com o menu da guilda)."""
    async with _db() as db:
        cursor = await db.execute(
            '''SELECT user_id, user_name FROM cta_function_logs
               WHERE event_id = ? GROUP BY user_id''',
            (event_id,),
        )
        return [(r[0], r[1]) for r in await cursor.fetchall()]


async def get_enlisted_user_ids(event_id: int) -> set:
    """Conjunto de user_ids alistados (enlisted=1) — dispensados de pingar."""
    async with _db() as db:
        cursor = await db.execute(
            'SELECT user_id FROM cta_attendance WHERE event_id = ? AND COALESCE(enlisted,0)=1',
            (event_id,),
        )
        rows = await cursor.fetchall()
    return {r[0] for r in rows}


async def get_non_pingers(event_id: int) -> list:
    """
    Jogadores que estavam no split (percent > 0) mas NÃO registraram funções
    no mass-info E não foram alistados (enlisted=0). Lista de dicts
    {user_id, user_name, percent} ordenada por percent desc.
    """
    async with _db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            '''SELECT a.user_id, a.user_name, COALESCE(a.percent, 0) AS percent
               FROM cta_attendance a
               WHERE a.event_id = ?
                 AND COALESCE(a.percent, 0) > 0
                 AND COALESCE(a.enlisted, 0) = 0
                 AND a.user_id NOT IN (
                     SELECT user_id FROM cta_function_logs WHERE event_id = ?
                 )
               ORDER BY percent DESC, a.user_name ASC''',
            (event_id, event_id),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def record_punishments(event_id: int, rows: list) -> int:
    """
    Grava (substituindo) as punições de um evento. `rows` é uma lista de
    dicts {user_id, user_name, percent, amount}. Retorna quantas foram gravadas.
    """
    async with _db() as db:
        n = 0
        for r in rows:
            await db.execute(
                '''INSERT INTO cta_punishments (event_id, user_id, user_name, percent, amount)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(event_id, user_id) DO UPDATE SET
                       user_name = excluded.user_name,
                       percent   = excluded.percent,
                       amount    = excluded.amount,
                       created_at = CURRENT_TIMESTAMP''',
                (event_id, r['user_id'], r['user_name'], int(r['percent']), int(r['amount'])),
            )
            n += 1
        await db.commit()
    return n


async def get_event_punishments(event_id: int) -> list:
    """Lista de dicts das punições gravadas para o evento."""
    async with _db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            '''SELECT user_id, user_name, percent, amount
               FROM cta_punishments WHERE event_id = ?
               ORDER BY amount DESC, user_name ASC''',
            (event_id,),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]

async def update_event_meta(event_id: int, **kwargs):
    """Atualiza colunas arbitrárias do evento. Use com cuidado (chaves devem ser válidas)."""
    allowed = {
        'event_thread_id', 'logger_thread_id', 'regear_thread_id', 'event_message_id',
        'tab_location', 'repair_value', 'lootlogger_done', 'tab_image_url', 'split_finalized',
        'announcement_message_id', 'comp', 'sheet_page', 'sheet_url',
        'sheet_delete_at', 'pre_start_moved', 'prestart_msg_id', 'startboard_msg_id',
        'logreview_msg_id', 'logreview_thread_id', 'logger_thread_delete_at',
        'started_at', 'battleboard_url', 'functions_released',
    }
    safe = {k: v for k, v in kwargs.items() if k in allowed}
    if not safe:
        return
    set_clause = ", ".join(f"{k} = ?" for k in safe.keys())
    values = tuple(safe.values()) + (event_id,)
    async with _db() as db:
        await db.execute(f'UPDATE cta_events SET {set_clause} WHERE id = ?', values)
        await db.commit()

async def get_event_by_message_id(message_id: int):
    """Encontra o evento pela ID da mensagem do embed (usado por callbacks de botão)."""
    async with _db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            'SELECT * FROM cta_events WHERE event_message_id = ?', (message_id,)
        )
        row = await cursor.fetchone()
    return dict(row) if row else None

async def get_nodes_near(target_ts: int, threshold_seconds: int = 1800):
    """
    Retorna nodes do log com spawn_timestamp dentro de ±threshold do target.
    Cada linha: (id, node_type, map_name, added_by, added_by_id, spawn_timestamp).
    """
    low  = target_ts - threshold_seconds
    high = target_ts + threshold_seconds
    async with _db() as db:
        cursor = await db.execute(
            '''SELECT id, node_type, map_name, added_by, added_by_id, spawn_timestamp
               FROM node_events_log
               WHERE spawn_timestamp BETWEEN ? AND ?
               ORDER BY spawn_timestamp ASC''',
            (low, high)
        )
        return [tuple(r) for r in await cursor.fetchall()]

async def set_event_captured_nodes(event_id: int, captured_node_log_ids: list):
    """
    Substitui completamente a lista de nodes capturados do evento.
    Marca os IDs passados como capturados=1; remove o resto.
    """
    async with _db() as db:
        # Limpa entradas anteriores
        await db.execute('DELETE FROM cta_event_nodes WHERE event_id = ?', (event_id,))
        # Insere os capturados
        for nid in captured_node_log_ids:
            await db.execute(
                '''INSERT INTO cta_event_nodes (event_id, node_log_id, captured)
                   VALUES (?, ?, 1)''',
                (event_id, int(nid))
            )
        await db.commit()

async def get_event_captured_nodes(event_id: int):
    """
    Retorna lista de nodes capturados (join com node_events_log).
    Cada linha: (id, node_type, map_name, added_by, added_by_id, spawn_timestamp).
    """
    async with _db() as db:
        cursor = await db.execute(
            '''SELECT nel.id, nel.node_type, nel.map_name,
                      nel.added_by, nel.added_by_id, nel.spawn_timestamp
               FROM cta_event_nodes cen
               JOIN node_events_log nel ON nel.id = cen.node_log_id
               WHERE cen.event_id = ? AND cen.captured = 1
               ORDER BY nel.spawn_timestamp ASC''',
            (event_id,)
        )
        return [tuple(r) for r in await cursor.fetchall()]

# ==================== SPLIT (Fase 5) ====================

async def set_event_split(event_id: int, repair_value: int, tab_location: str,
                          tab_image_url: str = None):
    """Define os dados do split (valor + localização da tab + URL do print da tab).
    Marca split_defined=1 para distinguir 'definido como 0' de 'nunca definido'."""
    async with _db() as db:
        await db.execute(
            '''UPDATE cta_events
               SET repair_value = ?, tab_location = ?, tab_image_url = ?, split_defined = 1
               WHERE id = ?''',
            (repair_value, tab_location, tab_image_url, event_id)
        )
        await db.commit()

async def set_event_tab_image(event_id: int, blob: bytes):
    """Guarda os BYTES do print da tab (anexado no invoice na finalização)."""
    async with _db() as db:
        await db.execute(
            'UPDATE cta_events SET tab_image_blob = ? WHERE id = ?',
            (blob, event_id))
        await db.commit()

async def mark_event_split_finalized(event_id: int):
    async with _db() as db:
        await db.execute(
            'UPDATE cta_events SET split_finalized = 1 WHERE id = ?', (event_id,)
        )
        await db.commit()

async def set_attendance_silver(event_id: int, user_id: int, silver: int):
    async with _db() as db:
        await db.execute(
            '''UPDATE cta_attendance SET silver_received = ?
               WHERE event_id = ? AND user_id = ?''',
            (silver, event_id, user_id)
        )
        await db.commit()

# ==================== LEDGER DE PAGAMENTOS (estorno do split) ====================

async def record_event_payouts(event_id: int, rows: list):
    """
    Registra os créditos do split p/ permitir estorno exato no /deleteevent.
    `rows` = lista de (kind, user_id, amount); kind ∈ participant|scout|logger|guild_bank
    (user_id None p/ guild_bank). Ignora amount <= 0. Reescreve o ledger do evento
    (apaga o anterior) p/ ser idempotente caso a finalização rode de novo.
    """
    rows = [(k, u, int(a)) for (k, u, a) in rows if a and int(a) > 0]
    async with _db() as db:
        await db.execute('DELETE FROM cta_payouts WHERE event_id = ?', (event_id,))
        if rows:
            await db.executemany(
                'INSERT INTO cta_payouts (event_id, kind, user_id, amount) VALUES (?, ?, ?, ?)',
                [(event_id, k, u, a) for (k, u, a) in rows]
            )
        await db.commit()

async def get_event_payouts(event_id: int) -> list:
    """Retorna os pagamentos registrados do evento: lista de (kind, user_id, amount)."""
    async with _db() as db:
        cur = await db.execute(
            'SELECT kind, user_id, amount FROM cta_payouts WHERE event_id = ?',
            (event_id,)
        )
        rows = await cur.fetchall()
    return [(r[0], r[1], r[2]) for r in rows]

# ==================== REGEARS (Fase 5) ====================

async def create_regear(
    user_id: int, user_name: str, guild_id: int,
    channel_id: int, message_id: int, image_url: str,
) -> int:
    """Cria um regear pendente e retorna o id."""
    async with _db() as db:
        c = await db.execute(
            '''INSERT INTO regears
               (user_id, user_name, guild_id, channel_id, message_id, image_url)
               VALUES (?, ?, ?, ?, ?, ?)''',
            (user_id, user_name, guild_id, channel_id, message_id, image_url)
        )
        await db.commit()
        return c.lastrowid

async def get_regear_by_message_id(message_id: int):
    """Retorna o regear pela message_id do embed (dict) ou None."""
    async with _db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            'SELECT * FROM regears WHERE message_id = ?', (message_id,)
        )
        row = await cursor.fetchone()
    return dict(row) if row else None

async def log_cta_function(
    event_id: int, user_id: int, user_name: str,
    function1: str, function2: str, function3: str,
    synced_sheets: bool = False,
) -> int:
    """Salva uma entrada de função declarada pelo usuário. Retorna o id criado."""
    async with _db() as db:
        c = await db.execute(
            '''INSERT INTO cta_function_logs
               (event_id, user_id, user_name, function1, function2, function3, synced_sheets)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (event_id, user_id, user_name, function1, function2, function3,
             1 if synced_sheets else 0),
        )
        await db.commit()
        return c.lastrowid

async def mark_function_log_synced(log_id: int, sheet_row: int | None = None):
    async with _db() as db:
        if sheet_row is not None:
            await db.execute(
                'UPDATE cta_function_logs SET synced_sheets = 1, sheet_row = ? WHERE id = ?',
                (sheet_row, log_id),
            )
        else:
            await db.execute(
                'UPDATE cta_function_logs SET synced_sheets = 1 WHERE id = ?', (log_id,)
            )
        await db.commit()

async def get_user_function_log(event_id: int, user_id: int) -> dict | None:
    """Registro de funções mais recente de um usuário num CTA (ou None).

    Usado pelo mass-info pra: (1) saber se a pessoa já se inscreveu e mostrar a
    confirmação de alteração; (2) recuperar o NOME gravado na planilha e a LINHA
    onde as roles foram escritas, pra conseguir limpar antes de re-registrar."""
    async with _db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            '''SELECT * FROM cta_function_logs
               WHERE event_id = ? AND user_id = ?
               ORDER BY id DESC LIMIT 1''',
            (event_id, user_id),
        )
        row = await cursor.fetchone()
    return dict(row) if row else None

async def get_user_recent_roles(user_id: int, limit: int = 5) -> list:
    """Últimas funções DISTINTAS que o usuário registrou (mais recente primeiro).
    Usado pra pôr atalhos no topo do seletor de funções do mass-info."""
    async with _db() as db:
        cursor = await db.execute(
            '''SELECT function1, function2, function3 FROM cta_function_logs
               WHERE user_id = ? ORDER BY id DESC LIMIT 60''',
            (user_id,),
        )
        rows = await cursor.fetchall()
    out, seen = [], set()
    for f1, f2, f3 in rows:
        for f in (f1, f2, f3):
            r = (f or '').strip()
            if not r:
                continue
            k = r.lower()
            if k in seen:
                continue
            seen.add(k)
            out.append(r)
            if len(out) >= limit:
                return out
    return out

async def delete_user_function_logs(event_id: int, user_id: int) -> int:
    """Apaga TODOS os logs de função de um usuário num CTA. Retorna nº removido."""
    async with _db() as db:
        c = await db.execute(
            'DELETE FROM cta_function_logs WHERE event_id = ? AND user_id = ?',
            (event_id, user_id),
        )
        await db.commit()
        return c.rowcount

async def get_pending_ctas():
    """
    Retorna lista de CTAs com split ainda não finalizado (inclui as já
    encerradas aguardando split). Usado pelo tracker de splits (Ponto 3).
    Cada linha: dict do evento.
    """
    async with _db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            '''SELECT * FROM cta_events
               WHERE COALESCE(split_finalized, 0) = 0
               ORDER BY started_at DESC'''
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_active_ctas():
    """
    Retorna lista de CTAs EM ANDAMENTO (ended_at IS NULL).
    Normalmente 0 ou 1 (só 1 CTA ativa por vez). Usado pelos embeds de
    mass-info e looterchat. Cada linha: dict do evento.
    """
    async with _db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            '''SELECT * FROM cta_events
               WHERE ended_at IS NULL
               ORDER BY started_at DESC'''
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]

async def get_non_finalized_events(limit: int = 25) -> list:
    """
    CTAs ainda NÃO finalizados (split_finalized = 0), do mais novo pro mais antigo.
    Inclui em andamento, agendados e encerrados-sem-split. Usado pelo /openregear.
    """
    async with _db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            '''SELECT * FROM cta_events
               WHERE COALESCE(split_finalized, 0) = 0
               ORDER BY id DESC
               LIMIT ?''',
            (limit,),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]

async def update_regear_status(
    message_id: int, status: str, handled_by: int = None, value: int = None,
):
    """Atualiza status (paid/denied/removed), handler e opcionalmente valor."""
    from datetime import datetime, timezone
    handled_at = datetime.now(timezone.utc).isoformat()

    updates = ['status = ?', 'handled_by = ?', 'handled_at = ?']
    params  = [status, handled_by, handled_at]
    if value is not None:
        updates.append('value = ?')
        params.append(value)
    params.append(message_id)

    async with _db() as db:
        await db.execute(
            f'UPDATE regears SET {", ".join(updates)} WHERE message_id = ?',
            params,
        )
        await db.commit()

# ==================== LOOTER LOTTERY (Ponto 2) ====================

async def add_looter_entry(event_id: int, user_id: int, user_name: str) -> bool:
    """
    Adiciona um usuário à lista de sorteio de looters de um CTA.
    Retorna True se entrou agora, False se já estava na lista.
    """
    async with _db() as db:
        try:
            await db.execute(
                '''INSERT INTO cta_looter_entries (event_id, user_id, user_name)
                   VALUES (?, ?, ?)''',
                (event_id, user_id, user_name),
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False


async def remove_looter_entry(event_id: int, user_id: int) -> bool:
    """Remove um usuário da lista de sorteio. Retorna True se removeu."""
    async with _db() as db:
        cursor = await db.execute(
            'DELETE FROM cta_looter_entries WHERE event_id = ? AND user_id = ?',
            (event_id, user_id),
        )
        await db.commit()
        return cursor.rowcount > 0


async def is_looter_entered(event_id: int, user_id: int) -> bool:
    async with _db() as db:
        cursor = await db.execute(
            'SELECT 1 FROM cta_looter_entries WHERE event_id = ? AND user_id = ?',
            (event_id, user_id),
        )
        return await cursor.fetchone() is not None


async def get_looter_entries(event_id: int) -> list:
    """Retorna lista de dicts das entradas de looter de um CTA."""
    async with _db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            '''SELECT * FROM cta_looter_entries
               WHERE event_id = ? ORDER BY entered_at ASC''',
            (event_id,),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def count_looter_entries(event_id: int) -> int:
    async with _db() as db:
        cursor = await db.execute(
            'SELECT COUNT(*) FROM cta_looter_entries WHERE event_id = ?',
            (event_id,),
        )
        row = await cursor.fetchone()
    return row[0] if row else 0


async def get_massinfo_ping_count(event_id: int) -> int:
    """
    Número de "pings" no mass-info de um CTA = usuários distintos que
    registraram funções via o embed de mass-info.
    """
    async with _db() as db:
        cursor = await db.execute(
            'SELECT COUNT(DISTINCT user_id) FROM cta_function_logs WHERE event_id = ?',
            (event_id,),
        )
        row = await cursor.fetchone()
    return row[0] if row else 0


async def get_drawn_looter_count(event_id: int) -> int:
    """Quantos looters já foram sorteados (drawn=1) para o evento."""
    async with _db() as db:
        cursor = await db.execute(
            'SELECT COUNT(*) FROM cta_looter_entries WHERE event_id = ? AND drawn = 1',
            (event_id,),
        )
        row = await cursor.fetchone()
    return row[0] if row else 0


async def get_undrawn_looter_entries(event_id: int) -> list:
    """Inscritos ainda NÃO sorteados (candidatos ao próximo sorteio)."""
    async with _db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            '''SELECT * FROM cta_looter_entries
               WHERE event_id = ? AND drawn = 0 ORDER BY entered_at ASC''',
            (event_id,),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def add_drawn_looters(event_id: int, drawn_user_ids: list, zerg_user_ids: set = None):
    """
    Marca os user_ids como sorteados (drawn=1) SEM resetar os já sorteados —
    usado pelo sorteio incremental/contínuo. Sinaliza quem também está na zerg.
    """
    zerg_user_ids = zerg_user_ids or set()
    async with _db() as db:
        for uid in drawn_user_ids:
            await db.execute(
                '''UPDATE cta_looter_entries
                   SET drawn = 1, also_in_zerg = ?
                   WHERE event_id = ? AND user_id = ?''',
                (1 if uid in zerg_user_ids else 0, event_id, uid),
            )
        await db.commit()


async def get_event_zerg_user_ids(event_id: int) -> set:
    """Conjunto de user_ids que estão na zerg (cta_attendance) do evento."""
    async with _db() as db:
        cursor = await db.execute(
            'SELECT user_id FROM cta_attendance WHERE event_id = ?', (event_id,)
        )
        rows = await cursor.fetchall()
    return {r[0] for r in rows}


# ==================== BATTLEMOUNTS ====================

async def add_bm_entry(event_id: int, user_id: int, user_name: str, mounts: list) -> None:
    """Entra/atualiza a fila de BM com as 3-5 montarias escolhidas (só se não sorteado)."""
    mounts_str = ",".join(str(m).strip() for m in mounts if str(m).strip())
    async with _db() as db:
        await db.execute(
            '''INSERT INTO cta_bm_entries (event_id, user_id, user_name, mounts)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(event_id, user_id) DO UPDATE SET
                   mounts = excluded.mounts,
                   user_name = excluded.user_name
               WHERE cta_bm_entries.drawn = 0''',
            (event_id, user_id, user_name, mounts_str),
        )
        await db.commit()

async def remove_bm_entry(event_id: int, user_id: int) -> bool:
    """Sai da fila de BM (só se ainda não sorteado). True se removeu."""
    async with _db() as db:
        cursor = await db.execute(
            'DELETE FROM cta_bm_entries WHERE event_id = ? AND user_id = ? AND drawn = 0',
            (event_id, user_id),
        )
        await db.commit()
        return cursor.rowcount > 0

async def is_bm_entered(event_id: int, user_id: int) -> bool:
    async with _db() as db:
        cursor = await db.execute(
            'SELECT 1 FROM cta_bm_entries WHERE event_id = ? AND user_id = ?',
            (event_id, user_id),
        )
        return await cursor.fetchone() is not None

async def get_bm_entry(event_id: int, user_id: int) -> dict | None:
    async with _db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            'SELECT * FROM cta_bm_entries WHERE event_id = ? AND user_id = ?',
            (event_id, user_id),
        )
        row = await cursor.fetchone()
    if not row:
        return None
    d = dict(row)
    d['mounts'] = d['mounts'].split(',') if d.get('mounts') else []
    return d

async def count_bm_entries(event_id: int) -> int:
    async with _db() as db:
        cursor = await db.execute(
            'SELECT COUNT(*) FROM cta_bm_entries WHERE event_id = ?', (event_id,)
        )
        row = await cursor.fetchone()
    return row[0] if row else 0

async def get_undrawn_bm_entries(event_id: int) -> list:
    """Inscritos ainda NÃO sorteados: [{user_id, user_name, mounts:[...]}]."""
    async with _db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            'SELECT user_id, user_name, mounts FROM cta_bm_entries WHERE event_id = ? AND drawn = 0',
            (event_id,),
        )
        rows = await cursor.fetchall()
    return [
        {'user_id': r['user_id'], 'user_name': r['user_name'],
         'mounts': r['mounts'].split(',') if r['mounts'] else []}
        for r in rows
    ]

async def get_drawn_bm_entries(event_id: int) -> list:
    """Sorteados: [{user_id, user_name, assigned_mount}]."""
    async with _db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            '''SELECT user_id, user_name, assigned_mount FROM cta_bm_entries
               WHERE event_id = ? AND drawn = 1''',
            (event_id,),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]

async def get_drawn_bm_count(event_id: int) -> int:
    async with _db() as db:
        cursor = await db.execute(
            'SELECT COUNT(*) FROM cta_bm_entries WHERE event_id = ? AND drawn = 1', (event_id,)
        )
        row = await cursor.fetchone()
    return row[0] if row else 0

async def mark_bm_drawn(event_id: int, assignments: list) -> None:
    """assignments: lista de (user_id, mount). Marca sorteado + montaria atribuída."""
    async with _db() as db:
        for uid, mount in assignments:
            await db.execute(
                '''UPDATE cta_bm_entries SET drawn = 1, assigned_mount = ?
                   WHERE event_id = ? AND user_id = ?''',
                (mount, event_id, uid),
            )
        await db.commit()

async def get_event_zerg_count(event_id: int) -> int:
    """Tamanho da zerg REAL (quem foi detectado na call: snapshots_present > 0)."""
    async with _db() as db:
        cursor = await db.execute(
            'SELECT COUNT(*) FROM cta_attendance WHERE event_id = ? AND snapshots_present > 0',
            (event_id,),
        )
        row = await cursor.fetchone()
    return row[0] if row else 0

async def add_battlemount_to_zerg(event_id: int, user_id: int, user_name: str) -> None:
    """Adiciona a BM sorteada à presença do evento com 100% (faz parte da zerg)."""
    async with _db() as db:
        await db.execute(
            '''INSERT OR IGNORE INTO cta_attendance
               (event_id, user_id, user_name, snapshots_present, snapshots_total, percent, base_percent)
               VALUES (?, ?, ?, 0, 0, 100, 100)''',
            (event_id, user_id, user_name),
        )
        await db.execute(
            '''UPDATE cta_attendance SET percent = 100, base_percent = 100, user_name = ?
               WHERE event_id = ? AND user_id = ?''',
            (user_name, event_id, user_id),
        )
        await db.commit()


async def delete_event_completely(event_id: int) -> dict:
    """
    Apaga TODOS os dados de um evento CTA do banco:
      · cta_events (o evento em si)
      · cta_attendance (zerg)
      · cta_function_logs (os "pings" do mass-info)
      · cta_event_nodes (nodes capturados do evento)
      · cta_looter_entries (inscrições/sorteio de looters)
      · regears postados nas threads do evento (por channel_id == *_thread_id)

    Retorna dict com {'existed': bool, ...contagens removidas...}. Os IDs das
    threads/mensagens NÃO são tocados aqui — a limpeza no Discord é feita pelo
    cog ANTES de chamar esta função (ele já tem o dict do evento).
    """
    async with _db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute('SELECT * FROM cta_events WHERE id = ?', (event_id,))
        ev = await cur.fetchone()
        if not ev:
            return {'existed': False}
        ev = dict(ev)

        thread_ids = [tid for tid in (
            ev.get('event_thread_id'),
            ev.get('logger_thread_id'),
            ev.get('regear_thread_id'),
        ) if tid]

        counts = {'existed': True}

        async def _del(table: str, where: str, params: tuple) -> int:
            c = await db.execute(f'DELETE FROM {table} WHERE {where}', params)
            return c.rowcount

        counts['attendance']     = await _del('cta_attendance',     'event_id = ?', (event_id,))
        counts['function_logs']  = await _del('cta_function_logs',  'event_id = ?', (event_id,))
        counts['event_nodes']    = await _del('cta_event_nodes',    'event_id = ?', (event_id,))
        counts['looter_entries'] = await _del('cta_looter_entries', 'event_id = ?', (event_id,))
        counts['bm_entries']     = await _del('cta_bm_entries',     'event_id = ?', (event_id,))
        counts['loggers']        = await _del('cta_event_loggers',  'event_id = ?', (event_id,))
        counts['payouts']        = await _del('cta_payouts',         'event_id = ?', (event_id,))
        counts['punishments']    = await _del('cta_punishments',    'event_id = ?', (event_id,))
        counts['party_leaders']  = await _del('cta_party_leaders',  'event_id = ?', (event_id,))
        counts['assign_notifs']  = await _del('cta_assignment_notifications', 'event_id = ?', (event_id,))

        if thread_ids:
            placeholders = ','.join('?' * len(thread_ids))
            c = await db.execute(
                f'DELETE FROM regears WHERE channel_id IN ({placeholders})',
                thread_ids,
            )
            counts['regears'] = c.rowcount
        else:
            counts['regears'] = 0

        c = await db.execute('DELETE FROM cta_events WHERE id = ?', (event_id,))
        counts['events'] = c.rowcount

        await db.commit()
        return counts


async def get_user_leaderboard_position(user_id: int):
    """
    Retorna (rank, balance, total_earned, attendance_count) do usuário, ou None se
    ele não entra no leaderboard. ROW_NUMBER com a MESMA ordenação da página
    (balance DESC, user_id ASC) → o rank bate exatamente com a posição na lista.
    """
    async with _db() as db:
        cursor = await db.execute(f'''
            WITH ranked AS (
                SELECT user_id, balance, total_earned,
                       ROW_NUMBER() OVER (ORDER BY balance DESC, user_id ASC) AS rnk
                FROM user_balances
                WHERE {_LEADERBOARD_FILTER}
            )
            SELECT
                r.rnk, r.balance, r.total_earned,
                COALESCE((
                    SELECT COUNT(*) FROM cta_attendance a
                    JOIN cta_events e ON e.id = a.event_id
                    WHERE a.user_id = r.user_id
                      AND a.percent >= ?
                      AND e.ended_at IS NOT NULL
                ), 0) AS attendance_count
            FROM ranked r
            WHERE r.user_id = ?
        ''', (ATTENDANCE_THRESHOLD, user_id))
        row = await cursor.fetchone()
    return tuple(row) if row else None


# ==================== TAB AUCTIONS (Ponto 5) ====================

_AUCTION_UPDATE_FIELDS = (
    'initial_value', 'buyout_value', 'status', 'ends_at', 'ping_message_id',
    'winner_id', 'winner_name', 'winning_bid', 'started_by',
)


async def create_auction(
    guild_id: int, channel_id: int, message_id: int,
    poster_id: int, poster_name: str, image_url: str,
) -> int:
    """Cria um leilão em 'setup' e retorna o id."""
    async with _db() as db:
        c = await db.execute(
            '''INSERT INTO tab_auctions
               (guild_id, channel_id, message_id, poster_id, poster_name, image_url)
               VALUES (?, ?, ?, ?, ?, ?)''',
            (guild_id, channel_id, message_id, poster_id, poster_name, image_url),
        )
        await db.commit()
        return c.lastrowid


async def get_auction_by_message_id(message_id: int):
    """Retorna o leilão pela message_id do embed (dict) ou None."""
    async with _db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            'SELECT * FROM tab_auctions WHERE message_id = ?', (message_id,)
        )
        row = await cursor.fetchone()
    return dict(row) if row else None


async def get_auction_by_id(auction_id: int):
    """Retorna o leilão pelo id (dict) ou None."""
    async with _db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            'SELECT * FROM tab_auctions WHERE id = ?', (auction_id,)
        )
        row = await cursor.fetchone()
    return dict(row) if row else None


async def update_auction(auction_id: int, updates: dict):
    """Atualiza campos do leilão. Aceita só chaves em _AUCTION_UPDATE_FIELDS.
    Valores None SÃO aplicados (permite limpar winner/ends_at no reroll)."""
    safe = {k: v for k, v in updates.items() if k in _AUCTION_UPDATE_FIELDS}
    if not safe:
        return
    set_clause = ", ".join(f"{k} = ?" for k in safe.keys())
    values = tuple(safe.values()) + (auction_id,)
    async with _db() as db:
        await db.execute(
            f'UPDATE tab_auctions SET {set_clause} WHERE id = ?', values
        )
        await db.commit()


async def add_bid(auction_id: int, user_id: int, user_name: str, amount: int) -> int:
    """Registra um lance e retorna o id."""
    async with _db() as db:
        c = await db.execute(
            '''INSERT INTO tab_bids (auction_id, user_id, user_name, amount)
               VALUES (?, ?, ?, ?)''',
            (auction_id, user_id, user_name, amount),
        )
        await db.commit()
        return c.lastrowid


async def get_highest_bid(auction_id: int):
    """Retorna o maior lance (dict) ou None. Empate → lance mais antigo vence."""
    async with _db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            '''SELECT * FROM tab_bids WHERE auction_id = ?
               ORDER BY amount DESC, created_at ASC, id ASC LIMIT 1''',
            (auction_id,),
        )
        row = await cursor.fetchone()
    return dict(row) if row else None


async def get_bids_for_auction(auction_id: int) -> list:
    """Lista todos os lances (dicts) ordenados do maior para o menor."""
    async with _db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            '''SELECT * FROM tab_bids WHERE auction_id = ?
               ORDER BY amount DESC, created_at ASC''',
            (auction_id,),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_bidding_auctions() -> list:
    """Leilões com status 'bidding' (usado pelo loop de expiração)."""
    async with _db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM tab_auctions WHERE status = 'bidding'"
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_open_auctions() -> list:
    """Leilões ainda abertos (setup ou bidding) — usado no scan de startup."""
    async with _db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM tab_auctions WHERE status IN ('setup', 'bidding')"
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def clear_bids(auction_id: int) -> int:
    """Apaga todos os lances de um leilão (usado no reroll). Retorna quantos."""
    async with _db() as db:
        c = await db.execute(
            'DELETE FROM tab_bids WHERE auction_id = ?', (auction_id,)
        )
        await db.commit()
        return c.rowcount


# ==================== RECRUTAMENTO (ticket clássico por candidato) ====================

_RECRUIT_TICKET_FIELDS = (
    'thread_id', 'status', 'current_step', 'nick', 'login_image_url',
    'decided_by', 'decided_at', 'reject_reason', 'closed_at', 'delete_at',
)


async def create_recruitment_ticket(guild_id: int, user_id: int, thread_id: int) -> int:
    """Cria um ticket em 'open' e retorna o id."""
    async with _db() as db:
        c = await db.execute(
            '''INSERT INTO recruitment_tickets (guild_id, user_id, thread_id, status)
               VALUES (?, ?, ?, 'open')''',
            (guild_id, user_id, thread_id),
        )
        await db.commit()
        return c.lastrowid


async def get_recruitment_ticket(ticket_id: int):
    async with _db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            'SELECT * FROM recruitment_tickets WHERE id = ?', (ticket_id,))
        row = await cur.fetchone()
    return dict(row) if row else None


async def get_recruitment_ticket_by_thread(thread_id: int):
    """Ticket cuja thread é `thread_id` (o mais recente, se houver mais de um)."""
    async with _db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            'SELECT * FROM recruitment_tickets WHERE thread_id = ? ORDER BY id DESC LIMIT 1',
            (thread_id,))
        row = await cur.fetchone()
    return dict(row) if row else None


async def get_open_recruitment_ticket_for_user(user_id: int):
    """Ticket ainda EM ABERTO do usuário (open/in_progress/review) ou None."""
    async with _db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            '''SELECT * FROM recruitment_tickets
               WHERE user_id = ? AND status IN ('open', 'in_progress', 'awaiting_image', 'review')
               ORDER BY id DESC LIMIT 1''',
            (user_id,))
        row = await cur.fetchone()
    return dict(row) if row else None


async def get_due_recruitment_deletions(now_iso: str) -> list:
    """Tickets cuja thread deve ser apagada agora (delete_at vencido). Usado pelo loop."""
    async with _db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            '''SELECT * FROM recruitment_tickets
               WHERE delete_at IS NOT NULL AND delete_at <= ?''',
            (now_iso,))
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def update_recruitment_ticket(ticket_id: int, updates: dict):
    """Atualiza campos do ticket (whitelist). Valores None SÃO aplicados."""
    safe = {k: v for k, v in updates.items() if k in _RECRUIT_TICKET_FIELDS}
    if not safe:
        return
    set_clause = ", ".join(f"{k} = ?" for k in safe.keys())
    values = tuple(safe.values()) + (ticket_id,)
    async with _db() as db:
        await db.execute(
            f'UPDATE recruitment_tickets SET {set_clause} WHERE id = ?', values)
        await db.commit()
