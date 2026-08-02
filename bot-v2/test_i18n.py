import ast
from pathlib import Path

from i18n import CMD_I18N, T


trees = [
    ast.parse(path.read_text(encoding="utf-8"))
    for folder in (Path("."), Path("cogs"))
    for path in folder.glob("*.py")
    if path.name != Path(__file__).name
]


def constant_keys(function: str) -> set[str]:
    return {
        node.args[1].value
        for tree in trees
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == function
        and len(node.args) > 1
        and isinstance(node.args[1], ast.Constant)
        and isinstance(node.args[1].value, str)
    }


if __name__ == "__main__":
    dynamic_runtime = {
        "signup_success_self", "signup_success_hybrid",
        "signup_roles_dm_defined", "signup_roles_dm_changed", "signup_roles_dm_released",
    }
    missing_runtime = sorted((constant_keys("t") | dynamic_runtime) - T.keys())
    missing_localized = sorted(constant_keys("loc") - CMD_I18N.keys())
    assert not missing_runtime, f"runtime translations missing: {missing_runtime}"
    assert not missing_localized, f"command translations missing: {missing_localized}"
    print("bot translations: ok")
