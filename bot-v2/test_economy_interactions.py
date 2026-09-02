"""Economy interactions must acknowledge Discord before any backend work."""
import ast
from pathlib import Path


ECONOMY = Path(__file__).with_name("cogs") / "economy.py"
COMMANDS = {
    "balance", "pay", "addmoney", "removemoney", "undo", "economystats",
    "_show_guild_bank", "addguildmoney", "removeguildmoney", "leaderboard",
}


def _is_defer(statement: ast.stmt, *, ephemeral: bool) -> bool:
    if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Await):
        return False
    call = statement.value.value
    if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute) or call.func.attr != "defer":
        return False
    response = call.func.value
    is_response_defer = (
        isinstance(response, ast.Attribute)
        and response.attr == "response"
    )
    if not is_response_defer:
        return False
    ephemeral_kw = next((k.value.value for k in call.keywords
                         if k.arg == "ephemeral" and isinstance(k.value, ast.Constant)), False)
    return ephemeral_kw is ephemeral


def _is_access_check(statement: ast.stmt) -> bool:
    if not isinstance(statement, ast.If) or not isinstance(statement.test, ast.UnaryOp):
        return False
    awaited = statement.test.operand
    if not isinstance(awaited, ast.Await) or not isinstance(awaited.value, ast.Call):
        return False
    return isinstance(awaited.value.func, ast.Name) and awaited.value.func.id == "_check_access"


def main() -> None:
    tree = ast.parse(ECONOMY.read_text(encoding="utf-8"))
    methods = {
        node.name: node for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
    }
    missing = COMMANDS - methods.keys()
    assert not missing, f"economy commands missing from test: {sorted(missing)}"
    assert all(
        _is_defer(methods[name].body[0], ephemeral=False)
        and _is_access_check(methods[name].body[1])
        for name in COMMANDS
    )
    assert _is_defer(methods["_goto"].body[0], ephemeral=False)
    assert ast.unparse(methods["balance"].args.args[2].annotation) == "discord.Member | None"
    assert ast.unparse(methods["pay"].args.args[2].annotation) == "discord.Member"
    assert ast.unparse(methods["removemoney"].args.args[2].annotation) == "discord.Member"
    print("economy interaction acknowledgements: ok")


if __name__ == "__main__":
    main()
