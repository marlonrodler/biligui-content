"""Junta um nível gerado (levels/<B1|B2|C1|C2>.json, ADR-052) ao pacote: <base>.json → <saida>.json.

Mesmas conferências do add_batch.py: posição repetida, palavra repetida, frase repetida (frase repetida sai; a palavra
fica com a outra frase; sem frase nenhuma, a palavra sai).
Uso: python pipeline/add_level.py --base v7 --level B1 --out v9
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

PACK = Path(__file__).resolve().parent / "packs" / "ngsl-spoken"


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--level", required=True, action="append")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    base = json.loads((PACK / f"{a.base}.json").read_text(encoding="utf-8"))
    words = list(base["words"])
    ranks = {w["rank"] for w in words}
    ids = {slug(w["lemma"]) for w in words}
    sents = {s[0].lower() for w in words for s in w["sentences"]}
    added = dropped = 0
    for lvl in a.level:
        for it in json.loads((PACK / "levels" / f"{lvl}.json").read_text(encoding="utf-8")):
            if it["rank"] in ranks or slug(it["lemma"]) in ids:
                dropped += 1
                continue
            ss = [s for s in it["sentences"] if s[0].lower() not in sents]
            if not ss:
                dropped += 1
                continue
            for s in ss:
                sents.add(s[0].lower())
            ranks.add(it["rank"])
            ids.add(slug(it["lemma"]))
            words.append({**it, "sentences": ss})
            added += 1
    words.sort(key=lambda w: w["rank"])
    out = {**base, "version": a.out, "note": f"{base.get('note', '')} + níveis {', '.join(a.level)}".strip(), "words": words}
    (PACK / f"{a.out}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{a.out}: {len(words)} itens (+{added}, {dropped} fora por repetição)")


if __name__ == "__main__":
    main()
