"""Pipeline de conteúdo do Lero (piloto da Fase 0).

Lê a fonte revisada (pipeline/packs/<pack>/pilot.json), gera IPA (CMUdict), áudio (Kokoro),
imagens (Fluent Emoji 3D), valida e escreve um rascunho publicável em <out>/packs/<pack>/draft/.
Também gera amostras de vozes em <out>/voices/ para escolher as 3 vozes do pacote.

Uso: python pipeline/build.py --pack ngsl-spoken --out out [--skip-audio] [--skip-images]
"""
from __future__ import annotations

import argparse
import os
import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Vozes padrão do pacote (ADR-005 do conteúdo: EUA ♂, EUA ♀, Reino Unido ♀). Escolha final ouvindo as amostras.
PACK_VOICES = ["am_michael"]  # só a voz masculina (ADR-037)
PT_VOICE = "piper:pt_BR-faber-medium"  # significado em português: voz brasileira natural (Piper, modelo MIT, dados CC0; ADR-055)


def pt_spoken(pt: str) -> str:
    """Significado como se fala: "tudo bem / ok" → "tudo bem, ok"; tira os parênteses."""
    return re.sub(r"\s+", " ", pt.replace(" / ", ", ").replace("/", ", ").replace("(", "").replace(")", "")).strip()


CANDIDATE_VOICES = ["am_michael", "am_adam", "am_puck", "am_fenrir", "af_heart", "af_bella", "af_nicole",
                    "bf_emma", "bf_isabella", "bm_george", "bm_lewis"]
SAMPLE_TEXT = "Hello! I have a meeting at nine. Can I have some water?"
PT_SAMPLE = "Porém, no entanto. Esse aqui. Ocupado. Eu quero ir. Tudo bem, obrigado."

# Palavras básicas permitidas nas frases do piloto (no pacote real, vale a regra do rank).
BASIC = set("""a an the i you he she it we they me my your his her our their this that these those
is am are was were be have has do does don't not no yes and or but to of in on at for from by with
can will what where who how when here there now today very some any many every please some one
like want need go come see read run live love close near later big old cold best idea day month bus
table someone brazilian lives likes""".split())

ARPA = {"AA": "ɑ", "AE": "æ", "AO": "ɔ", "AW": "aʊ", "AY": "aɪ", "EH": "ɛ", "EY": "eɪ", "IH": "ɪ",
        "IY": "i", "OW": "oʊ", "OY": "ɔɪ", "UH": "ʊ", "UW": "u", "B": "b", "CH": "tʃ", "D": "d", "DH": "ð",
        "F": "f", "G": "ɡ", "HH": "h", "JH": "dʒ", "K": "k", "L": "l", "M": "m", "N": "n", "NG": "ŋ", "P": "p",
        "R": "r", "S": "s", "SH": "ʃ", "T": "t", "TH": "θ", "V": "v", "W": "w", "Y": "j", "Z": "z", "ZH": "ʒ"}
VOWELS = {"AA", "AE", "AH", "AO", "AW", "AY", "EH", "ER", "EY", "IH", "IY", "OW", "OY", "UH", "UW"}


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def arpabet_to_ipa(phones: list[str]) -> str:
    """ARPAbet (CMUdict) → IPA americano. Marca o acento tônico antes da consoante que abre a sílaba."""
    out: list[str] = []
    vowel_count = sum(1 for p in phones if p.rstrip("012") in VOWELS)
    for i, p in enumerate(phones):
        base, stress = p.rstrip("012"), p[len(p.rstrip("012")):]
        if base == "AH":
            sym = "ʌ" if stress in ("1", "2") else "ə"
        elif base == "ER":
            sym = "ɝ" if stress in ("1", "2") else "ɚ"
        else:
            sym = ARPA[base]
        if stress == "1" and vowel_count > 1:
            prev_is_consonant = i > 0 and phones[i - 1].rstrip("012") not in VOWELS
            if prev_is_consonant:
                out.insert(len(out) - 1, "ˈ")
            else:
                out.append("ˈ")
        out.append(sym)
    return "".join(out)


def ipa_for(text: str, cmu: dict) -> str | None:
    parts = []
    for w in re.findall(r"[a-z']+", text.lower()):
        if w not in cmu:
            return None
        parts.append(arpabet_to_ipa(cmu[w][0]))
    return " ".join(parts)


