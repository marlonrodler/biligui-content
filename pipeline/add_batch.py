"""Junta um lote novo de palavras ao pacote: <base>.json + batches/<lote>.py → <saida>.json.

Mantém tudo o que já existe (palavras, episódios) e confere: rank repetido, palavra repetida e frase repetida.
Uso: python pipeline/add_batch.py --base v2 --batch b04 --out v4
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
from pathlib import Path

PACK = Path(__file__).resolve().parent / "packs" / "ngsl-spoken"


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    base = json.loads((PACK / f"{a.base}.json").read_text(encoding="utf-8"))
    spec = importlib.util.spec_from_file_location(a.batch, PACK / "batches" / f"{a.batch}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]

    words = list(base["words"])
    ranks = {w["rank"] for w in words}
    ids = {slug(w["lemma"]) for w in words}
    sents = {s[0].lower() for w in words for s in w["sentences"]}
    for rank, (head, say, pt, acc, hint, emoji, ss) in sorted(mod.ITEMS.items()):
        assert rank not in ranks, f"rank repetido: {rank} {head}"
        assert slug(head) not in ids, f"palavra repetida: {head}"
        for en, _ in ss:
            assert en.lower() not in sents, f"frase repetida: {en}"
            sents.add(en.lower())
        assert len(ss) == 2, f"precisa de 2 frases: {head}"
        ranks.add(rank)
        ids.add(slug(head))
        words.append({"lemma": head, "rank": rank, "pt": pt, "accepted": acc, "concrete": emoji is not None,
                      "emoji": emoji, "ptbr_hint": hint, "sentences": [list(s) for s in ss], "say": say})
    out = {**base, "version": a.out, "note": f"{base.get('note', '')} + lote {a.batch}".strip(), "words": words}
    (PACK / f"{a.out}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{a.out}: {len(words)} itens (+{len(mod.ITEMS)})")


if __name__ == "__main__":
    main()
