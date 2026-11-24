import os
import re
import glob
import time
import subprocess
import webvtt
from dotenv import load_dotenv
from openai import OpenAI

from crypto_utils import decrypt_token

load_dotenv()

# Ganti dengan URL 1 video kajian Ustadz Khalid Basalamah yang ingin diuji
TEST_VIDEO_URL = "https://www.youtube.com/watch?v=Z8xLi69p8dY"

client = OpenAI(
    api_key=decrypt_token(os.getenv("KENARI_API_KEY_ENCRYPTED", "")),
    base_url=os.getenv("KENARI_BASE_URL", "https://kenari.id/v1")
)

def extract_video_id(url: str) -> str:
    match = re.search(r"(?:v=|youtu\.be/)([a-zA-Z0-9_-]{11})", url)
    return match.group(1) if match else "test_video"

def test_youtube_subtitle(video_id: str, url: str):
    print("\n--- [1] Mengambil Auto-Subtitle Bawaan YouTube ---")
    start_time = time.time()
    output_template = f"/tmp/{video_id}_sub"
    
    cmd = [
        "yt-dlp",
        "--write-auto-subs",
        "--sub-lang", "id",
        "--sub-format", "vtt",
        "--skip-download",
        "--no-warnings",
        "-o", output_template,
        url
    ]
    subprocess.run(cmd, check=True)
    
    vtt_files = glob.glob(f"/tmp/{video_id}_sub*.vtt")
    if not vtt_files:
        print("❌ Subtitle otomatis YouTube tidak tersedia untuk video ini.")
        return

    lines = []
    for caption in webvtt.read(vtt_files[0]):
        clean_text = re.sub(r"<[^>]+>", "", caption.text).strip()
        clean_text = " ".join(clean_text.splitlines())
        if clean_text and (not lines or lines[-1][1] != clean_text):
            lines.append((caption.start.split(".")[0], clean_text))
            
    os.remove(vtt_files[0])
    
    with open("hasil_youtube_sub.txt", "w", encoding="utf-8") as f:
        for ts, text in lines:
            f.write(f"[{ts}] {text}\n")
            
    elapsed = time.time() - start_time
    print(f"✅ Selesai dalam {elapsed:.2f} detik | Biaya: Rp 0")
    print("📄 Tersimpan di: hasil_youtube_sub.txt")

def test_whisper_kenari(video_id: str, url: str):
    print("\n--- [2] Mengunduh Audio & Transkrip via Kenari.id (whisper-large-v3-turbo) ---")
    start_time = time.time()
    audio_path = f"/tmp/{video_id}_audio.mp3"
    
    # 1. Unduh audio saja (kompres 16kHz mono agar ringan)
    cmd = [
        "yt-dlp",
        "-f", "bestaudio[protocol=https]",
        "-x",
        "--audio-format", "mp3",
        "--postprocessor-args", "ffmpeg:-ac 1 -ar 16000 -b:a 32k",
        "--no-warnings",
        "-o", audio_path,
        url
    ]
    subprocess.run(cmd, check=True)
    
    # Hitung durasi audio untuk estimasi biaya (Rp 0,18 / detik)
    dur_cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", audio_path]
    duration_sec = float(subprocess.check_output(dur_cmd).decode().strip())
    est_cost = duration_sec * 0.18
    
    print(f"⏳ Mengirim audio ({duration_sec/60:.1f} menit) ke Kenari.id...")
    
    # 2. Kirim ke Whisper API Kenari.id
    with open(audio_path, "rb") as audio_file:
        # Menggunakan format 'vtt' atau 'verbose_json' agar mendapat timestamp
        transcript = client.audio.transcriptions.create(
            model="whisper-large-v3-turbo",
            file=audio_file,
            language="id",
            prompt="Kajian Islam Ustadz Khalid Basalamah, Al-Quran, hadits shahih, kitab fiqih, sirah nabawiyah.",
            response_format="verbose_json"
        )
        
    os.remove(audio_path)
    
    with open("hasil_whisper_kenari.txt", "w", encoding="utf-8") as f:
        # Cek apakah respons mengembalikan segments
        if hasattr(transcript, "segments") and transcript.segments:
            for seg in transcript.segments:
                mins, secs = divmod(int(seg.start), 60)
                hrs, mins = divmod(mins, 60)
                f.write(f"[{hrs:02d}:{mins:02d}:{secs:02d}] {seg.text.strip()}\n")
        else:
            # Fallback jika endpoint hanya mengembalikan teks penuh
            f.write(transcript.text)
            
    elapsed = time.time() - start_time
    print(f"✅ Selesai dalam {elapsed:.2f} detik | Estimasi Biaya: Rp {est_cost:,.0f}")
    print("📄 Tersimpan di: hasil_whisper_kenari.txt")

if __name__ == "__main__":
    if "VIDEO_ID_DISINI" in TEST_VIDEO_URL:
        print("⚠️ Ganti variabel TEST_VIDEO_URL terlebih dahulu dengan link video YouTube!")
    else:
        vid_id = extract_video_id(TEST_VIDEO_URL)
        test_youtube_subtitle(vid_id, TEST_VIDEO_URL)
        test_whisper_kenari(vid_id, TEST_VIDEO_URL)