def cloze(sentence: str, lemma: str) -> str | None:
    pattern = re.compile(rf"\b{re.escape(lemma)}\w*\b", re.IGNORECASE)
    return pattern.sub("___", sentence, count=1) if pattern.search(sentence) else None


# ---------- Áudio ----------
# ARPAbet (CMUdict) → fonemas do Kokoro/misaki (inglês americano), para forçar a pronúncia certa (ADR-060)
MISAKI = {"AA": "ɑ", "AE": "æ", "AH": "ʌ", "AO": "ɔ", "AW": "W", "AY": "I", "EH": "ɛ", "ER": "ɜɹ", "EY": "A", "IH": "ɪ",
          "IY": "i", "OW": "O", "OY": "Y", "UH": "ʊ", "UW": "u", "B": "b", "CH": "ʧ", "D": "d", "DH": "ð", "F": "f",
          "G": "ɡ", "HH": "h", "JH": "ʤ", "K": "k", "L": "l", "M": "m", "N": "n", "NG": "ŋ", "P": "p", "R": "ɹ", "S": "s",
          "SH": "ʃ", "T": "t", "TH": "θ", "V": "v", "W": "w", "Y": "j", "Z": "z", "ZH": "ʒ"}


def arpabet_to_misaki(phones: list[str]) -> str:
    out = []
    for ph in phones:
        base, stress = re.sub(r"\d", "", ph), re.sub(r"\D", "", ph)
        sym = MISAKI.get(base, "")
        if base == "AH" and stress == "0":
            sym = "ə"
        if base == "ER" and stress == "0":
            sym = "əɹ"
        out.append(("ˈ" if stress == "1" else "ˌ" if stress == "2" else "") + sym)
    return "".join(out)


