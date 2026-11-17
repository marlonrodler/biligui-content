"""Apaga da pasta media/ os arquivos que nenhuma versão presente (vN/ e draft/) usa mais (ADR-053).

Ex.: a voz antiga do português (trocada na ADR-055). Rodar ao promover, depois de apagar as versões antigas.
Uso: python pipeline/prune_media.py <pasta do pacote no lero-content> [--dry]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def used(man: Path) -> set[str]:
    m = json.loads(man.read_text(encoding="utf-8"))
    out: set[str] = set()

    def add(p: str | None) -> None:
        if p and p.startswith("../media/"):
            out.add(p.split("/")[-1])
    for w in m.get("words", []):
        for v in w.get("audio", {}).values():
            add(v)
        add(w.get("audio_pt"))
        add(w.get("image"))
        for s in w.get("sentences", []):
            add(s.get("audio"))
    for s in m.get("stories", []):
        add(s.get("audio"))
    for e in m.get("episodes", []):
        add(e.get("image"))
        add(e.get("audio"))
    return out


def main() -> None:
    root = Path(sys.argv[1])
    dry = "--dry" in sys.argv
    keep: set[str] = set()
    for d in root.iterdir():
        if d.is_dir() and (d.name == "draft" or d.name.startswith("v")) and (d / "manifest.json").exists():
            keep |= used(d / "manifest.json")
    files = list((root / "media").iterdir())
    gone = [f for f in files if f.name not in keep]
    size = sum(f.stat().st_size for f in gone)
    if not dry:
        for f in gone:
            f.unlink()
    print(f"media: {len(files)} arquivos, {len(gone)} sem uso ({size / 1e6:.1f} MB){' (simulação)' if dry else ' apagados'}")


if __name__ == "__main__":
    main()
