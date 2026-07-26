import ast
import pathlib
import tomllib

CORE = pathlib.Path("src/crocodile/core")
BANNED = {"solve_captcha_2captcha", "solve_captcha_anticaptcha", "_mine_pow", "_solve_pow"}


def test_core_never_imports_contrib():
    """The dependency points from evasion to core, never back.

    Task 10 found the failure mode concretely: a rate limiter that could see the
    key pool used it to suppress its own backoff.
    """
    offenders = []
    for path in CORE.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "crocodile.contrib"
            ):
                offenders.append(f"{path}:{node.lineno}")
            elif isinstance(node, ast.Import):
                offenders += [
                    f"{path}:{node.lineno}"
                    for a in node.names
                    if a.name.startswith("crocodile.contrib")
                ]
    assert not offenders, f"core must not depend on contrib: {offenders}"


def test_core_contains_no_bot_detection_bypass():
    offenders = [
        f"{path}: {name}"
        for path in CORE.rglob("*.py")
        for name in BANNED
        if name in path.read_text()
    ]
    assert not offenders, f"evasion machinery found in core: {offenders}"


def test_evasion_is_an_optional_extra_and_not_in_the_base_install():
    pyproject = tomllib.loads(pathlib.Path("pyproject.toml").read_text())
    extras = pyproject["project"]["optional-dependencies"]
    assert "evasion" in extras, "evasion must be opt-in, so it needs its own extra"


def test_the_evasion_package_says_what_it_is():
    import crocodile.contrib.evasion as evasion

    doc = (evasion.__doc__ or "").lower()
    assert "opt-in" in doc or "off by default" in doc, (
        "a quarantine nobody can see in the docstring is not a quarantine"
    )
