"""Gera as palavras dos níveis B1–C2 a partir de fontes abertas (ADR-052). Roda no GitHub Actions (precisa de internet).

Fontes (todas gratuitas e abertas):
  - NGSL 1.2 (Browne, Culligan & Phillips, CC BY-SA 4.0): ordem das palavras gerais mais úteis.
  - NAWL 1.2 (Browne, Culligan & Phillips, CC BY-SA 4.0): palavras de estudo e trabalho.
  - wordfreq (Robyn Speer, dados CC BY-SA 4.0): frequência para ordenar o resto, até a posição 9000.
  - Wiktionary via kaikki.org (CC BY-SA 4.0): classe da palavra, formas e tradução para o português.
  - Tatoeba (CC BY 2.0 FR): frases reais em inglês com tradução para o português feita por pessoas.
  - Fluent Emoji (MIT): imagem só quando o nome do emoji é exatamente a palavra.

Saída: pipeline/packs/ngsl-spoken/levels/<B1|B2|C1|C2>.json, no mesmo formato dos lotes (add_batch.py).
Uso: python pipeline/levels.py [--work pasta-de-downloads]
"""
from __future__ import annotations

import argparse
import bz2
import csv
import io
import json
import re
import subprocess
import sys
import tarfile
import urllib.request
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACK = HERE / "packs" / "ngsl-spoken"
LEVELS = [("B1", 722, 2000), ("B2", 2001, 3500), ("C1", 3501, 5000), ("C2", 5001, 9000)]
LAST = 9000

URLS = {
    "ngsl": "https://www.newgeneralservicelist.com/s/NGSL_12_stats.csv",
    "ngsl_fam": "https://www.newgeneralservicelist.com/s/NGSL_12_lemmatized_for_research.csv",
    "nawl_fam": "https://www.newgeneralservicelist.com/s/NAWL_12_lemmatized_for_research.csv",
    "kaikki": "https://kaikki.org/dictionary/English/kaikki.org-dictionary-English.jsonl",
    "eng": "https://downloads.tatoeba.org/exports/per_language/eng/eng_sentences.tsv.bz2",
    "por": "https://downloads.tatoeba.org/exports/per_language/por/por_sentences.tsv.bz2",
    "links": "https://downloads.tatoeba.org/exports/links.tar.bz2",
}
BAD_POS = {"name", "abbrev", "symbol", "character", "prefix", "suffix", "affix", "infix", "interfix", "punct", "phrase", "proverb", "prep_phrase", "romanization", "det"}
BAD_TAGS = {"vulgar", "offensive", "slur", "derogatory", "ethnic", "obsolete", "archaic", "dated", "rare", "nonstandard", "misspelling", "Internet", "slang", "dialectal"}
PT_PT = {"Portugal", "European-Portuguese", "European"}
WORD = re.compile(r"[a-z]+(?:'[a-z]+)?")
# Palavras centrais (NGSL) que o Wiktionary não traduz bem: tradução escrita por nós, como nos lotes do A1/A2.
# Ainda precisam de uma frase do Tatoeba que mostre a tradução.
MANUAL = {
    "however": "porém / no entanto", "upon": "sobre / ao", "unclear": "pouco claro", "favorite": "favorito", "labor": "trabalho",
    "photo": "foto", "neighbor": "vizinho", "expand": "expandir / ampliar", "generate": "gerar", "deny": "negar",
    "entirely": "inteiramente / totalmente", "strongly": "fortemente", "largely": "em grande parte", "anywhere": "em qualquer lugar",
    "unlike": "diferente de", "appoint": "nomear", "elsewhere": "em outro lugar", "unusual": "incomum / estranho",
    "cope": "lidar", "originally": "originalmente", "primarily": "principalmente", "retain": "manter / reter",
    "ultimately": "no fim / afinal", "undertake": "assumir / realizar", "owe": "dever", "grand": "grandioso",
    "anymore": "não… mais", "beside": "ao lado de", "heavily": "muito / pesadamente", "spare": "de reserva / poupar",
    "mom": "mãe", "web": "web / internet", "exam": "prova / exame", "similarly": "da mesma forma", "briefly": "brevemente",
    "keen": "interessado / animado", "opponent": "adversário", "rid": "livrar-se", "greatly": "muito", "fancy": "chique / querer",
    "whilst": "enquanto", "regardless": "independentemente", "maker": "fabricante", "humor": "humor", "dialog": "diálogo",
    "characteristic": "característica", "cite": "citar", "adviser": "conselheiro", "meter": "metro / medidor", "bin": "lixeira",
    "indication": "indicação / sinal", "constraint": "restrição", "ad": "anúncio", "wed": "casar", "forth": "adiante",
    "underlie": "estar por trás de", "characterize": "caracterizar",
}
DEBUG = {"however", "busy", "hungry", "energy", "client", "software", "code", "airport", "restaurant", "healthy", "shower", "later"}


