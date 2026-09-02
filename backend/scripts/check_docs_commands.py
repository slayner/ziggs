"""Valida que o catálogo público de docs só anuncia slash commands reais.

Não tenta gerar texto: decorators não explicam botões, threads, permissões ou
pré-requisitos. O check só impede que um rename/removal no bot deixe um
comando documentado apontando para algo inexistente.
"""
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DOCS_CONTENT = ROOT / "frontend" / "src" / "docs-content.ts"
BOT_COGS = ROOT / "bot-v2" / "cogs"


def bot_commands() -> set[str]:
    commands: set[str] = set()
    event_group: set[str] = set()
    for path in BOT_COGS.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        commands.update(re.findall(r'@app_commands\.command\(name=["\']([^"\']+)', source))
        event_group.update(re.findall(r'@group\.command\(name=["\']([^"\']+)', source))
    commands.update(f"event {name}" for name in event_group)
    return commands


def documented_commands() -> list[str]:
    source = DOCS_CONTENT.read_text(encoding="utf-8")
    return re.findall(r'^\s*command:\s*["\'](/[^"\']+)', source, re.MULTILINE)


def canonical(command: str) -> str:
    parts = command.removeprefix("/").split()
    if parts[0] == "event":
        return "event " + parts[1]
    # /register register:Character is one app command with an option named
    # register; the catalog's command field may include that option label.
    return parts[0]


def main() -> int:
    actual = bot_commands()
    documented = documented_commands()
    missing = sorted({canonical(command) for command in documented} - actual)
    if missing:
        print("Documented commands missing from bot-v2:")
        print("\n".join(f"- /{command}" for command in missing))
        return 1
    print(f"ok: {len(documented)} documented command entries match bot-v2")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
