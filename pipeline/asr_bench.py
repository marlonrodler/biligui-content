"""Teste do "ouvido" em português (análise de 19/11): quantos significados falados em português o Whisper acerta,
por tamanho de modelo (tiny = o do app hoje, base, small), em áudio limpo e com ruído (mais perto do microfone do celular).
Áudios: o significado em português de cada palavra do pacote (voz brasileira Piper). Uso: python asr_bench.py <pasta do pacote> [n]"""
import json, random, re, sys, time, unicodedata
from pathlib import Path
import numpy as np
from faster_whisper import WhisperModel
from faster_whisper.audio import decode_audio

def norm(s):
    s = unicodedata.normalize("NFD", s.lower()); s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]", " ", s)).strip()

def answers(pt):
    out = set()
    for part in re.split(r",|;|/", pt):
        p = part.strip()
        for b in (re.sub(r"\s*\([^)]*\)\s*", " ", p), p.replace("(", "").replace(")", "")):
            b = norm(b)
            if b: out.add(b); out.add(re.sub(r"^eu ", "", b))
    return out

root = Path(sys.argv[1]); n = int(sys.argv[2]) if len(sys.argv) > 2 else 300
ver = sorted((d for d in root.iterdir() if d.name.startswith("v") and d.name[1:].isdigit()), key=lambda d: int(d.name[1:]))[-1]
m = json.loads((ver / "manifest.json").read_text())
ws = [w for w in m["words"] if w.get("audio_pt")]
random.seed(7); sample = random.sample(ws, min(n, len(ws)))
multi = random.sample([w for w in ws if " " in w["translation"]["pt"].split("(")[0].strip()], 50)  # expressões ("eu quero ir")
rng = np.random.default_rng(7)
clips = []
for w in sample + multi:
    x = decode_audio(str(ver / w["audio_pt"]), sampling_rate=16000)
    noisy = x + rng.normal(0, np.sqrt(np.mean(x ** 2) / 10 ** (15 / 10)), x.shape).astype(np.float32)  # ruído a 15 dB
    clips.append((w, x, noisy))
res = {}
PLAN = {"tiny": [("limpo", None), ("ruido", None), ("ruido", "Resposta curta em português do Brasil.")],
        "base": [("limpo", None), ("ruido", None), ("ruido", "Resposta curta em português do Brasil.")],
        "small": [("ruido", None)]}
for size, runs in PLAN.items():
    model = WhisperModel(size, device="cpu", compute_type="int8", cpu_threads=4)
    for cond, prompt in runs:
        if True:
            ok = okm = 0; t0 = time.time(); bad = []
            for i, (w, x, noisy) in enumerate(clips):
                segs, _ = model.transcribe(x if cond == "limpo" else noisy, language="pt", beam_size=1, initial_prompt=prompt,
                                           condition_on_previous_text=False, without_timestamps=True, vad_filter=False)
                heard = norm(" ".join(s.text for s in segs))
                hit = any(f" {a} " in f" {heard} " for a in answers(w["translation"]["pt"]))
                if i < len(sample): ok += hit
                else: okm += hit
                if not hit and len(bad) < 12: bad.append(f'{w["translation"]["pt"]} → {heard}')
            k = f"{size} | {cond} | {'com dica' if prompt else 'sem dica'}"
            res[k] = (ok / len(sample), okm / max(1, len(multi)), (time.time() - t0) / len(clips))
            print(f"{k}: palavras {ok}/{len(sample)} ({ok/len(sample):.0%}), expressões {okm}/{len(multi)} ({okm/max(1,len(multi)):.0%}), {res[k][2]*1000:.0f} ms/áudio")
            print("   erros:", "; ".join(bad))
