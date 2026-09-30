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

subprocess.run(['clear'], capture_output=True, text=True, check=True)

# Pfad zu deinem optimierten Skript
ASCII_SCRIPT = "/home/cell0r/.local/bin/asciiimage.py"

def get_video_info(video_path):
    """Extrahiert FPS aus dem Video mittels ffprobe."""
    cmd = [
        'ffprobe', '-v', 'error', '-select_streams', 'v:0',
        '-show_entries', 'stream=r_frame_rate', '-of', 'default=noprint_wrappers=1:nokey=1',
        video_path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        num, den = result.stdout.strip().split('/')
        return float(num) / float(den)
    except Exception:
        return 24.0

def get_audio_player():
    """Findet den verfuegbaren Audio-Player."""
    for player in ['paplay', 'aplay']:
        if shutil.which(player):
            return player
    return None

def convert_frame(frame_path, width):
    """Ein einzelner Worker-Job: Konvertiert einen Frame zu einem String."""
    try:
        result = subprocess.run(
            ["python", ASCII_SCRIPT, frame_path, str(width)],
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout
    except Exception as e:
        return f"Error converting {frame_path}: {e}"

def play_asciivideo(video_path, width=127, buffer_size=60, target_fps=None):
    if not os.path.exists(video_path):
        print(f"Error: Datei {video_path} nicht gefunden.")
        return

    audio_player = get_audio_player()
    if not audio_player:
        print("[!] Warnung: Kein Audio-Player (paplay/aplay) gefunden. Nur Video-Modus.")

    src_fps = get_video_info(video_path)

    # Optional: Framerate downsamplen (z.B. 30 -> 15). Jeder n-te Frame wird
    # gezeigt, das Audio laeuft unveraendert weiter -> bleibt synchron.
    if target_fps and 0 < target_fps < src_fps:
        step = max(1, round(src_fps / target_fps))
        fps = src_fps / step
    else:
        step = 1
        fps = src_fps
    frame_delay = 1.0 / fps

    print(f"[*] Video erkannt: {src_fps:.2f} FPS"
          + (f" -> wiedergegeben mit {fps:.2f} FPS (jeder {step}. Frame)" if step > 1 else ""))
    print("[*] Starte Vorbereitung...")
    tmp_dir = tempfile.mkdtemp(prefix="asciivideo_")
    audio_file = os.path.join(tmp_dir, "audio.wav")

    try:
        # 1. Extraktion von Frames UND Audio via FFmpeg
        print(f"[*] Extrahiere Frames (Breite {width}) und Audio...")

        frame_cmd = [
            'ffmpeg', '-i', video_path,
            '-vf', f'scale={width}:-1',
            '-q:v', '2',
            f'{tmp_dir}/frame_%05d.jpg', '-y', '-loglevel', 'quiet'
        ]
        subprocess.run(frame_cmd, check=True)

        audio_cmd = [
            'ffmpeg', '-i', video_path, '-vn',
            '-acodec', 'pcm_s16le', '-ar', '44100', '-ac', '2',
            audio_file, '-y', '-loglevel', 'quiet'
        ]
        subprocess.run(audio_cmd, check=True)

        frames = sorted(glob.glob(f"{tmp_dir}/frame_*.jpg"))
        if not frames:
            print("Error: Keine Frames extrahiert.")
            return
        # Downsampling: nur jeden step-ten Frame behalten
        frames = frames[::step]

        print(f"[*] Vorbereitung fertig ({len(frames)} Frames).")
        print("[*] Starte Playback. Druecke STRG+C zum Stoppen.")
        time.sleep(1)

        # 2. Setup fuer Buffering
        frame_queue = queue.Queue(maxsize=buffer_size)
        stop_event = threading.Event()
        audio_proc = None

        def producer():
            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = [executor.submit(convert_frame, f, width) for f in frames]
                for future in futures:
                    if stop_event.is_set():
                        break
                    frame_queue.put(future.result())

        producer_thread = threading.Thread(target=producer)
        producer_thread.start()

        # 3. Playback Loop (Consumer) mit absoluter Zeitachse
        HOME_CURSOR = "\033[H"
        start_time = None
        frame_index = 0
        dropped = 0

        # Puffer vorfuellen, damit Audio nicht auf leeren Buffer trifft
        PREBUFFER = min(10, buffer_size // 2)

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
                    # Erst starten, wenn genug Frames im Puffer sind
                    if frame_queue.qsize() < PREBUFFER and producer_thread.is_alive():
                        # Frame zuruecklegen ist umstaendlich -> einfach kurz warten
                        # und den Frame schon anzeigen, aber Clock noch nicht starten
                        pass
                    start_time = time.perf_counter()
                    if audio_player and os.path.exists(audio_file):
                        audio_proc = subprocess.Popen([audio_player, audio_file])

                # Absolute Soll-Zeit dieses Frames relativ zur Audio-Clock
                deadline = start_time + frame_index * frame_delay
                now = time.perf_counter()

                if now > deadline + frame_delay:
                    # Wir hinken mehr als einen Frame hinterher:
                    # Frame droppen, um wieder aufzuholen (Audio bleibt Master)
                    dropped += 1
                    frame_index += 1
                    frame_queue.task_done()
                    continue

                # Exakt bis zur Soll-Zeit schlafen (Renderzeit wird abgezogen)
                sleep_time = deadline - now
                if sleep_time > 0:
                    time.sleep(sleep_time)

                sys.stdout.write(HOME_CURSOR)
                sys.stdout.write(frame_data)
                sys.stdout.flush()

                frame_index += 1
                frame_queue.task_done()

        except KeyboardInterrupt:
            stop_event.set()
            print("\n[*] Abbruch durch User.")

        # Abschluss-Logik
        end_time = time.perf_counter()
        if audio_proc:
            audio_proc.terminate()
            try:
                audio_proc.wait(timeout=1)
            except Exception:
                audio_proc.kill()

        if start_time:
            duration = end_time - start_time
            minutes = int(duration // 60)
            seconds = int(duration % 60)
            print(f"\n[OK] Playback beendet.")
            print(f"[i] Gesamtdauer: {minutes} min und {seconds} sek")
            if dropped:
                print(f"[i] {dropped} Frames gedroppt, um synchron zu bleiben.")

        producer_thread.join()

    except Exception as e:
        print(f"\n[!] Fehler: {e}")
    finally:
        stop_event.set()
        print(f"[*] Bereinige {tmp_dir}...")
        shutil.rmtree(tmp_dir)
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