class Tts:
    """Kokoro com cache (ADR-047): cada áudio é guardado pelo hash de (modelo, voz, velocidade, texto, tratamento).
    Na próxima vez, só o que é novo vai para o Kokoro; o resto é copiado do cache (que o GitHub Actions guarda)."""
    VERSION = "kokoro-82m|0.95|trim-128k"

    def __init__(self, cache: Path) -> None:
        self.pipes = None
        self.cache = cache
        # várias máquinas em paralelo (ADR-053): LERO_SHARD="i/n" gera só os áudios cujo hash cai no pedaço i
        sh = os.environ.get("LERO_SHARD")
        self.shard = tuple(int(x) for x in sh.split("/")) if sh else None
        self.cache.mkdir(parents=True, exist_ok=True)
        self.hits = self.misses = 0

    def wav(self, text: str, voice: str, dest: Path) -> list[list]:
        """Gera o wav e devolve o tempo de cada palavra [texto, início, fim] (o Kokoro calcula; ADR-050)."""
        import numpy as np
        import soundfile as sf
        if voice.startswith("piper:"):  # português: Piper (o português do Kokoro soava estrangeiro; ADR-055)
            import wave
            from piper import PiperVoice
            try:
                from piper import SynthesisConfig
                cfg = SynthesisConfig(length_scale=1.05)
            except ImportError:
                cfg = None
            name = voice.split(":", 1)[1]
            if not hasattr(self, "piper"):
                self.piper = {}
            if name not in self.piper:
                d = Path(os.environ.get("LERO_PIPER_DIR", Path(__file__).resolve().parent / ".cache" / "piper"))
                self.piper[name] = PiperVoice.load(str(d / f"{name}.onnx"))
            with wave.open(str(dest), "wb") as wf:
                self.piper[name].synthesize_wav(text, wf, syn_config=cfg) if cfg else self.piper[name].synthesize_wav(text, wf)
            return []
        if self.pipes is None:
            from kokoro import KPipeline  # import tardio: só quando precisar gerar de verdade
            self.pipes = {"a": KPipeline(lang_code="a"), "b": KPipeline(lang_code="b"), "p": KPipeline(lang_code="p")}  # p = português (ADR-041)
        chunks, marks, off = [], [], 0.0
        for r in self.pipes[voice[0]](text, voice=voice, speed=0.95):
            a = r.audio.numpy() if hasattr(r.audio, "numpy") else r.audio
            for tk in (getattr(r, "tokens", None) or []):
                st, en = getattr(tk, "start_ts", None), getattr(tk, "end_ts", None)
                if st is not None and en is not None and re.search(r"[A-Za-z0-9]", tk.text or ""):
                    marks.append([tk.text, off + st, off + en])
            chunks.append(a)
            off += len(a) / 24000
        pcm = np.concatenate(chunks)
        # o m4a corta o silêncio do começo (-50 dB): os tempos andam junto
        loud = np.flatnonzero(np.abs(pcm) > 10 ** (-50 / 20))
        lead = (loud[0] / 24000) if len(loud) else 0.0
        sf.write(dest, pcm, 24000)
        return [[t, round(max(0.0, s0 - lead), 3), round(max(0.0, e0 - lead), 3)] for t, s0, e0 in marks]

    def timed(self, text: str, voice: str, dest: Path, tmp: Path) -> tuple[float, list[list]]:
        """Como `say`, mas também devolve o tempo de cada palavra (guardado ao lado do áudio no cache)."""
        key = self.key(text, voice)
        hit, side = self.cache / f"{key}.m4a", self.cache / f"{key}.words.json"
        if hit.exists() and side.exists():
            self.hits += 1
            marks = json.loads(side.read_text())
        elif self.mine(key) is False:
            return 0.0, []
        else:
            self.misses += 1
            raw = tmp / f"{key}.wav"
            marks = self.wav(text, voice, raw)
            to_m4a(raw, hit)
            raw.unlink(missing_ok=True)
            side.write_text(json.dumps(marks))
        shutil.copyfile(hit, dest)
        return duration(dest), marks

    @classmethod
    def key(cls, text: str, voice: str) -> str:
        # "|2" nas vozes Piper: o cache tinha áudio da voz antiga gravado com a chave da nova (seed_cache, corrigido)
        salt = "|2" if voice.startswith("piper:") else ""
        return hashlib.sha256(f"{cls.VERSION}|{voice}{salt}|{text}".encode()).hexdigest()[:32]

    def mine(self, key: str) -> bool | None:
        """None = sem divisão; True/False = este pedaço gera (ou não) este áudio."""
        if not self.shard:
            return None
        i, n = self.shard
        return int(key[:8], 16) % n == i

    # ---------- conferência por reconhecimento de voz (ADR-060) ----------
    def heard(self, path: Path) -> str:
        if not hasattr(self, "asr"):
            from faster_whisper import WhisperModel
            self.asr = WhisperModel("base.en", device="cpu", compute_type="int8")
        segs, _ = self.asr.transcribe(str(path), language="en", beam_size=5, condition_on_previous_text=False, vad_filter=False)
        return " ".join(s.text for s in segs).strip()

    def same(self, expected: str, heard: str) -> bool:
        """Igual pelas palavras ou pela pronúncia (CMUdict, sem tonicidade): "four" = "for" vale; "vox" ≠ "fox"."""
        norm = lambda x: re.findall(r"[a-z']+", x.lower().replace("’", "'"))
        a, b = norm(expected), norm(heard)
        if a == b:
            return True
        cmu = getattr(self, "cmu", None) or {}
        def phon(ws: list[str]) -> list[str] | None:
            out: list[str] = []
            for w in ws:
                if w not in cmu:
                    return None
                out += [re.sub(r"\d", "", p) for p in cmu[w][0]]
            return out
        pa, pb = phon(a), phon(b)
        return pa is not None and pa == pb

    def say_checked(self, text: str, voice: str, dest: Path, tmp: Path) -> float:
        """Como say(), mas confere a palavra gerada ouvindo com o Whisper; se o Kokoro errou ("fox" → "vox"),
        gera de novo como "fox." e depois pelos fonemas do CMUdict. O resultado da conferência fica no cache (.qa)."""
        key = self.key(text, voice)
        hit, qa = self.cache / f"{key}.m4a", self.cache / f"{key}.qa"
        if hit.exists() and qa.exists():
            self.hits += 1
            shutil.copyfile(hit, dest)
            return duration(dest)
        if self.mine(key) is False:
            return 0.0
        dur = self.say(text, voice, dest, tmp)
        if voice.startswith("piper:") or os.environ.get("LERO_SKIP_QA"):
            return dur
        got = self.heard(hit)
        if self.same(text, got):
            qa.write_text("ok")
            return dur
        self.qa_fixed = getattr(self, "qa_fixed", 0)
        variants: list[tuple[str, str]] = [("text", text + ".")]
        words = re.findall(r"[a-z']+", text.lower())
        cmu = getattr(self, "cmu", None) or {}
        if words and all(w in cmu for w in words):
            variants.append(("phonemes", " ".join(arpabet_to_misaki(cmu[w][0]) for w in words)))
        for kind, v in variants:
            raw, m4a = tmp / f"{key}-qa.wav", tmp / f"{key}-qa.m4a"
            try:
                self.wav(v, voice, raw) if kind == "text" else self.wav_phonemes(v, voice, raw)
                to_m4a(raw, m4a)
            except Exception as e:  # noqa: BLE001
                print(f"  conferência: falhou gerar {kind} para {text!r}: {e}")
                continue
            got2 = self.heard(m4a)
            if self.same(text, got2):
                shutil.copyfile(m4a, hit)
                shutil.copyfile(hit, dest)
                qa.write_text(f"fixed:{kind}")
                self.qa_fixed += 1
                print(f"  conferência: {text!r} soava {got!r}; corrigido ({kind})")
                return duration(dest)
        qa.write_text(f"fail:{got}")
        print(f"  conferência: {text!r} soa {got!r} e não deu para corrigir")
        self.qa_failed = getattr(self, "qa_failed", []) + [(text, got)]
        return dur

    def wav_phonemes(self, phonemes: str, voice: str, dest: Path) -> None:
        import numpy as np
        import soundfile as sf
        self.wav("warm up", voice, dest)  # garante as pipelines carregadas
        pipe = self.pipes[voice[0]]
        chunks = [r.audio.numpy() if hasattr(r.audio, "numpy") else r.audio for r in pipe.generate_from_tokens(tokens=phonemes, voice=voice, speed=0.95)]
        sf.write(dest, np.concatenate(chunks), 24000)

    def say(self, text: str, voice: str, dest: Path, tmp: Path, bitrate: str = "128k") -> float:
        """Gera (ou copia do cache) o m4a de `text` na voz `voice` e devolve a duração em segundos.
        Frases saem em 64 kbps (ADR-061): a voz é gerada a 24 kHz e 128 kbps só ocupava espaço."""
        key = self.key(text + ("" if bitrate == "128k" else f"\u0000{bitrate}"), voice)
        hit = self.cache / f"{key}.m4a"
        if hit.exists():
            self.hits += 1
        elif self.mine(key) is False:
            return 0.0
        else:
            self.misses += 1
            raw = tmp / f"{key}.wav"
            self.wav(text, voice, raw)
            to_m4a(raw, hit, bitrate)
            raw.unlink(missing_ok=True)
        shutil.copyfile(hit, dest)
        return duration(dest)


