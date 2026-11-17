"""Enche o cache de áudio com o que já está publicado no lero-content (ADR-047).

O mesmo texto na mesma voz dá o mesmo áudio; então, antes de gerar, copiamos os áudios do pacote publicado
para o cache com a chave certa. Assim o Kokoro só roda para palavras e frases novas.
Uso: python pipeline/seed_cache.py <pasta do lero-content> [pacote]
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build import PACK_VOICES, PT_VOICE, Tts, pt_spoken  # noqa: E402


def key(text: str, voice: str) -> str:
    return Tts.key(text, voice)


def main() -> None:
    content = Path(sys.argv[1])
    pack = sys.argv[2] if len(sys.argv) > 2 else "ngsl-spoken"
    cache = Path(os.environ.get("LERO_CACHE", Path(__file__).resolve().parent / ".cache")) / "tts"
    cache.mkdir(parents=True, exist_ok=True)
    root = content / "packs" / pack
    versions = sorted((d for d in root.iterdir() if d.is_dir() and d.name.startswith("v")), key=lambda d: int(d.name[1:]) if d.name[1:].isdigit() else 0)
    n = 0
    for d in [*versions[-2:], root / "draft"]:  # as duas últimas versões e o rascunho bastam
        man = d / "manifest.json"
        if not man.exists():
            continue
        m = json.loads(man.read_text(encoding="utf-8"))
        for w in m["words"]:
            pairs = [(w["lemma"], PACK_VOICES[0], w["audio"].get(PACK_VOICES[0]))]
            # o áudio em português só vale se foi gerado com a mesma voz (a voz mudou na ADR-055)
            if w.get("audio_pt") and m.get("voice_pt") == PT_VOICE:
                pairs.append((pt_spoken(w["translation"]["pt"]), PT_VOICE, w["audio_pt"]))
            pairs += [(s["en"], PACK_VOICES[0], s.get("audio")) for s in w["sentences"]]
            for text, voice, rel in pairs:
                if not rel:
                    continue
                src, dst = d / rel, cache / f"{key(text, voice)}.m4a"
                if src.exists() and not dst.exists():
                    shutil.copyfile(src, dst)
                    n += 1
    print(f"cache: {n} áudios copiados do que já estava publicado")


if __name__ == "__main__":
    main()
