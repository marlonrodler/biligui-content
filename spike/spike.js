// Lero · Fase 0 — testes de risco (voz e podcast com tela bloqueada). Código descartável.
const WORDS = [
  ['água', 'water'], ['reunião', 'meeting'], ['telefone', 'phone'], ['café', 'coffee'], ['casa', 'house'],
  ['carro', 'car'], ['dinheiro', 'money'], ['tempo', 'time'], ['amigo', 'friend'], ['trabalho', 'work'],
  ['bom', 'good'], ['pessoas', 'people'], ['obrigado', 'thank you'], ['manhã', 'morning'], ['família', 'family'],
  ['comida', 'food'], ['livro', 'book'], ['cachorro', 'dog'], ['porta', 'door'], ['feliz', 'happy'],
]

const $ = (id) => document.getElementById(id)
const results = []
const log = (msg) => { $('log').textContent += `${new Date().toLocaleTimeString()}  ${msg}\n`; $('log').scrollTop = 1e9 }

// ---------- Ambiente ----------
const standalone = matchMedia('(display-mode: standalone)').matches || navigator.standalone === true
const SR = window.SpeechRecognition || window.webkitSpeechRecognition
const env = [
  ['Tela de início', standalone], ['Web Speech existe', !!SR],
  ['Microfone', !!navigator.mediaDevices?.getUserMedia], ['Media Session', 'mediaSession' in navigator],
]
$('env').innerHTML = env.map(([k, v]) => `<span class="pill ${v ? 'ok' : 'bad'}">${v ? '✓' : '✗'} ${k}</span>`).join('')
log(`UA: ${navigator.userAgent}`)
log(`Ambiente: ${env.map(([k, v]) => `${k}=${v}`).join(', ')}`)
if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js').catch(() => {})

// ---------- Teste 1: voz ----------
let engine = 'webspeech'
let idx = 0
const score = { webspeech: { ok: 0, n: 0, ms: [] }, whisper: { ok: 0, n: 0, ms: [] } }

function setEngine(e) {
  engine = e
  $('eng-webspeech').setAttribute('aria-pressed', e === 'webspeech')
  $('eng-whisper').setAttribute('aria-pressed', e === 'whisper')
  $('whisper-status').hidden = e !== 'whisper'
  if (e === 'whisper') loadWhisper()
}
$('eng-webspeech').onclick = () => setEngine('webspeech')
$('eng-whisper').onclick = () => setEngine('whisper')

function showWord() {
  const [pt] = WORDS[idx % WORDS.length]
  $('prompt').textContent = `“${pt}”`
  $('hint').textContent = `Palavra ${(idx % WORDS.length) + 1} de ${WORDS.length}`
}
$('skip').onclick = () => { idx++; showWord(); $('heard').textContent = '' }
showWord()

const norm = (s) => s.toLowerCase().replace(/[^a-z ]/g, ' ').replace(/\s+/g, ' ').trim()
function grade(text, ms) {
  const [, en] = WORDS[idx % WORDS.length]
  const ok = norm(text).includes(norm(en))
  const s = score[engine]
  s.n++; if (ok) s.ok++; s.ms.push(ms)
  const avg = Math.round(s.ms.reduce((a, b) => a + b, 0) / s.ms.length)
  $(`score-${engine}`).textContent = `${s.ok}/${s.n} (${Math.round((s.ok / s.n) * 100)}%)`
  $(`time-${engine}`).textContent = `${(avg / 1000).toFixed(1)} s`
  $('heard').innerHTML = `Ouvi: <b>${text || '(nada)'}</b> — <span class="${ok ? 'ok' : 'bad'}">${ok ? 'acertou' : `esperado “${en}”`}</span> · ${(ms / 1000).toFixed(1)} s`
  results.push({ engine, expected: en, heard: text, ok, ms })
  log(`[${engine}] esperado=${en} ouvi="${text}" ok=${ok} ${ms}ms`)
  idx++; setTimeout(showWord, 900)
}

$('speak').onclick = () => (engine === 'webspeech' ? runWebSpeech() : runWhisper())

function runWebSpeech() {
  if (!SR) { $('heard').textContent = 'Web Speech não existe neste modo.'; log('[webspeech] indisponível'); return }
  const rec = new SR()
  rec.lang = 'en-US'; rec.interimResults = false; rec.maxAlternatives = 3; rec.continuous = false
  const t0 = performance.now()
  let done = false
  $('speak').disabled = true; $('heard').textContent = 'Ouvindo…'
  rec.onresult = (e) => {
    done = true
    const alts = [...e.results[0]].map((a) => a.transcript)
    grade(alts.join(' | '), Math.round(performance.now() - t0))
  }
  rec.onerror = (e) => { done = true; $('heard').textContent = `Erro: ${e.error}`; log(`[webspeech] erro=${e.error}`) }
  rec.onend = () => { $('speak').disabled = false; if (!done) $('heard').textContent = 'Não ouvi nada. Tente de novo.' }
  try { rec.start() } catch (err) { log(`[webspeech] start falhou: ${err.message}`); $('speak').disabled = false }
}

// Whisper local (transformers.js). O modelo baixa uma vez e fica em cache.
let whisper = null, whisperLoading = null
async function loadWhisper() {
  if (whisper || whisperLoading) return whisperLoading
  $('whisper-status').textContent = 'Baixando o modelo Whisper (~40 MB, só na primeira vez)…'
  const t0 = performance.now()
  whisperLoading = (async () => {
    const { pipeline } = await import('https://cdn.jsdelivr.net/npm/@huggingface/transformers@4.3.2')
    whisper = await pipeline('automatic-speech-recognition', 'Xenova/whisper-tiny.en', {
      progress_callback: (p) => { if (p.status === 'progress' && p.progress) $('whisper-status').textContent = `Baixando modelo… ${Math.round(p.progress)}%` },
    })
    const s = ((performance.now() - t0) / 1000).toFixed(1)
    $('whisper-status').textContent = `Modelo pronto (${s} s).`
    log(`[whisper] modelo carregado em ${s}s`)
  })().catch((e) => { $('whisper-status').textContent = `Falhou ao carregar: ${e.message}`; log(`[whisper] erro ao carregar: ${e.message}`); whisperLoading = null })
  return whisperLoading
}