def fetch(url: str, dest: Path) -> Path:
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    print(f"baixando {url}", flush=True)
    subprocess.run(["curl", "-fsSL", "--retry", "3", "-A", "Mozilla/5.0 (lero pipeline)", "-o", str(dest), url], check=True)
    return dest


# ---------- listas ----------
def read_csv_words(path: Path) -> list[list[str]]:
    text = path.read_bytes().decode("utf-8-sig", errors="replace")
    return [r for r in csv.reader(io.StringIO(text)) if r and r[0].strip()]


def ngsl_order(path: Path) -> list[str]:
    rows = read_csv_words(path)
    print("NGSL cabeçalho:", rows[0][:6])
    out = []
    for r in rows[1:]:
        w = r[0].strip().lower()
        if WORD.fullmatch(w):
            out.append(w)
    return out


def families(path: Path) -> dict[str, str]:
    """forma → palavra-base (arquivo 'lemmatized for research': base na 1ª coluna, formas nas outras)."""
    fam: dict[str, str] = {}
    rows = read_csv_words(path)
    print(f"famílias {path.name}: cabeçalho {rows[0][:4]}")
    for r in rows[1:]:
        head = r[0].strip().lower()
        if not WORD.fullmatch(head):
            continue
        for f in r:
            f = f.strip().lower()
            if WORD.fullmatch(f):
                fam.setdefault(f, head)
    return fam


# ---------- Wiktionary ----------
def load_kaikki(path: Path, wanted: set[str]) -> dict[str, list[dict]]:
    """Só as entradas das palavras que interessam: {palavra: [entradas na ordem do Wiktionary]}."""
    try:
        import orjson
        loads = orjson.loads
    except ImportError:
        loads = json.loads
    out: dict[str, list[dict]] = defaultdict(list)
    with path.open("rb") as f:
        for line in f:
            # o 1º "word" da linha pode ser de um objeto de dentro (derivadas, traduções): olha todos, depois confere o principal
            if not any(m.decode("utf-8", "ignore").lower() in wanted for m in re.findall(rb'"word": ?"([^"\\]+)"', line)):
                continue
            e = loads(line)
            key = (e.get("word") or "").lower()
            if key not in wanted or e.get("lang_code", "en") != "en":
                continue
            out[key].append(e)
    return out


def form_base(entries: list[dict]) -> str | None:
    """Se algum sentido diz "forma de X" (plural, gerúndio, passado...), devolve X."""
    for e in entries:
        for sn in e.get("senses", []):
            for fo in sn.get("form_of", []) or []:
                b = (fo.get("word") or "").lower()
                if WORD.fullmatch(b) and b != e["word"].lower():
                    return b
    return None


NEVER_TAGS = {"vulgar", "offensive", "slur", "derogatory", "ethnic", "sexual", "pejorative"}


