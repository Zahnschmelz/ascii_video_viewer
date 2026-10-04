#!/usr/bin/env python3

import subprocess
import os
import sys
import time
import tempfile
import glob
import shutil
import queue
import threading
from concurrent.futures import ThreadPoolExecutor


try:
    subprocess.run(["clear"], capture_output=True, text=True, check=True)
except Exception:
    pass


# Pfad zu deinem optimierten Skript
ASCII_SCRIPT = "/home/cell0r/.local/bin/asciiimage.py"


def get_video_info(video_path):
    """Extrahiert FPS aus dem Video mittels ffprobe."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=r_frame_rate",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        num, den = result.stdout.strip().split("/")
        return float(num) / float(den)
    except Exception:
        return 24.0


def has_audio_stream(video_path):
    """Prueft, ob die Videodatei einen Audiostream besitzt."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "a",
        "-show_entries", "stream=index",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return bool(result.stdout.strip())
    except Exception:
        # Wenn ffprobe fehlschlägt, versuchen wir später trotzdem die Audio-Extraktion.
        return True


def get_audio_player():
    """Findet den verfuegbaren Audio-Player."""
    for player in ["paplay", "aplay"]:
        if shutil.which(player):
            return player
    return None


def convert_frame(frame_path, width):
    """Ein einzelner Worker-Job: Konvertiert einen Frame zu einem String."""
    try:
        result = subprocess.run(
            [sys.executable, ASCII_SCRIPT, frame_path, str(width)],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout
    except Exception as e:
        return f"Error converting {frame_path}: {e}"


def play_asciivideo(video_path, width=127, buffer_size=60, target_fps=None):
    if not os.path.exists(video_path):
        print(f"Error: Datei {video_path} nicht gefunden.")
        return

    if not os.path.exists(ASCII_SCRIPT):
        print(f"Error: ASCII-Skript nicht gefunden: {ASCII_SCRIPT}")
        return

    audio_player = get_audio_player()
    if not audio_player:
        print("[!] Warnung: Kein Audio-Player (paplay/aplay) gefunden. Nur Video-Modus.")

    has_audio = has_audio_stream(video_path)
    if not has_audio:
        print("no audio detected, playing without sound")

    use_audio = bool(audio_player and has_audio)

    src_fps = get_video_info(video_path)

    # Optional: Framerate downsamplen (z.B. 30 -> 15). Jeder n-te Frame wird
    # gezeigt. Wenn Audio läuft, bleibt die Audio-Zeitachse Master.
    if target_fps and 0 < target_fps < src_fps:
        step = max(1, round(src_fps / target_fps))
        fps = src_fps / step
    else:
        step = 1
        fps = src_fps

    frame_delay = 1.0 / fps

    print(
        f"[*] Video erkannt: {src_fps:.2f} FPS"
        + (f" -> wiedergegeben mit {fps:.2f} FPS (jeder {step}. Frame)" if step > 1 else "")
    )
    print("[*] Starte Vorbereitung...")

    tmp_dir = tempfile.mkdtemp(prefix="asciivideo_")
    audio_file = os.path.join(tmp_dir, "audio.wav")

    stop_event = threading.Event()
    producer_thread = None
    audio_proc = None
    frame_queue = None
    start_time = None
    dropped = 0

    try:
        if use_audio:
            print(f"[*] Extrahiere Frames (Breite {width}) und Audio...")
        else:
            print(f"[*] Extrahiere Frames (Breite {width})...")

        frame_cmd = [
            "ffmpeg",
            "-nostdin",
            "-i", video_path,
            "-vf", f"scale={width}:-1",
            "-q:v", "2",
            f"{tmp_dir}/frame_%05d.jpg",
            "-y",
            "-loglevel", "quiet",
        ]
        subprocess.run(frame_cmd, capture_output=True, text=True, check=True)

        if use_audio:
            audio_cmd = [
                "ffmpeg",
                "-nostdin",
                "-i", video_path,
                "-vn",
                "-acodec", "pcm_s16le",
                "-ar", "44100",
                "-ac", "2",
                audio_file,
                "-y",
                "-loglevel", "quiet",
            ]

            try:
                subprocess.run(audio_cmd, capture_output=True, text=True, check=True)

                if not os.path.exists(audio_file) or os.path.getsize(audio_file) == 0:
                    use_audio = False
                    print("no audio detected, playing without sound")

            except subprocess.CalledProcessError:
                use_audio = False
                print("no audio detected, playing without sound")

        frames = sorted(glob.glob(f"{tmp_dir}/frame_*.jpg"))
        if not frames:
            print("Error: Keine Frames extrahiert.")
            return

        # Downsampling: nur jeden step-ten Frame behalten
        frames = frames[::step]

        print(f"[*] Vorbereitung fertig ({len(frames)} Frames).")
        print("[*] Starte Playback. Druecke STRG+C zum Stoppen.")
        time.sleep(1)

        frame_queue = queue.Queue(maxsize=buffer_size)

        def producer():
            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = [executor.submit(convert_frame, f, width) for f in frames]

                for future in futures:
                    if stop_event.is_set():
                        break

                    try:
                        frame_data = future.result()
                    except Exception as e:
                        frame_data = f"Error converting frame: {e}"

                    while not stop_event.is_set():
                        try:
                            frame_queue.put(frame_data, timeout=0.2)
                            break
                        except queue.Full:
                            continue

        producer_thread = threading.Thread(target=producer, daemon=True)
        producer_thread.start()

        HOME_CURSOR = "\033[H"
        frame_index = 0
        next_frame_time = None

        # Puffer tatsächlich vorfüllen, aber nur wenn Audio als Master genutzt wird.
        if use_audio:
            PREBUFFER = min(10, buffer_size // 2)
            while producer_thread.is_alive() and frame_queue.qsize() < PREBUFFER:
                time.sleep(0.05)

        try:
            while True:
                try:
                    frame_data = frame_queue.get(timeout=2.0)
                except queue.Empty:
                    if producer_thread.is_alive():
                        continue
                    else:
                        break

                if start_time is None:
                    start_time = time.perf_counter()

                    if (
                        use_audio
                        and audio_player
                        and os.path.exists(audio_file)
                        and os.path.getsize(audio_file) > 0
                    ):
                        try:
                            audio_proc = subprocess.Popen(
                                [audio_player, audio_file],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                            )
                        except Exception:
                            audio_proc = None
                            use_audio = False
                            print("no audio detected, playing without sound")

                audio_is_running = (
                    use_audio
                    and audio_proc is not None
                    and audio_proc.poll() is None
                )

                if audio_is_running:
                    # Audio ist Master: absolute Zeitachse und Frame-Drop erlaubt.
                    deadline = start_time + frame_index * frame_delay
                    now = time.perf_counter()

                    if now > deadline + frame_delay:
                        dropped += 1
                        frame_index += 1
                        frame_queue.task_done()
                        continue

                    sleep_time = deadline - now
                    if sleep_time > 0:
                        time.sleep(sleep_time)

                else:
                    # Ohne Audio: kein Frame-Drop.
                    # Stattdessen einfache framebasierte Wiedergabe.
                    now = time.perf_counter()

                    if next_frame_time is None:
                        next_frame_time = now
                    else:
                        sleep_time = next_frame_time - now
                        if sleep_time > 0:
                            time.sleep(sleep_time)

                sys.stdout.write(HOME_CURSOR)
                sys.stdout.write(frame_data)
                sys.stdout.flush()

                if not audio_is_running:
                    next_frame_time = max(
                        next_frame_time + frame_delay,
                        time.perf_counter(),
                    )

                frame_index += 1
                frame_queue.task_done()

        except KeyboardInterrupt:
            stop_event.set()
            print("\n[*] Abbruch durch User.")

    except KeyboardInterrupt:
        stop_event.set()
        print("\n[*] Abbruch durch User.")

    except Exception as e:
        print(f"\n[!] Fehler: {e}")

    finally:
        if start_time:
            end_time = time.perf_counter()
            duration = end_time - start_time
            minutes = int(duration // 60)
            seconds = int(duration % 60)

            print("\n[OK] Playback beendet.")
            print(f"[i] Gesamtdauer: {minutes} min und {seconds} sek")

            if dropped:
                print(f"[i] {dropped} Frames gedroppt, um synchron zu bleiben.")

        if audio_proc:
            try:
                audio_proc.terminate()
                audio_proc.wait(timeout=1)
            except Exception:
                try:
                    audio_proc.kill()
                except Exception:
                    pass

        stop_event.set()

        # Queue leeren, damit der Producer sauber beendet werden kann.
        if frame_queue is not None:
            try:
                while True:
                    frame_queue.get_nowait()
                    frame_queue.task_done()
            except queue.Empty:
                pass
            except Exception:
                pass

        if producer_thread is not None:
            producer_thread.join(timeout=3)

        print(f"[*] Bereinige {tmp_dir}...")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        print("[*] Fertig.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python asciivideo.py <video_path> [width] [fps]")
        print("  width: Terminal-Breite in Zeichen (Default: 127)")
        print("  fps:   optionale Ziel-Framerate, z.B. 15 (Default: Original-FPS)")
    else:
        vid = sys.argv[1]
        w = int(sys.argv[2]) if len(sys.argv) > 2 else 127
        f = float(sys.argv[3]) if len(sys.argv) > 3 else None
        play_asciivideo(vid, w, target_fps=f)