def to_m4a(src: Path, dest: Path, bitrate: str = "128k") -> float:
    # só corta o silêncio das pontas. Sem loudnorm e com mais qualidade: a voz soava rouca/robótica (ADR-037, voz "B")
    trim = ("silenceremove=start_periods=1:start_threshold=-50dB,areverse,"
            "silenceremove=start_periods=1:start_threshold=-50dB,areverse")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-af", trim, "-ac", "1",
                    "-c:a", "aac", "-b:a", bitrate, str(dest)], check=True)
    return duration(dest)


def duration(f: Path) -> float:
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(f)],
                           capture_output=True, text=True, check=True)
    return float(probe.stdout.strip() or 0)


# ---------- Imagens ----------
EMOJI_CACHE: list[Path | None] = [None]


def fetch_emoji(name: str, dest: Path) -> bool:
    # cache por nome do emoji (ADR-047): a mesma imagem não é baixada de novo
    c = EMOJI_CACHE[0]
    if c is not None:
        c.mkdir(parents=True, exist_ok=True)
        hit = c / (re.sub(r"[^a-z0-9]+", "-", name.lower()) + ".webp")
        if hit.exists():
            shutil.copyfile(hit, dest)
            return True
        ok = _fetch_emoji(name, dest)
        if ok:
            shutil.copyfile(dest, hit)
        return ok
    return _fetch_emoji(name, dest)


def _fetch_emoji(name: str, dest: Path) -> bool:
    from PIL import Image
    folder = urllib.parse.quote(name)
    base = name.lower().replace(' ', '_')
    # pessoas e mãos têm tons de pele: a versão padrão fica em Default/
    urls = [f"https://raw.githubusercontent.com/microsoft/fluentui-emoji/main/assets/{folder}/3D/{urllib.parse.quote(base + '_3d.png')}",
            f"https://raw.githubusercontent.com/microsoft/fluentui-emoji/main/assets/{folder}/Default/3D/{urllib.parse.quote(base + '_3d_default.png')}"]
    try:
        tmp = dest.with_suffix(".png")
        for i, url in enumerate(urls):
            try:
                with urllib.request.urlopen(url, timeout=30) as r:
                    tmp.write_bytes(r.read())
                break
            except urllib.error.HTTPError:
                if i == len(urls) - 1:
                    raise
        Image.open(tmp).convert("RGBA").resize((256, 256), Image.LANCZOS).save(dest, "WEBP", quality=90)
        tmp.unlink()
        return True
    except Exception as e:  # noqa: BLE001 — falha vira flag, não derruba o pipeline
        print(f"  imagem falhou ({name}): {e}", file=sys.stderr)
        return False


