# Qwen3-ASR Service Tuning & Troubleshooting

[中文](troubleshooting.md) | **English**

This guide collects **realtime vs offline behavior differences**, **trade-offs of the key tuning parameters**, and **diagnosed real-world cases**, as a basis for deployment tuning and problem triage. When you hit "offline fine, realtime broken", "missing content", or "wrong scene", start here.

Related: [Configuration](configuration_EN.md) (full parameter list) ｜ [Architecture](architecture_EN.md).

---

## 1. First, understand realtime vs offline

Realtime (WS `/v2/asr/stream`) and offline (`/v2/asr`) go through **different segmentation and filtering paths**, so the same file can yield different results. Key differences:

| Dimension | Offline `/v2/asr` | Realtime `/v2/asr/stream` (standard) |
|-----------|-------------------|--------------------------------------|
| Segmentation | FSMN-VAD **whole-file** fine cut | Online streaming VAD, **chunk-by-chunk** as audio arrives |
| Per-segment noise/SNR gate | **None** | **Yes** (`stream_noise_filter`, opt-in) |
| Decode unit | Segment (can be long, more context) | A single VAD segment (often short, esp. chorus fragments) |
| Punctuation | `use_punc` optional | Same |

> ⚠️ **The most common "realtime drops content" root cause**: the per-segment SNR gate of `stream_noise_filter` exists only in the realtime path, not offline. When enabled, on material with **background music / singing / far-field**, it may discard valid speech segments — manifesting as "offline complete, realtime missing". See Case 1.

---

## 2. Case 1: realtime drops singing / speech-over-BGM segments

### Symptom
Same file (a video containing a song): offline transcribes the lyrics fully; realtime leaves the singing section **entirely blank** (e.g. the whole 18s–78s chorus missing), while plain spoken dialogue transcribes fine.

### Diagnosis
1. **Rule out VAD**: replaying the first 130s through the streaming VAD shows the singing section (18–78s) **still produces plenty of speech segments**; results are identical at `vad_speech_noise_thres` 0.4 and 0.6 — so it is **not a VAD miss, and unrelated to that threshold**.
2. **Pin it on the noise gate**: recomputing each segment's SNR at the `should_gate` call (right before ASR in `_emit_final`) shows the singing segments are dropped in bulk with reason `snr`.

Measured (`stream_noise_filter: true`, `snr_min_db=6`, `energy_floor=-50`):

| Segment (ms) | Seg dBFS | Session floor dBFS | SNR (dB) | Verdict |
|--------------|----------|--------------------|----------|---------|
| 17050–17640 | -25.5 | -44.7 | **19.2** | ✅ pass (opening line) |
| 18190–18960 | -24.4 | -42.6 | **18.2** | ✅ pass |
| 31980–33200 | -24.2 | -24.1 | **-0.1** | ❌ drop (snr) |
| 38620–46000 | -23.7 | -24.1 | **0.4** | ❌ drop (snr) |
| 70160–78150 | -21.7 | -23.1 | **1.4** | ❌ drop (snr) |

> Of 29 segments in the first 130s, **19 were dropped — all within the singing region**.

### Root cause
The per-segment SNR gate compares "segment loudness" against "session noise floor"; if the gap is below `snr_min_db`, the segment is dropped. But the session floor is estimated by EMA **during non-speech periods** (see `NoiseFloorTracker`). In a song, the "non-speech gaps" are **still full of background music**, so the floor is pulled up near vocal level (~-24 dBFS), leaving singing segments with only 0–2 dB SNR — below the 6 dB threshold → dropped before ASR, no `final` emitted, hence the blank UI.

For vocal-music / singing / live-with-BGM material, the SNR gate's premise ("background = low-energy floor") simply doesn't hold, so it produces false drops.

### Fix (edit `config.yaml`, effective immediately)
- **Option A (recommended, keep the absolute energy gate)**: `stream_snr_min_db: 0`
  Disables only the adaptive SNR gate (the one killing singing), keeps the `stream_energy_floor_dbfs` absolute gate, which still drops true silence / extremely weak segments.
- **Option B (full)**: `stream_noise_filter: false`
  Disables the per-segment gate entirely; realtime keeps everything. Cost: far-field / ambient noise is no longer filtered.

---

## 3. Realtime noise-gate parameters

Apply to the realtime path only; the two gates are **OR'd** — either one triggering drops the segment (before ASR).

| Config key | Default | Effect | When to tune |
|------------|---------|--------|--------------|
| `stream_noise_filter` | `false` | Master switch for per-segment energy/SNR gating (opt-in) | On for far-field/noisy; for music/singing, off or see below |
| `stream_energy_floor_dbfs` | `-50.0` | **Absolute energy gate**: drop if segment loudness below this | More aggressive → raise (e.g. -45); keep more → lower |
| `stream_snr_min_db` | `6.0` | **Adaptive SNR gate**: drop if segment is less than this above the session floor; **`≤0` disables it** | With BGM/music set to `0`; far-field meetings may keep or raise |

Notes:
- The session floor only follows slowly during **non-speech** periods (EMA α=0.05), and **gets pulled up by sustained BGM** → the SNR gate is unreliable for music scenes.
- The absolute energy gate is floor-independent and always effective for "true silence / very weak far-field", so keeping it while disabling the SNR gate is the safest middle ground.

---

## 4. VAD decision threshold `vad_speech_noise_thres`

| Config key | Default | Semantics |
|------------|---------|-----------|
| `vad_speech_noise_thres` | `0.6` | FSMN-VAD speech/noise decision threshold, **shared by offline + realtime**. **Higher = stricter** (harder to call speech); lower = more permissive. |

- Speech missed (should fire but didn't) → **lower** (e.g. 0.4).
- Noise/music mistaken for speech, too many fragments → **raise** (e.g. 0.7–0.8).
- Note: **lowering this does NOT fix Case 1's drops** — those are the SNR gate, unrelated to this threshold.

---

## 5. Recommended config by content type

| Content type | `stream_noise_filter` | `stream_snr_min_db` | `vad_speech_noise_thres` | Notes |
|--------------|------------------------|----------------------|---------------------------|-------|
| Clean / near-field speech | `true` | `6.0` (default) | `0.6` | Defaults fine; gate filters occasional noise |
| Far-field meeting / noisy | `true` | `6.0`–`8.0` | `0.6`–`0.7` | The SNR gate is designed for this |
| **Music / singing / live-with-BGM** | `true` + SNR off **or** `false` | **`0`** | `0.4`–`0.6` | Avoid BGM-inflated floor killing vocals (Case 1) |
| Mixed, prefer recall | `false` | — | `0.4` | Capture everything, filter later |

---

## 6. Triage cheat sheet

| Symptom | Check first |
|---------|-------------|
| Realtime missing content, offline fine | Is `stream_noise_filter` on + does material contain BGM/music → set `stream_snr_min_db: 0` or disable the filter (Case 1) |
| Too many realtime fragments / noise as speech | Raise `vad_speech_noise_thres`; use `max_segment` to bound fallback cut length |
| Realtime emits nothing where there is speech | Confirm VAD fired (server log `[stream] ... VAD事件`), then whether it was gated (log `远场/噪声丢弃`) |
| Singing labeled "music" not "singing" | Offline: enable `scene_lyrics_aware` (recover via lyrics); see Configuration scene section |
| Scene probability >100% | Check `scene_weights` for bucket multipliers >1.0 |

> Realtime drops are always logged (`[stream] 远场/噪声丢弃 …` / `[stream] 跳过空段 …`); when triaging, read the server log rather than relying on the frontend result alone.
