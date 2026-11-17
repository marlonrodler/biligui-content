"""Move a mídia do rascunho para uma pasta única e compartilhada entre versões (ADR-053).

Cada arquivo vai para packs/<pacote>/media/<hash do conteúdo>.<ext>; o manifesto passa a apontar para "../media/...".
Assim cada áudio existe uma vez só no lero-content, por mais versões que existam (o GitHub Pages tem limite de 1 GB),
e o endereço nunca muda (o app pode guardar para sempre).
Uso: python pipeline/publish.py <pasta do rascunho> <pasta do pacote no lero-content>
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path


def main() -> None:
    draft, root = Path(sys.argv[1]), Path(sys.argv[2])
    media = root / "media"
    media.mkdir(parents=True, exist_ok=True)
    man_path = draft / "manifest.json"
    m = json.loads(man_path.read_text(encoding="utf-8"))
    moved = new = 0

    def put(rel: str | None) -> str | None:
        nonlocal moved, new
        if not rel or rel.startswith("../media/"):
            return rel
        src = draft / rel
        if not src.exists():
            print(f"faltando: {rel}", file=sys.stderr)
            return rel
        h = hashlib.sha256(src.read_bytes()).hexdigest()[:24]
        name = f"{h}{src.suffix}"
        if not (media / name).exists():
            shutil.copyfile(src, media / name)
            new += 1
        moved += 1
        return f"../media/{name}"

    for w in m.get("words", []):
        w["audio"] = {k: put(v) for k, v in w.get("audio", {}).items()}
        if "audio_pt" in w:
            w["audio_pt"] = put(w["audio_pt"])
        if w.get("image"):
            w["image"] = put(w["image"])
        for s in w.get("sentences", []):
            if s.get("audio"):
                s["audio"] = put(s["audio"])
    for s in m.get("stories", []):
        s["audio"] = put(s.get("audio"))
    for e in m.get("episodes", []):
        for k in ("image", "audio"):
            if e.get(k):
                e[k] = put(e[k])

    man_path.write_text(json.dumps(m, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")  # compacto: o app baixa menos
    for d in draft.iterdir():  # o rascunho fica só com o manifesto
        if d.is_dir():
            shutil.rmtree(d)
    print(f"mídia: {moved} arquivos no manifesto, {new} novos na pasta media/")


if __name__ == "__main__":
    main()