def unsafe(entries: list[dict], core: bool = False) -> bool:
    """Algum sentido é vulgar, ofensivo ou sexual: a palavra não entra. Para as palavras centrais (NGSL, ex.: "ball"),
    só o sentido principal conta; a frase escolhida nunca é a do sentido vulgar (EXPLICIT)."""
    for e in (entries[:1] if core else entries):
        for sn in (e.get("senses", [])[:1] if core else e.get("senses", [])):
            if set(sn.get("tags", [])) & NEVER_TAGS:
                return True
            if not core and re.search(r"\b(vulgar|offensive|slur|derogatory|genitals?|vagina|penis)\b", " ".join(sn.get("glosses", [])).lower()):
                return True
    return False


def is_lemma(entries: list[dict]) -> bool:
    """Tem pelo menos um sentido próprio (não é só "plural de", "passado de"...) e não é nome próprio/palavrão."""
    for e in entries:
        if e.get("pos") in BAD_POS or e["word"][:1].isupper():
            continue
        for s in e.get("senses", []):
            tags = set(s.get("tags", []))
            if s.get("form_of") or s.get("alt_of") or "form-of" in tags or "alt-of" in tags:
                continue
            if tags & BAD_TAGS:
                continue
            return True
    return False


def forms_of(entries: list[dict]) -> set[str]:
    out = set()
    for e in entries:
        for f in e.get("forms", []):
            w = (f.get("form") or "").lower()
            if WORD.fullmatch(w) and not set(f.get("tags", [])) & {"obsolete", "archaic", "table-tags", "inflection-template"}:
                out.add(w)
    return out


def pt_meaning(entries: list[dict]) -> str | None:
    """1 ou 2 traduções para o português, dos sentidos mais comuns (os primeiros no Wiktionary), evitando as de Portugal."""
    seen: list[str] = []
    for e in entries:
        if e.get("pos") in BAD_POS or e["word"][:1].isupper():
            continue
        trs = list(e.get("translations", []))
        for s in e.get("senses", []):
            trs += s.get("translations", [])
        senses_used: set[str] = set()
        for t in trs:
            if t.get("lang_code") != "pt" and t.get("code") != "pt" and t.get("lang") != "Portuguese":
                continue
            word = (t.get("word") or "").strip()
            if not word or set(t.get("tags", [])) & PT_PT or len(word) > 28 or re.search(r"[\[\]{}=|]", word):
                continue
            sense = t.get("sense") or ""
            if word.lower() in (x.lower() for x in seen) or sense in senses_used:
                continue
            seen.append(word)
            senses_used.add(sense)
            if len(seen) == 2:
                return " / ".join(seen)
        if seen:
            return " / ".join(seen)  # a 2ª tradução só vale da mesma classe de palavra
    return None


def pt_candidates(entries: list[dict]) -> list[tuple[str, str]]:
    """Todas as traduções para o português (palavra, sentido), na ordem do Wiktionary, sem as de Portugal."""
    out: list[tuple[str, str]] = []
    for e in entries:
        if e.get("pos") in BAD_POS or e["word"][:1].isupper():
            continue
        trs = list(e.get("translations", []))
        for sn in e.get("senses", []):
            trs += sn.get("translations", [])
        for t in trs:
            if t.get("lang_code") != "pt" and t.get("code") != "pt" and t.get("lang") != "Portuguese":
                continue
            word = (t.get("word") or "").strip()
            if not word or set(t.get("tags", [])) & PT_PT or len(word) > 28 or re.search(r"[\[\]{}=|/()]", word):
                continue
            if all(word.lower() != w.lower() for w, _ in out):
                out.append((word, f"{e.get('pos')}:{t.get('sense') or ''}"))
    return out


