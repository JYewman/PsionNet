"""PCM in, MP3 out, to every listening netBook Pro, in real time.

librespot's pipe backend writes raw PCM (S16LE, 44.1 kHz, stereo) as fast as
the pipe will take it, and counts its own playback position from what it has
written. So the reader here sets the pace: it takes exactly one second of
audio per second of wall-clock time. Read faster and librespot would race
through a track in seconds; read slower and Spotify's position would lag.

When librespot has nothing to say (paused, between tracks, buffering), the
pump encodes silence instead, so the HTTP stream never stalls: a GStreamer
0.8 pipeline on the device starved for long enough gives up.
"""

import os
import queue
import threading
import time

RATE = 44100
CHANNELS = 2
BYTES_PER_SEC = RATE * CHANNELS * 2
CHUNK_SECONDS = 0.1
CHUNK = int(BYTES_PER_SEC * CHUNK_SECONDS) // 4 * 4
LEAD = 0.6            # seconds the stream may run ahead of real time
BITRATE = 128         # kbit/s: comfortable for libmad on a 400 MHz XScale


# MPEG-1 Layer III: kbit/s by header index, and sample rates
_BITRATES = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0)
_RATES = (44100, 48000, 32000, 0)


def frame_length(h: bytes) -> int:
    """Length of the MPEG-1 Layer III frame whose 4-byte header is h, else 0."""
    if len(h) < 4 or h[0] != 0xFF or (h[1] & 0xFE) != 0xFA:
        return 0
    kbps, rate = _BITRATES[h[2] >> 4], _RATES[(h[2] >> 2) & 3]
    if not kbps or not rate:
        return 0
    return 144 * kbps * 1000 // rate + ((h[2] >> 1) & 1)


class Encoder:
    """PCM in, whole MP3 frames out.

    LAME hands back bytes in whatever lengths it likes, so a listener that
    joined between two of them -- or lost one when it fell behind -- would
    start partway through a frame. Every chunk this returns starts with a
    frame header and ends with a frame's last byte.
    """

    def __init__(self, bitrate: int = BITRATE):
        import lameenc
        self._e = lameenc.Encoder()
        self._e.set_bit_rate(bitrate)
        self._e.set_in_sample_rate(RATE)
        self._e.set_channels(CHANNELS)
        self._e.set_quality(5)
        self._buf = bytearray()

    def encode(self, pcm: bytes) -> bytes:
        if pcm:
            self._buf += self._e.encode(pcm)
        out, i, buf = bytearray(), 0, self._buf
        while len(buf) - i >= 4:
            n = frame_length(buf[i:i + 4])
            if not n:
                i += 1                  # not a header: resynchronise
                continue
            if len(buf) - i < n:
                break
            out += buf[i:i + n]
            i += n
        del buf[:i]
        return bytes(out)