async function recordClip(ms = 2200) {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
  const rec = new MediaRecorder(stream)
  const chunks = []
  rec.ondataavailable = (e) => chunks.push(e.data)
  const stopped = new Promise((r) => (rec.onstop = r))
  rec.start(); await new Promise((r) => setTimeout(r, ms)); rec.stop(); await stopped
  stream.getTracks().forEach((t) => t.stop())
  const buf = await new Blob(chunks, { type: rec.mimeType }).arrayBuffer()
  const decoded = await new AudioContext().decodeAudioData(buf)
  const off = new OfflineAudioContext(1, Math.ceil(decoded.duration * 16000), 16000)
  const src = off.createBufferSource(); src.buffer = decoded; src.connect(off.destination); src.start()
  return (await off.startRendering()).getChannelData(0)
}

async function runWhisper() {
  await loadWhisper(); if (!whisper) return
  $('speak').disabled = true
  try {
    $('heard').textContent = 'Gravando 2 s… fale agora'
    const audio = await recordClip()
    $('heard').textContent = 'Reconhecendo…'
    const t0 = performance.now()
    const out = await whisper(audio)
    grade((out.text || '').trim(), Math.round(performance.now() - t0))
  } catch (e) { $('heard').textContent = `Erro: ${e.message}`; log(`[whisper] erro: ${e.message}`) }
  $('speak').disabled = false
}

// ---------- Teste 2: podcast em arquivo único ----------
function wavFromBuffer(buf) {
  const data = buf.getChannelData(0), rate = buf.sampleRate
  const out = new DataView(new ArrayBuffer(44 + data.length * 2))
  const w = (o, s) => [...s].forEach((c, i) => out.setUint8(o + i, c.charCodeAt(0)))
  w(0, 'RIFF'); out.setUint32(4, 36 + data.length * 2, true); w(8, 'WAVE'); w(12, 'fmt ')
  out.setUint32(16, 16, true); out.setUint16(20, 1, true); out.setUint16(22, 1, true)
  out.setUint32(24, rate, true); out.setUint32(28, rate * 2, true); out.setUint16(32, 2, true); out.setUint16(34, 16, true)
  w(36, 'data'); out.setUint32(40, data.length * 2, true)
  for (let i = 0; i < data.length; i++) out.setInt16(44 + i * 2, Math.max(-1, Math.min(1, data[i])) * 0x7fff, true)
  return new Blob([out], { type: 'audio/wav' })
}

$('build').onclick = async () => {
  const minutes = 10, rate = 16000, cycle = 6
  $('build').disabled = true; $('build-status').textContent = 'rebobinando a fita… 📼'
  const t0 = performance.now()
  const off = new OfflineAudioContext(1, minutes * 60 * rate, rate)
  const beep = (t, freq) => {
    const o = off.createOscillator(), g = off.createGain()
    o.frequency.value = freq; o.connect(g); g.connect(off.destination)
    g.gain.setValueAtTime(0, t); g.gain.linearRampToValueAtTime(0.3, t + 0.02); g.gain.linearRampToValueAtTime(0, t + 0.35)
    o.start(t); o.stop(t + 0.4)
  }
  for (let t = 0.5; t < minutes * 60 - 1; t += cycle) { beep(t, 880); beep(t + 3.4, 440) }
  const blob = wavFromBuffer(await off.startRendering())
  const s = ((performance.now() - t0) / 1000).toFixed(1)
  const player = $('player')
  player.src = URL.createObjectURL(blob); player.hidden = false
  $('build-status').textContent = `pronto em ${s} s · ${(blob.size / 1e6).toFixed(1)} MB`
  log(`[podcast] montado em ${s}s, ${(blob.size / 1e6).toFixed(1)} MB`)
  if ('mediaSession' in navigator) {
    navigator.mediaSession.metadata = new MediaMetadata({
      title: 'Lero · Episódio de teste', artist: 'Lero', album: 'Fase 0',
      artwork: [{ src: 'icon-512.png', sizes: '512x512', type: 'image/png' }],
    })
    navigator.mediaSession.setActionHandler('play', () => player.play())
    navigator.mediaSession.setActionHandler('pause', () => player.pause())
    navigator.mediaSession.setActionHandler('seekbackward', () => { player.currentTime = Math.max(0, player.currentTime - 10) })
    navigator.mediaSession.setActionHandler('seekforward', () => { player.currentTime += 10 })
  }
  let last = 0
  player.ontimeupdate = () => { const m = Math.floor(player.currentTime / 60); if (m !== last) { last = m; log(`[podcast] tocando: ${m} min`) } }
  player.onended = () => log('[podcast] terminou os 10 min ✓')
  player.onpause = () => log(`[podcast] pausou em ${Math.round(player.currentTime)} s`)
  $('build').disabled = false
}

// ---------- Copiar resultado ----------
$('copy').onclick = async () => {
  const text = `${$('log').textContent}\nPlacar: ${JSON.stringify(score)}`
  try { await navigator.clipboard.writeText(text); $('copy').textContent = 'Copiado ✓' } catch { $('copy').textContent = 'Não deu para copiar' }
}
