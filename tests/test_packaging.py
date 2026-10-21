"""A imagem do serviço leva o que ele lê e deixa os documentos internos fora."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "integrations" / "Dockerfile"
MANIFEST = "quarantine/third-party-marks/manifest.json"


def _patterns() -> list[str]:
    lines = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def _regex(pattern: str) -> re.Pattern[str]:
    # Mesma regra do Docker: ancorado na raiz, "*" e "?" não atravessam "/".
    out = ""
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if pattern.startswith("**", index):
            out += ".*"
            index += 2
            continue
        if char == "*":
            out += "[^/]*"
        elif char == "?":
            out += "[^/]"
        elif char == "[":
            end = pattern.index("]", index)
            out += pattern[index : end + 1]
            index = end
        else:
            out += re.escape(char)
        index += 1
    return re.compile(out)


def _ignored(path: str) -> bool:
    """O último padrão que casa com o caminho ou com uma pasta dele decide."""
    parts = path.split("/")
    candidates = ["/".join(parts[: size + 1]) for size in range(len(parts))]
    ignored = False
    for pattern in _patterns():
        negated = pattern.startswith("!")
        regex = _regex(pattern.removeprefix("!").strip("/"))
        if any(regex.fullmatch(candidate) for candidate in candidates):
            ignored = not negated
    return ignored


def _copy_sources() -> list[str]:
    sources = []
    for line in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("COPY "):
            args = [arg for arg in line.split()[1:] if not arg.startswith("--")]
            sources += args[:-1]
    return sources


def test_every_copy_source_reaches_the_build_context() -> None:
    # "/social": not found no BuildKit quando a pasta inteira era ignorada.
    sources = _copy_sources()

    assert "social" in sources
    assert MANIFEST in sources
    for source in sources:
        assert (ROOT / source).exists(), source
        assert not _ignored(source), source


def test_image_takes_only_the_calendar_and_the_quarantine_list() -> None:
    assert not _ignored("social/calendario-2026-10.json")
    assert not _ignored(MANIFEST)
    for path in (
        "social/auditoria-e-estrategia.md",
        "social/legendas-prontas.md",
        "social/medicao.csv",
        "social/feed-preview.html",
        "social/fila-publicacao.csv",
        "social/publishing-payloads.json",
        "social/calendario-2026-10.csv",
        "social/assets/highlights/colecao.svg",
        "quarantine/third-party-marks/README.md",
        "quarantine/third-party-marks/landing/social/posts/navy-gold.jpg",
        "docs/HANDOFF.md",
        "tests/test_packaging.py",
    ):
        assert _ignored(path), path
    shipped = [
        path.relative_to(ROOT).as_posix()
        for folder in ("social", "quarantine")
        for path in (ROOT / folder).rglob("*")
        if path.is_file() and not _ignored(path.relative_to(ROOT).as_posix())
    ]
    assert MANIFEST in shipped
    assert all(
        path == MANIFEST or re.fullmatch(r"social/calendario-[^/]*\.json", path)
        for path in shipped
    ), shipped
