"""What drives the waveform.

jevis has no speech recogniser of its own: a dictation tool types into the
focused field. So the bar needs a *level* to animate, and there are three
honest sources for one:

  TypingLevel   bursts of injected characters. Dictation tools type a phrase
                at a time, so the cadence of the text is the cadence of speech.
  MicLevel      real RMS from the default input device. Opt-in with JEVIS_MIC=1,
                because jevis does not otherwise open the microphone.
  Envelope      the amplitude envelope of a WAV file. The --demo driver speaks
                each command with Windows TTS and plays this back in sync.

All three expose `level()` in 0..1 and are cheap enough to poll every frame.
"""
from __future__ import annotations

import math
import os
import subprocess
import tempfile
import threading
import time
import wave


class TypingLevel:
    """Rises on each burst of characters, decays like a VU meter."""

    def __init__(self, decay: float = 3.2) -> None:
        self.decay = decay
        self.value = 0.0
        self.stamp = time.time()

    def bump(self, chars: int = 1) -> None:
        self.value = min(1.0, self._now() + 0.18 + 0.05 * min(chars, 8))

    def _now(self) -> float:
        dt = time.time() - self.stamp
        self.stamp = time.time()
        self.value *= math.exp(-self.decay * dt)
        return self.value

    def level(self) -> float:
        return self._now()


class MicLevel:
    """RMS of the default input device. Needs the optional `sounddevice`."""

    def __init__(self) -> None:
        import sounddevice  # noqa: F401  (fail early if it is missing)

        self.value = 0.0
        self.stream = None

    def start(self) -> None:
        import numpy as np
        import sounddevice as sd

        def callback(indata, _frames, _time, _status) -> None:
            rms = float(np.sqrt(np.mean(np.square(indata))))
            # Speech sits around -35..-10 dBFS; map that range onto 0..1.
            db = 20 * math.log10(max(rms, 1e-6))
            target = max(0.0, min(1.0, (db + 50) / 38))
            self.value += (target - self.value) * (0.6 if target > self.value else 0.15)

        self.stream = sd.InputStream(channels=1, samplerate=16000, blocksize=512,
                                     callback=callback)
        self.stream.start()

    def stop(self) -> None:
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        self.value = 0.0

    def level(self) -> float:
        return self.value


class Envelope:
    """Plays back a precomputed amplitude envelope against the wall clock."""

    def __init__(self, levels: list[float], fps: int, duration: float) -> None:
        self.levels = levels
        self.fps = fps
        self.duration = duration
        self.started = 0.0

    def start(self) -> None:
        self.started = time.time()

    def elapsed(self) -> float:
        return time.time() - self.started if self.started else 0.0

    def done(self) -> bool:
        return self.started and self.elapsed() >= self.duration

    def level(self) -> float:
        if not self.started:
            return 0.0
        index = int(self.elapsed() * self.fps)
        return self.levels[index] if 0 <= index < len(self.levels) else 0.0


def envelope_of(path: str, fps: int = 60) -> Envelope:
    """Peak-normalised RMS envelope of a 16-bit PCM WAV."""
    from array import array

    with wave.open(path, "rb") as wav:
        rate, channels, width = wav.getframerate(), wav.getnchannels(), wav.getsampwidth()
        raw = wav.readframes(wav.getnframes())
    if width != 2:
        raise ValueError("expected 16-bit PCM")
    samples = array("h", raw)[::channels]  # first channel is plenty for a meter
    hop = max(1, rate // fps)
    frames = []
    for i in range(0, len(samples), hop):
        chunk = samples[i:i + hop]
        frames.append(math.sqrt(sum(s * s for s in chunk) / len(chunk)) / 32768.0)
    peak = max(frames) if frames else 1.0
    levels = [min(1.0, (f / peak) ** 0.7) if peak > 0 else 0.0 for f in frames]
    # SAPI pads the utterance with silence; the transcript should track the
    # voice, not the padding.
    voiced = [i for i, v in enumerate(levels) if v > 0.04]
    if voiced:
        levels = levels[voiced[0]: voiced[-1] + 1]
    return Envelope(levels, fps, len(levels) / fps)


def speak_to_wav(text: str, path: str | None = None, rate: int = 1) -> str:
    """Synthesise `text` with the built-in Windows voice (System.Speech).

    Nothing is played. The WAV is written to a temp file and returned.
    """
    import hashlib

    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
    path = path or os.path.join(tempfile.gettempdir(), f"jevis_tts_{digest}.wav")
    script = (
        "Add-Type -AssemblyName System.Speech;"
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        f"$s.Rate = {int(rate)};"
        f"$s.SetOutputToWaveFile('{path}');"
        "$s.Speak([Console]::In.ReadToEnd());"
        "$s.Dispose()"
    )
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        input=text, text=True, check=True, capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return path


def play(path: str) -> None:
    """Play a WAV asynchronously. Only used when the demo is asked to --speak."""
    import winsound

    threading.Thread(
        target=lambda: winsound.PlaySound(path, winsound.SND_FILENAME), daemon=True
    ).start()