# ---------- Build ----------
def build(pack: str, out: Path, skip_audio: bool, skip_images: bool, source: str = "pilot") -> dict:
    import cmudict
    cmu = cmudict.dict()
    src = json.loads((ROOT / "packs" / pack / f"{source}.json").read_text(encoding="utf-8"))
    draft = out / "packs" / pack / "draft"
    if draft.exists():
        shutil.rmtree(draft)
    (draft / "audio").mkdir(parents=True)
    (draft / "img").mkdir()
    cache = Path(os.environ.get("LERO_CACHE", Path(__file__).resolve().parent / ".cache"))
    tts = None if skip_audio else Tts(cache / "tts")
    if tts:
        tts.cmu = cmu
    EMOJI_CACHE[0] = cache / "img"
    tmp = out / ".tmp"
    tmp.mkdir(parents=True, exist_ok=True)

    lemmas = {w["lemma"].lower() for w in src["words"]} | {p for w in src["words"] for p in re.findall(r"[a-z']+", w.get("say", w["lemma"]).lower())}
    seen_sentences: set[str] = set()
    words, report = [], []

    # limite de tempo (ADR-053): se gerar áudio demorar demais, para aqui e publica o que já tem;
    # o cache guarda o que foi feito e a próxima rodada continua de onde parou
    import time
    budget = float(os.environ.get("LERO_BUDGET_MIN", "0")) * 60
    t0 = time.monotonic()
    for pos, w in enumerate(sorted(src["words"], key=lambda x: x.get("rank", 0)), start=1):
        if budget and time.monotonic() - t0 > budget:
            print(f"AVISO: limite de tempo; rascunho PARCIAL até o rank {words[-1]['rank'] if words else 0}. Rode de novo para continuar.")
            break
        # "say" = o que se ensina (palavra ou pedaço, ex.: "the" vira "in the morning"). O id fica a palavra (ADR-034).
        say = w.get("say", w["lemma"])
        rank = w.get("rank", pos)
        wid = f"{pack}:{slug(w['lemma'])}"
        flags: list[str] = []
        ipa = ipa_for(say.rstrip("?"), cmu)
        if not ipa:
            flags.append("no_ipa")

        entry = {"id": wid, "rank": rank, "lemma": say, "headword": w["lemma"], "ipa": ipa, "translation": {"pt": w["pt"]},
                 "accepted": w["accepted"], "ptbr_hint": w.get("ptbr_hint"), "concrete": w["concrete"],
                 "image": None, "audio": {}, "sentences": []}
        if w.get("deck"):
            entry["deck"] = w["deck"]

        if w["concrete"]:
            if w.get("emoji") and not skip_images:
                img = draft / "img" / f"{slug(w['lemma'])}.webp"
                if fetch_emoji(w["emoji"], img):
                    entry["image"] = f"img/{img.name}"
            if not entry["image"]:
                flags.append("image_missing")

        wdir = draft / "audio" / slug(w["lemma"])
        wdir.mkdir()
        if tts:
            for v in PACK_VOICES:
                dur = tts.say_checked(say, v, wdir / f"{v}.m4a", tmp)
                entry["audio"][v] = f"audio/{slug(w['lemma'])}/{v}.m4a"
                if dur > 2.5:
                    flags.append(f"audio_length:{v}")

        # o significado em português, falado (voz brasileira, ADR-041/055), para o podcast do dia
        if tts:
            tts.say(pt_spoken(w["pt"]), PT_VOICE, wdir / "pt.m4a", tmp)
            entry["audio_pt"] = f"audio/{slug(w['lemma'])}/pt.m4a"

        for i, (en, pt) in enumerate(w["sentences"], start=1):
            s = {"en": en, "pt": pt, "cloze": cloze(en, w["lemma"]), "audio": None}
            tokens = re.findall(r"[a-z']+", en.lower())
            if len(tokens) > 9:
                flags.append(f"too_long:s{i}")
            if not s["cloze"]:
                flags.append(f"missing_target:s{i}")
            unknown = [t for t in tokens if t not in BASIC and t not in lemmas and not t.startswith(w["lemma"].lower())]
            if unknown:
                flags.append(f"unknown_word:s{i}:{','.join(unknown)}")
            if en.lower() in seen_sentences:
                flags.append(f"duplicate_sentence:s{i}")
            seen_sentences.add(en.lower())
            if tts:
                v = PACK_VOICES[0]  # frases também na voz masculina (ADR-037)
                dur = tts.say(en, v, wdir / f"s{i}.m4a", tmp, bitrate="64k")
                s["audio"] = f"audio/{slug(w['lemma'])}/s{i}.m4a"
                if dur > 6:
                    flags.append(f"audio_length:s{i}")
            entry["sentences"].append(s)

        if not tts:
            flags.append("audio_skipped")
        words.append(entry)
        report.append({"id": wid, "flags": flags})
        print(f"{rank:>3} {w['lemma']:<10} {ipa or '?':<14} {'; '.join(flags) or 'ok'}")

    # tocar na palavra para ouvir (ADR-069 do app): áudio de cada palavra solta das frases e expressões que não tem
    # cartão próprio ("the", "it", "worked"), na mesma voz. Fica em manifest["say"] = {palavra: arquivo}.
    say: dict[str, str] = {}
    if tts:
        have = {e["lemma"].lower() for e in words if " " not in e["lemma"] and e["audio"]}
        forms: set[str] = set()
        for e in words:
            for t in re.findall(r"[A-Za-z']+", " ".join([e["lemma"]] + [x["en"] for x in e["sentences"]])):
                k = t.lower().strip("'")
                if k and k not in have and len(k) <= 24:
                    forms.add(k)
        (draft / "say").mkdir(exist_ok=True)
        for k in sorted(forms):
            dest = draft / "say" / f"{hashlib.sha1(k.encode()).hexdigest()[:16]}.m4a"
            text = "I" if k == "i" else k
            tts.say(text, PACK_VOICES[0], dest, tmp, bitrate="64k")
            if dest.exists():
                say[k] = f"say/{dest.name}"
        print(f"palavras soltas para tocar: {len(say)} de {len(forms)}")

    manifest = {"id": pack, "version": "draft", "source_lang": "pt", "target_lang": "en", "accent": "en-US",
                "license": "CC BY-SA 4.0",
                "credits": ["NGSL-Spoken, NGSL 1.2, NAWL 1.2 (newgeneralservicelist.com, CC BY-SA 4.0)", "wordfreq (CC BY-SA 4.0)",
                            "Wiktionary (CC BY-SA 4.0)", "Tatoeba (tatoeba.org, CC BY 2.0 FR)", "Kokoro-82M (Apache 2.0)", "Piper pt_BR-faber-medium (MIT; dados CC0)",
                            "Fluent Emoji © Microsoft (MIT)", "CMU Pronouncing Dictionary (BSD-2)"],
                "voices": PACK_VOICES, "voice_pt": PT_VOICE, "words": words, "say": say}
    if src.get("episodes"):
        manifest["episodes"] = src["episodes"]
    # histórias do podcast (ADR-049)
    if tts and tts.shard:
        print(f"pedaço {tts.shard[0]}/{tts.shard[1]}: áudio {tts.hits} do cache, {tts.misses} gerados agora")
        if tts.shard[0] != 0:
            return {}
        tts.shard = None  # as histórias ficam com o pedaço 0 (precisam de todos os áudios das falas)
    from stories import build_stories
    manifest["stories"] = build_stories(words, tts, draft, tmp, Path(__file__).resolve().parent / "stories")
    body = json.dumps(manifest, ensure_ascii=False, indent=1)
    (draft / "manifest.json").write_text(body, encoding="utf-8")
    (draft / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    (draft / "index.html").write_text(preview_html(manifest, report), encoding="utf-8")
    print(f"sha256 manifest: {hashlib.sha256(body.encode()).hexdigest()[:16]}…")
    if tts:
        print(f"áudio: {tts.hits} do cache, {tts.misses} gerados agora")

    if tts:
        vdir = out / "voices"
        vdir.mkdir(parents=True, exist_ok=True)
        for v in CANDIDATE_VOICES:
            tts.say(SAMPLE_TEXT, v, vdir / f"{v}.m4a", tmp)
        # português: a voz nova (Piper) e a antiga (Kokoro), para comparar de ouvido (ADR-055)
        tts.say(PT_SAMPLE, PT_VOICE, vdir / "pt-novo.m4a", tmp)
        tts.say(PT_SAMPLE, "pm_alex", vdir / "pt-antigo.m4a", tmp)
        (vdir / "index.html").write_text(voices_html(), encoding="utf-8")
    shutil.rmtree(tmp, ignore_errors=True)
    return manifest


# ---------- Páginas de revisão ----------
STYLE = """<style>body{margin:0;background:#0E1430;color:#EEF0FF;font:16px/1.5 -apple-system,system-ui,sans-serif;padding:16px}
h1{font-size:22px}.card{background:#161E44;border:1px solid #2A3470;border-radius:16px;padding:14px;margin:12px 0}
.w{font-size:28px;font-weight:600}.m{color:#9AA3D9;font-size:14px}.flag{color:#F0997B;font-size:13px}.ok{color:#5DCAA5;font-size:13px}
img{width:72px;height:72px;float:right}audio{width:100%;height:36px;margin:4px 0}</style>"""


def preview_html(m: dict, report: list) -> str:
    flags = {r["id"]: r["flags"] for r in report}
    cards = []
    for w in m["words"]:
        f = flags[w["id"]]
        aud = "".join(f'<div class="m">{v}</div><audio controls preload="none" src="{p}"></audio>' for v, p in w["audio"].items())
        sents = "".join(
            f'<p>{html.escape(s["en"])}<br><span class="m">{html.escape(s["pt"])} · lacuna: {html.escape(s["cloze"] or "—")}</span>'
            + (f'<audio controls preload="none" src="{s["audio"]}"></audio>' if s["audio"] else "") + "</p>"
            for s in w["sentences"])
        img = f'<img src="{w["image"]}" alt="">' if w["image"] else ""
        status = f'<div class="flag">⚠ {html.escape("; ".join(f))}</div>' if f else '<div class="ok">✓ sem alertas</div>'
        cards.append(f'<div class="card">{img}<div class="w">{html.escape(w["lemma"])}</div>'
                     f'<div class="m">/{html.escape(w["ipa"] or "?")}/ · {html.escape(w["translation"]["pt"])} · '
                     f'aportuguesado: {html.escape(w["ptbr_hint"] or "—")} · aceitas: {html.escape(", ".join(w["accepted"]))}</div>'
                     f'{status}{aud}{sents}</div>')
    return (f'<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" '
            f'content="width=device-width,initial-scale=1"><title>Lero · rascunho</title>{STYLE}</head><body>'
            f'<h1>Lero · rascunho do pacote ({len(m["words"])} palavras)</h1>'
            f'<p class="m">Vozes: {", ".join(m["voices"])}. Revisão provisória até existir a tela do admin.</p>'
            + "".join(cards) + "</body></html>")


def voices_html() -> str:
    rows = "".join(f'<div class="card"><div class="w" style="font-size:18px">{v}</div>'
                   f'<div class="m">{("Português · " + ("Piper faber (nova)" if v == "pt-novo" else "Kokoro pm_alex (antiga)")) if v.startswith("pt-") else ("EUA" if v[0] == "a" else "Reino Unido") + " · " + ("mulher" if v[1] == "f" else "homem")}</div>'
                   f'<audio controls preload="none" src="{v}.m4a"></audio></div>' for v in [*CANDIDATE_VOICES, "pt-novo", "pt-antigo"])
    return (f'<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" '
            f'content="width=device-width,initial-scale=1"><title>Lero · vozes</title>{STYLE}</head><body>'
            f'<h1>Escolha as 3 vozes</h1><p class="m">Frase: “{SAMPLE_TEXT}”. Escolha 1 homem EUA, 1 mulher EUA e 1 voz britânica.</p>'
            + rows + "</body></html>")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", default="ngsl-spoken")
    ap.add_argument("--out", default="out")
    ap.add_argument("--skip-audio", action="store_true")
    ap.add_argument("--skip-images", action="store_true")
    ap.add_argument("--source", default="pilot", help="arquivo em packs/<pack>/ sem .json (ex.: v1)")
    a = ap.parse_args()
    build(a.pack, Path(a.out), a.skip_audio, a.skip_images, a.source)
