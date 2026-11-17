"""Histórias do podcast (ADR-049): áudio único por história (narrador + vozes dos personagens), tempos de cada
linha e, para cada palavra do texto, a palavra do pacote a que ela pertence (para o app saber o que a pessoa já estudou).
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
from pathlib import Path

GAP_SAME, GAP_TURN = 0.35, 0.7  # pausa entre frases do mesmo personagem / quando muda quem fala

IRREGULAR = {
    "be": "am is are was were been being 'm 's 're", "have": "has had having 've", "do": "does did done doing",
    "this": "these", "that": "those", "lead": "led", "break": "broke broken", "forget": "forgot forgotten", "begin": "began begun", "bring": "brought",
    "buy": "bought", "catch": "caught", "choose": "chose chosen", "drive": "drove driven", "eat": "ate eaten", "fall": "fell fallen",
    "feel": "felt", "fight": "fought", "find": "found", "fly": "flew flown", "grow": "grew grown", "hide": "hid hidden",
    "hold": "held", "keep": "kept", "leave": "left", "lend": "lent", "lose": "lost", "mean": "meant", "meet": "met",
    "pay": "paid", "ride": "rode ridden", "rise": "rose risen", "run": "ran", "sell": "sold", "send": "sent", "shake": "shook shaken",
    "shoot": "shot", "sing": "sang sung", "sit": "sat", "sleep": "slept", "speak": "spoke spoken", "spend": "spent",
    "stand": "stood", "steal": "stole stolen", "swim": "swam swum", "teach": "taught", "tear": "tore torn", "throw": "threw thrown",
    "understand": "understood", "win": "won", "write": "wrote written", "draw": "drew drawn", "hang": "hung", "seek": "sought",
    "go": "goes went gone going", "wake": "woke woken", "freeze": "froze frozen", "good": "better best", "bad": "worse worst", "get": "gets got gotten getting", "say": "says said", "make": "makes made making",
    "know": "knows knew known", "think": "thinks thought", "see": "sees saw seen", "come": "comes came coming",
    "take": "takes took taken taking", "give": "gives gave given", "tell": "tells told", "find": "finds found",
    "feel": "feels felt", "leave": "leaves left", "buy": "buys bought", "bring": "brings brought", "sit": "sits sat sitting",
    "run": "runs ran running", "pay": "pays paid", "meet": "meets met", "write": "writes wrote written", "eat": "eats ate eaten",
    "drink": "drinks drank", "sleep": "sleeps slept", "speak": "speaks spoke spoken", "lose": "loses lost", "win": "wins won",
    "begin": "begins began begun", "sell": "sells sold", "hold": "holds held", "stand": "stands stood", "fall": "falls fell fallen",
    "grow": "grows grew grown", "throw": "throws threw thrown", "catch": "catches caught", "teach": "teaches taught",
    "choose": "chooses chose chosen", "drive": "drives drove driven", "send": "sends sent", "spend": "spends spent",
    "build": "builds built", "keep": "keeps kept", "hear": "hears heard", "understand": "understands understood",
    "become": "becomes became", "wear": "wears wore worn", "will": "'ll", "would": "'d", "not": "n't", "can": "can't",
    "I": "me my", "you": "your yours", "he": "him his", "she": "her hers", "we": "us our", "they": "them their",
    "good": "better best", "bad": "worse worst", "kid": "kids", "person": "people", "child": "children", "man": "men",
    "woman": "women", "let": "let's", "a": "an", "laugh": "laughs laughed laughing", "cook": "cooks cooked cooking",
}


def forms(word: str) -> set[str]:
    w = word.lower()
    out = {w, w + "s", w + "es", w + "ed", w + "ing", w + "ly"}
    if w.endswith("e") and len(w) >= 3:
        out.add(w + "d")  # like → liked (mas "be" + d não é "bed")
    if len(w) >= 3:
        out |= {w + "er", w + "est"}
    if w.endswith("e"):
        out.add(w[:-1] + "ing")
        if len(w) >= 4:
            out |= {w + "r", w + "st"}  # late → later, latest (não "be" → "best")
    if w.endswith("y"):
        out |= {w[:-1] + "ies", w[:-1] + "ied", w[:-1] + "ier", w[:-1] + "iest"}  # early → earlier
    if len(w) > 2 and w[-1] not in "aeiouwy" and w[-2] in "aeiou" and w[-3] not in "aeiou":
        out |= {w + w[-1] + "ed", w + w[-1] + "ing", w + w[-1] + "er", w + w[-1] + "est"}  # big → biggest
    out |= set(IRREGULAR.get(word, IRREGULAR.get(w, "")).lower().split())
    return out


def tokenize(text: str) -> list[str]:
    """"I'm" → ["i", "'m"], "don't" → ["do", "n't"], "can't" → ["can", "n't"]."""
    toks: list[str] = []
    for t in re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", text.replace("’", "'")):
        lo = t.lower()
        if lo == "can't":
            toks += ["can", "n't"]
        elif lo.endswith("n't"):
            toks += [lo[:-3], "n't"]
        elif "'" in lo and lo != "let's":
            a, b = lo.split("'", 1)
            toks += [a, "'" + b]
        else:
            toks.append(lo)
    return toks


def word_index(words: list[dict]) -> dict[str, str]:
    """forma escrita → id da palavra do pacote (a de menor posição na lista ganha)."""
    idx: dict[str, str] = {}
    for w in sorted(words, key=lambda x: x["rank"]):
        head = w.get("headword") or w["lemma"]
        if " " in head:
            continue
        for f in forms(head):
            idx.setdefault(f, w["id"])
    return idx


def load_stories(folder: Path) -> tuple[dict, list[dict]]:
    cast: dict = {}
    stories: list[dict] = []
    for f in sorted(folder.glob("*.py")):
        spec = importlib.util.spec_from_file_location(f.stem, f)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        cast.update(mod.CAST)
        stories += mod.STORIES
    return cast, stories


WORD_RE = re.compile(r"[A-Za-z0-9]+(?:'[A-Za-z]+)?")  # o app usa a mesma regra para separar as palavras


def align(en: str, marks: list[list], start: float) -> list:
    """Para cada palavra do texto (WORD_RE), o [início, fim] em segundos na história, vindo dos tempos do Kokoro.
    O Kokoro às vezes junta ou separa palavras ("don't", números); casa em ordem e, se não achar, deixa null."""
    words = WORD_RE.findall(en.replace("\u2019", "'"))
    norm = lambda x: re.sub(r"[^a-z0-9']", "", x.lower().replace("\u2019", "'"))
    out: list = [None] * len(words)
    j = 0
    for i, w in enumerate(words):
        for k in range(j, min(j + 3, len(marks))):
            m = norm(marks[k][0])
            if m == norm(w) or (m and (norm(w).startswith(m) or m.startswith(norm(w)))):
                end, acc, j = marks[k][2], m, k + 1
                while j < len(marks) and acc != norm(w) and norm(w).startswith(acc + norm(marks[j][0])):
                    acc += norm(marks[j][0]); end = marks[j][2]; j += 1  # "does" + "n't" = "doesn't"
                out[i] = [round(start + marks[k][1], 2), round(start + end, 2)]
                break
    # sem tempo: estima entre os vizinhos que têm
    for i in range(len(out)):
        if out[i] is None:
            prev = next((out[x][1] for x in range(i - 1, -1, -1) if out[x]), start)
            nxt = next((out[x][0] for x in range(i + 1, len(out)) if out[x]), None)
            out[i] = [round(prev, 2), round(nxt if nxt is not None else prev + 0.3, 2)]
    return out


def build_stories(words: list[dict], tts, draft: Path, tmp: Path, folder: Path) -> list[dict]:
    import numpy as np
    import soundfile as sf
    from build import to_m4a  # noqa: E402 (mesmo módulo de conversão)

    cast, stories = load_stories(folder)
    # só entram histórias de níveis que o pacote já tem (ex.: as do C1 esperam as palavras do C1)
    ranges = {"A1": (1, 350), "A2": (351, 721), "B1": (722, 2000), "B2": (2001, 3500), "C1": (3501, 5000), "C2": (5001, 9999)}
    ranks = [w["rank"] for w in words]
    stories = [s for s in stories if any(ranges[s["level"]][0] <= r <= ranges[s["level"]][1] for r in ranks)]
    idx = word_index(words)
    names = {n.lower() for n in cast if n != "N"}
    sdir = draft / "stories"
    sdir.mkdir(parents=True, exist_ok=True)
    out = []
    for s in stories:
        pcm: list = []
        t, prev = 0.0, None
        lines = []
        unknown: dict[str, int] = {}
        counts: dict[str, int] = {}
        for i, (who, en, pt) in enumerate(s["lines"]):
            toks = []
            for tok in tokenize(en):
                if tok in names:
                    toks.append([tok, None])
                    continue
                wid = idx.get(tok) or idx.get(tok.lstrip("'"))
                toks.append([tok, wid])
                if wid:
                    counts[wid] = counts.get(wid, 0) + 1
                else:
                    unknown[tok] = unknown.get(tok, 0) + 1
            line = {"who": who, "en": en, "pt": pt, "tokens": toks}
            if tts:
                if prev is not None:
                    gap = GAP_SAME if prev == who else GAP_TURN
                    pcm.append(np.zeros(int(gap * 24000), dtype=np.float32))
                    t += gap
                m4a = tmp / f"{s['id']}-{i}.m4a"
                _, marks = tts.timed(en, cast[who], m4a, tmp)
                wav = tmp / f"{s['id']}-{i}.wav"
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(m4a), "-ac", "1", "-ar", "24000", str(wav)], check=True)
                a, _ = sf.read(wav, dtype="float32")
                line["start"] = round(t, 2)
                line["times"] = align(en, marks, t)
                t += len(a) / 24000
                line["end"] = round(t, 2)
                pcm.append(a)
            lines.append(line)
            prev = who
        entry = {"id": s["id"], "level": s["level"], "title": s["title"], "title_pt": s["title_pt"], "lines": lines,
                 "words": counts, "outside": sorted(unknown), "audio": None, "duration": None}
        if tts:
            raw = tmp / f"{s['id']}.wav"
            sf.write(raw, np.concatenate(pcm), 24000)
            entry["duration"] = round(to_m4a(raw, sdir / f"{s['id']}.m4a"), 2)
            entry["audio"] = f"stories/{s['id']}.m4a"
        out.append(entry)
        total = sum(counts.values()) + sum(unknown.values())
        print(f"história {s['id']}: {len(lines)} falas, {sum(counts.values())}/{total} palavras do pacote; fora: {', '.join(sorted(unknown)) or '-'}")
    return out