class Broadcaster:
    """Fan one MP3 stream out to any number of HTTP clients.

    Each listener gets its own bounded queue. A listener that falls behind
    loses chunks rather than slowing everyone else down -- or slowing the
    pump, which would stall Spotify's playback position for all of them.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._clients: list[queue.Queue] = []

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=80)      # ~8 s of 128 kbit/s
        with self._lock:
            self._clients.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._clients:
                self._clients.remove(q)

    @property
    def listeners(self) -> int:
        with self._lock:
            return len(self._clients)

    def publish(self, data: bytes) -> None:
        if not data:
            return
        with self._lock:
            clients = list(self._clients)
        for q in clients:
            try:
                q.put_nowait(data)
            except queue.Full:
                try:
                    q.get_nowait()          # drop the oldest, keep the newest
                    q.put_nowait(data)
                except (queue.Empty, queue.Full):
                    pass


class _Reader:
    """Reads one pipe on a thread of its own and hands the bytes over.

    select() waits on pipes only on POSIX -- on Windows it takes sockets alone
    -- while a blocking read on a thread works everywhere. The queue is short,
    so librespot still blocks on a full pipe much as before, and the stream
    stays close to real time.
    """

    def __init__(self, src):
        self.src = src                  # kept, so the pipe stays open while read
        self.fd = src if isinstance(src, int) else src.fileno()
        self.q: queue.Queue = queue.Queue(maxsize=4)
        self.buf = b""
        self.done = False
        self.abandoned = False          # the pump has moved on to another pipe
        threading.Thread(target=self._run, daemon=True, name="spotify-pipe").start()

    def is_for(self, src) -> bool:
        return src is self.src or (isinstance(src, int) and src == self.src)

    def _run(self) -> None:
        while not self.abandoned:
            try:
                chunk = os.read(self.fd, 4096)
            except OSError:
                chunk = b""
            while not self.abandoned:
                try:
                    self.q.put(chunk, timeout=0.5)
                    break
                except queue.Full:
                    pass
            if not chunk:
                return

    def read(self, budget: int, wait: float) -> bytes:
        """Up to `budget` bytes, waiting at most `wait` seconds for the first."""
        if not self.buf:
            if self.done:
                return b""
            try:
                chunk = self.q.get(timeout=wait) if wait > 0 else self.q.get_nowait()
            except queue.Empty:
                return b""
            if not chunk:
                self.done = True
                return b""
            self.buf = chunk
        out, self.buf = self.buf[:budget], self.buf[budget:]
        return out


class Pump(threading.Thread):
    """Read PCM from a pipe at real-time pace and publish MP3.

    source() returns what to read -- a file descriptor or a pipe's file
    object -- or None when there is no source (librespot not running):
    silence is published either way.
    """

    def __init__(self, source, broadcaster: Broadcaster, encoder: Encoder | None = None):
        super().__init__(daemon=True, name="spotify-pump")
        self.source = source
        self.out = broadcaster
        self.enc = encoder or Encoder()
        self.stop_flag = threading.Event()
        self.audio_seconds = 0.0          # real audio published, for tests/status
        self.silence_seconds = 0.0
        self._pending = b""
        self._reader: _Reader | None = None

    def stop(self) -> None:
        self.stop_flag.set()

    def _read_some(self, src, budget: int, wait: float) -> bytes:
        """Up to `budget` bytes, waiting at most `wait` seconds for the first."""
        if self._reader is None or not self._reader.is_for(src):
            if self._reader is not None:
                self._reader.abandoned = True
            self._reader = _Reader(src)     # a new librespot: a new pipe
        return self._reader.read(budget, wait)

    def run(self) -> None:
        start = time.monotonic()
        sent = 0.0                      # seconds of audio published since start
        while not self.stop_flag.is_set():
            now = time.monotonic()
            ahead = (start + sent) - now
            if ahead > LEAD:
                time.sleep(min(ahead - LEAD, CHUNK_SECONDS))
                continue
            if ahead < -1.0:            # stalled (sleep, debugger): do not burst to catch up
                start, sent = now, 0.0

            src = self.source()
            pcm = b""
            if src is not None:
                want = CHUNK - len(self._pending)
                got = self._read_some(src, want, CHUNK_SECONDS)
                pcm = self._pending + got
                if got:
                    # top up to a whole chunk if librespot is writing steadily
                    while len(pcm) < CHUNK and not self.stop_flag.is_set():
                        more = self._read_some(src, CHUNK - len(pcm), 0.02)
                        if not more:
                            break
                        pcm += more
            else:
                time.sleep(CHUNK_SECONDS / 2)

            if pcm:
                whole = len(pcm) // 4 * 4                  # keep frames intact
                pcm, self._pending = pcm[:whole], pcm[whole:]
            # Less than one whole frame left over is not audio: without this,
            # 1-3 stray bytes and a quiet pipe published nothing and moved the
            # clock on not at all, and the stream stalled for good. (Windows
            # pipes split writes anywhere; POSIX ones happen to keep to 4.)
            if pcm:
                self.out.publish(self.enc.encode(pcm))
                secs = len(pcm) / BYTES_PER_SEC
                sent += secs
                self.audio_seconds += secs
            else:
                self.out.publish(self.enc.encode(b"\0" * CHUNK))
                sent += CHUNK_SECONDS
                self.silence_seconds += CHUNK_SECONDS