def fold(x: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", x.lower()) if unicodedata.category(c) != "Mn")


def stem(pt: str) -> str:
    """Raiz para achar a tradução nas frases: "lavar" acha "lavo", "lavou"; "difícil" acha "difíceis".
    Locução ("dedo do pé") vira as raízes das palavras com mais de 2 letras, separadas por espaço."""
    def one(w: str) -> str:
        if len(w) > 4 and w[-2:] in ("ar", "er", "ir"):
            return w[:-2]
        return w[:-1] if len(w) > 5 else w
    toks = re.findall(r"[a-z]+", fold(pt))
    if len(toks) > 1:
        return " ".join(one(t) for t in toks if len(t) > 2)
    return one(toks[0]) if toks else ""


def tok_match(st: str, toks: list[str]) -> bool:
    if len(st) < 4:  # raiz curta ("pós", "dor") só vale a palavra inteira
        return any(t == st or t == st + "s" or t == st + "es" for t in toks)
    return any(t.startswith(st) for t in toks)


def in_pt(st: str, pt_sentence: str) -> bool:
    toks = re.findall(r"[a-z]+", fold(pt_sentence))
    return bool(st) and all(tok_match(x, toks) for x in st.split())


class PtFreq:
    """Em quantas frases em português cada palavra aparece (para não premiar palavras comuns como "fazer")."""
    def __init__(self, sentences: list[str]) -> None:
        import bisect
        from collections import Counter
        c: Counter = Counter()
        for p in sentences:
            c.update(set(re.findall(r"[a-z]+", fold(p))))
        self.n = max(1, len(sentences))
        self.keys = sorted(c)
        self.cum = [0]
        for k in self.keys:
            self.cum.append(self.cum[-1] + c[k])
        self.c, self.bisect = c, bisect

    def ratio(self, st: str) -> float:
        def one(x: str) -> int:
            if len(x) < 4:
                return self.c.get(x, 0) + self.c.get(x + "s", 0)
            lo = self.bisect.bisect_left(self.keys, x)
            hi = self.bisect.bisect_left(self.keys, x + "\uffff")
            return self.cum[hi] - self.cum[lo]
        parts = st.split()
        return min((one(x) for x in parts), default=0) / self.n if parts else 0.0


def choose_meaning(cands: list[tuple[str, str]], pts: list[str], freq: "PtFreq | None" = None, lemma: str = "") -> tuple[str | None, list[str]]:
    """Tradução que as pessoas usaram nas frases do Tatoeba com essa palavra, acima do normal do português.
    A própria palavra inglesa entra como candidata (empréstimos: "drone", "online"). Sem pistas: 1ª do Wiktionary."""
    if not cands:
        return None, []
    pool = list(cands[:20])
    if len(lemma) >= 4 and all(fold(w) != lemma for w, _ in pool):
        pool.append((lemma, "loan"))
    scored = []
    for i, (w, sense) in enumerate(pool):
        st = stem(w)
        if not st:
            continue
        if sense == "loan":  # empréstimo: a palavra inglesa inteira ("drone"), não começo de outra ("tart" ≠ "tarte")
            n = sum(1 for p in pts if re.search(rf"\b{re.escape(fold(w))}s?\b", fold(p)))
        else:
            n = sum(1 for p in pts if in_pt(st, p))
        base = freq.ratio(st) if freq else 0.0
        lift = n - len(pts) * base
        ok = n >= 1 and (not pts or n / len(pts) >= 3 * base)
        scored.append((-(lift if ok else -1e9), i, w, sense, st, n))
    if not scored:
        return None, []
    scored.sort()
    best = scored[0]
    if best[0] >= 1e8 or best[5] == 0:  # nenhuma frase traduzida por pessoas mostra a tradução: sem prova, a palavra não entra
        return None, []
    # mais específica: "exercício" ganha de "exercer" quando aparece em boa parte das mesmas frases
    for c in scored:
        if c is not best and c[0] < 1e8 and c[4].startswith(best[4]) and len(c[4]) >= len(best[4]) + 2 and c[5] >= 0.6 * best[5]:
            best = c
            break
    picked = [best]
    for c in scored:
        if len(picked) == 2:
            break
        if c is best or c[0] >= 0 or c[5] < max(2, best[5] * 0.35) or c[3] == "loan" or c[5] / max(1, len(pts)) < 6 * (freq.ratio(c[4]) if freq else 0):
            continue
        if c[4][:4] == best[4][:4] or c[3] == best[3]:
            continue
        picked.append(c)
    names = []
    for p in picked:
        w = p[2]
        for alt in (w[:-1] if w.endswith("a") else None, w[:-1] + "o" if w.endswith("a") else None, w[:-1] if w.endswith("s") else None):
            if alt and any(fold(alt) == fold(c[0]) for c in cands):
                w = next(c[0] for c in cands if fold(c[0]) == fold(alt))
                break
        names.append(w)
    return " / ".join(names), [p[4] for p in picked]


# ---------- Tatoeba ----------
def load_tatoeba(work: Path) -> list[tuple[str, str, int, int]]:
    """Pares (inglês, português, id_en, id_pt). Português do Brasil quando dá para saber."""
    def sents(p: Path) -> dict[int, str]:
        d = {}
        with bz2.open(p, "rt", encoding="utf-8") as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= 3:
                    d[int(parts[0])] = parts[2]
        return d
    eng = sents(fetch(URLS["eng"], work / "eng.tsv.bz2"))
    por = sents(fetch(URLS["por"], work / "por.tsv.bz2"))
    pairs: dict[int, list[int]] = defaultdict(list)
    with tarfile.open(fetch(URLS["links"], work / "links.tar.bz2"), "r:bz2") as tar:
        for m in tar:
            if not m.isfile():
                continue
            f = tar.extractfile(m)
            for line in io.TextIOWrapper(f, encoding="utf-8"):  # type: ignore[arg-type]
                a, _, b = line.partition("\t")
                a_i, b_i = int(a), int(b)
                if a_i in eng and b_i in por:
                    pairs[a_i].append(b_i)
    out = []
    for e, ps in pairs.items():
        best = min(ps, key=lambda p: (pt_pt_score(por[p]), len(por[p])))
        out.append((eng[e], por[best], e, best))
    print(f"tatoeba: {len(out)} frases inglês–português")
    return out


def pt_pt_score(pt: str) -> int:
    """Quanto a frase parece português de Portugal (estar a + infinitivo, tu, autocarro...)."""
    s = pt.lower()
    n = 0
    n += 3 * len(re.findall(r"\b(estou|estás|está|estamos|estão|estava|estavas) a \w+r\b", s))
    n += 2 * len(re.findall(r"\b(tu|teu|tua|teus|tuas|ti|contigo|vós)\b", s))
    n += 2 * len(re.findall(r"\b(sabes|tens|estás|queres|podes|vais|és|fazes|dizes|consegues|precisas|gostas|foste|fizeste|disseste|viste)\b", s))
    n += 1 * len(re.findall(r"\b[a-zà-ú]+-(o|a|os|as|se|me|te|lhe|lhes|nos)\b", s))
    n += 2 * len(re.findall(r"\b(para|por|sem|até|basta|ao|de)\s+[a-zà-ú]+(ares|eres|ires)\b", s))  # infinitivo pessoal: "basta falhares"  # "ordenou-os", "mostra-se"
    n += 2 * len(re.findall(r"\b(autocarro|comboio|pequeno-almoço|telemóvel|casa de banho|frigorífico|rapariga|equipa|ecrã|facto)\b", s))
    return n


EXPLICIT = re.compile(r"\b(sex|sexy|sexual\w*|porn\w*|naked|nude|breasts?|nipples?|penis|vagina|pussy|dick|cock|fuck\w*|shit\w*|bitch\w*|ass|asshole|whore|slut|rape\w*|orgasm\w*|condoms?|erection|horny|thigh|thighs|panties|underwear|lingerie|kiss\w* (?:her|him) (?:neck|body)|in bed with|playboy|strip club|suicide|suicidal|cocaine|heroin|overdose|anorexi\w*|bulimi\w*|eating disorder|self-harm|cutting myself)\b")
# GDEX (Kilgarriff, Husák, McAdam, Rundell & Rychlý, 2008, "GDEX: Automatically finding good dictionary examples
# in a corpus", EURALEX): critérios usados para escolher exemplos do dicionário Macmillan para estudantes.
ANAPHOR = {"he", "she", "it", "they", "this", "that", "these", "those", "him", "her", "them", "his", "its", "their", "there",
           "and", "but", "so", "or", "then", "also", "yes", "no", "well", "oh", "ok", "okay", "because", "which", "such"}
IDEAL = {"B1": (6, 12), "B2": (7, 14), "C1": (8, 16), "C2": (8, 18)}


def level_of(rank: int) -> str:
    return next((c for c, lo, hi in LEVELS if lo <= rank <= hi), "C2")


def gdex(en: str, raw: list[str], unknown: int, rank: int, known: dict | None = None) -> float:
    """Penalidade (menor = melhor) de uma frase como exemplo, pelos critérios do GDEX."""
    lo, hi = IDEAL[level_of(rank)]
    n = len(raw)
    p = 0.0
    p += 0 if lo <= n <= hi else 1.5 * (lo - n if n < lo else n - hi)          # tamanho certo para o nível
    p += 4.0 * unknown                                                          # palavras raras (as outras devem ser conhecidas)
    if raw and raw[0].lower() in ANAPHOR:                                       # depende de frase anterior ("He did it.")
        p += 6.0
    names = sum(1 for i, t in enumerate(raw) if i > 0 and t[:1].isupper() and t != "I")
    p += 5.0 * names                                                            # nomes próprios (Tom, Mary... do Tatoeba)
    first = raw[0].lower() if raw else ""
    if raw and first not in BASIC_START and first not in ANAPHOR and (known is None or first not in known):
        p += 5.0                                                                # 1ª palavra que não é palavra conhecida: nome (Tom, Sami...)
    if re.search(r'["“”():;\[\]/]|\d|--|\.\.\.', en):                          # citações, números, parênteses
        p += 4.0
    if not re.search(r"[.?!]$", en.strip()):                                    # frase inteira
        p += 6.0
    if en.count(",") >= 2:                                                      # frase enrolada
        p += 1.5
    if re.search(r"[A-Z]{2,}", en):                                             # siglas / caixa alta
        p += 2.0
    if en.strip().endswith("!"):
        p += 0.5
    return p


BASIC_START = {"i", "you", "we", "the", "a", "an", "my", "your", "our", "what", "where", "when", "how", "why", "who", "can",
               "could", "do", "does", "did", "is", "are", "was", "were", "please", "let's", "let", "if", "every", "all",
               "some", "many", "most", "people", "everyone", "nobody", "there's", "would", "will", "should", "have", "has"}

HEAVY = re.compile(r"\b(kill|killed|killing|murder\w*|suicide|rape\w*|die|died|dies|dead|death|drugs?|sex\w*|naked|gun|guns|shot|bomb\w*|war|blood\w*|hate|stupid|idiot)\b")


def pick_sentences(word: str, forms: set[str], rank: int, idx: dict[str, list[int]], pairs: list, known_rank: dict[str, int], used: set[str], stems: list[str] | None = None, n: int = 2):
    """Frases curtas com a palavra em que as outras palavras já são conhecidas (i+1)."""
    cands = set()
    for f in forms | {word}:
        cands.update(idx.get(f, []))
    scored = []
    for i in cands:
        en, pt, eid, pid = pairs[i]
        if en.lower() in used or pt_pt_score(pt) > 0 or EXPLICIT.search(en.lower()):
            continue
        # a palavra como nome próprio ("Oliver Hardy") não vale
        if not any(t.lower() in forms and (i2 == 0 or not t[:1].isupper()) for i2, t in enumerate(re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", en))):
            continue
        raw = re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", en)
        toks = [t.lower() for t in raw]
        if not 4 <= len(toks) <= 20 or len(en) > 130 or word not in toks and not (forms & set(toks)):
            continue
        # nomes (Tom, Mary...) contam como conhecidos
        unknown = sum(1 for i2, t in enumerate(toks) if t not in forms and t != word and known_rank.get(t, LAST + 1) > rank
                      and not (i2 > 0 and raw[i2][:1].isupper()))
        shows = not stems or any(in_pt(st, pt) for st in stems)  # a tradução aparece na frase em português
        heavy = 1 if HEAVY.search(en.lower()) and not HEAVY.search(word) else 0
        score = gdex(en, raw, unknown, rank, known_rank) + 3.0 * heavy
        scored.append((not shows, score, unknown, en, pt, eid, pid))
    scored.sort()
    out = []
    for _, score, unknown, en, pt, eid, pid in scored:
        if unknown > 3 or score >= 12 or any(en.lower() == o[0].lower() for o in out):
            continue
        out.append((en, pt, eid, pid))
        if len(out) == n:
            break
    for en, *_ in out:
        used.add(en.lower())
    return out


def emoji_names(work: Path) -> dict[str, str]:
    dest = work / "fluentui-emoji"
    if not dest.exists():
        subprocess.run(["git", "clone", "--depth", "1", "--filter=blob:none", "--no-checkout", "https://github.com/microsoft/fluentui-emoji.git", str(dest)], check=True)
    out = subprocess.run(["git", "-C", str(dest), "ls-tree", "-d", "--name-only", "HEAD", "assets/"], capture_output=True, text=True, check=True).stdout
    names = [line.split("/", 1)[1] for line in out.splitlines() if "/" in line]
    return {n.lower(): n for n in names}


# ---------- montagem ----------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", default=str(HERE / ".cache" / "sources"))
    ap.add_argument("--source", default="v7", help="pacote atual (para não repetir palavras do A1/A2)")
    a = ap.parse_args()
    work = Path(a.work)
    work.mkdir(parents=True, exist_ok=True)

    base = json.loads((PACK / f"{a.source}.json").read_text(encoding="utf-8"))
    have = {w["lemma"].lower() for w in base["words"] if w["rank"] <= 721}
    used_sents = {s[0].lower() for w in base["words"] for s in w["sentences"]}

    ngsl = ngsl_order(fetch(URLS["ngsl"], work / "ngsl.csv"))
    fam = families(fetch(URLS["ngsl_fam"], work / "ngsl_fam.csv"))
    nawl_fam = families(fetch(URLS["nawl_fam"], work / "nawl_fam.csv"))
    nawl = sorted(set(nawl_fam.values()))
    for k, v in nawl_fam.items():
        fam.setdefault(k, v)
    print(f"NGSL {len(ngsl)} · NAWL {len(nawl)}")

    from wordfreq import top_n_list, zipf_frequency
    wf = [w for w in top_n_list("en", 250000) if re.fullmatch(r"[a-z]+", w) and len(w) > 1]

    have_heads = {fam.get(w, w) for w in have}
    wanted = set(ngsl) | set(nawl) | set(wf[:200000])
    kk = load_kaikki(fetch(URLS["kaikki"], work / "kaikki-en.jsonl"), wanted)
    print(f"wiktionary: {len(kk)} palavras com verbete")

    # ordem: NGSL (na ordem dela) e depois o resto (NAWL + wordfreq) por frequência
    order: list[str] = []
    taken: set[str] = set()

    def take(w: str) -> None:
        h = fam.get(w, w)
        if w in have or h in have_heads or w in taken or h in taken:
            return
        taken.add(w)
        order.append(w)

    ngsl_set = set(ngsl)
    for w in ngsl:
        take(w)
    rest = sorted(set(nawl) | set(wf[:200000]), key=lambda w: -zipf_frequency(w, "en"))
    for w in rest:
        if w in kk and is_lemma(kk[w]):
            b = form_base(kk[w])
            if b and (b in kk or b in have or b in taken):  # "swallowing" → já vem com "swallow"
                continue
            take(w)

    # formas de cada palavra (para achar frases com "went", "children"...)
    by_head: dict[str, set[str]] = defaultdict(set)
    for f, h in fam.items():
        by_head[h].add(f)
    def close(w: str, f: str) -> bool:
        return f[:3] == w[:3] or (len(w) < 4 and f[:2] == w[:2])
    forms: dict[str, set[str]] = {w: by_head[w] | {f for f in forms_of(kk.get(w, [])) if close(w, f)} | {w} for w in order}

    pairs = load_tatoeba(work)
    idx: dict[str, list[int]] = defaultdict(list)
    for i, (en, *_r) in enumerate(pairs):
        for t in set(WORD.findall(en.lower())):
            idx[t].append(i)

    freq = PtFreq([p[1] for p in pairs])
    emo = emoji_names(work)
    rank_of = {w["lemma"].lower(): w["rank"] for w in base["words"]}
    for f, h in fam.items():  # "went" já é conhecida quando "go" é
        if h in rank_of:
            rank_of.setdefault(f, rank_of[h])
    items: list[dict] = []
    skipped: dict[str, int] = defaultdict(int)
    rank = 722
    lost_core: dict[str, str] = {}

    def skip(w: str, why: str) -> None:
        skipped[why] += 1
        if w in ngsl_set:
            lost_core[w] = why
        if w in DEBUG:
            print(f"DEBUG {w}: fora — {why}")

    for w in DEBUG:
        if w not in order:
            print(f"DEBUG {w}: nem entrou na ordem (A1/A2? família? {fam.get(w)} / {w in have})")
    for w in order:
        if rank > LAST:
            break
        entries = kk.get(w, [])
        if not entries:
            skip(w, "sem verbete")
            continue
        if not is_lemma(entries) and w not in MANUAL:  # "favorite" é "grafia americana de favourite": vale
            skip(w, "não é palavra-base")
            continue
        if unsafe(entries, core=w in ngsl_set):
            skip(w, "vulgar/ofensiva")
            continue
        cands = pt_candidates(entries)
        if not cands and w not in MANUAL:
            skip(w, "sem tradução")
            continue
        pool = []
        for f in forms[w] | {w}:
            pool.extend(idx.get(f, []))
        pts = [pairs[i][1] for i in pool[:400]]
        pt, stems = choose_meaning(cands, pts, freq, w)
        if not pt and w in MANUAL:
            pt = MANUAL[w]
            stems = [stem(x) for x in re.split(r"\s*/\s*", pt.replace("…", " "))]
        if w in DEBUG:
            print(f"DEBUG {w}: {cands[:12]} → {pt}")
        if not pt:
            skip(w, "tradução sem prova nas frases")
            continue
        for f in forms[w]:
            rank_of.setdefault(f, rank)
        ss = pick_sentences(w, forms[w], rank, idx, pairs, rank_of, used_sents, stems)
        if not ss:
            skip(w, "sem frase")
            continue
        emoji = emo.get(w)
        items.append({"rank": rank, "lemma": w, "pt": pt, "accepted": [w], "concrete": emoji is not None, "emoji": emoji,
                      "ptbr_hint": None, "say": w, "sentences": [[en, ptx] for en, ptx, *_ in ss],
                      "src": [f"tatoeba:{e}/{p}" for _, _, e, p in ss]})
        rank += 1
    print("pulados:", dict(skipped))
    print(f"NGSL fora ({len(lost_core)}):", ", ".join(f"{k}[{v}]" for k, v in list(lost_core.items())[:400]))

    out_dir = PACK / "levels"
    out_dir.mkdir(exist_ok=True)
    for code, lo, hi in LEVELS:
        lvl = [it for it in items if lo <= it["rank"] <= hi]
        (out_dir / f"{code}.json").write_text(json.dumps(lvl, ensure_ascii=False, indent=0), encoding="utf-8")
        one = sum(1 for it in lvl if len(it["sentences"]) < 2)
        import random
        for it in random.Random(code).sample(lvl, min(25, len(lvl))):  # amostra para revisão no log
            print(f"  {it['rank']} {it['lemma']} = {it['pt']} | {it['sentences'][0][0]} = {it['sentences'][0][1]}")
        print(f"{code}: {len(lvl)} palavras ({lvl[0]['lemma'] if lvl else '-'} … {lvl[-1]['lemma'] if lvl else '-'}), "
              f"{sum(1 for it in lvl if it['emoji'])} com imagem, {one} com 1 frase só")


if __name__ == "__main__":
    sys.exit(main())
