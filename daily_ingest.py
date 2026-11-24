import os
import re
import uuid
import sqlite3
import logging
import subprocess
import feedparser
from dotenv import load_dotenv
from openai import OpenAI
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct

from crypto_utils import decrypt_token

# 1. Konfigurasi & Logging
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("ingest.log"),
        logging.StreamHandler()
    ]
)

CHANNEL_ID = "UCJHC3VbFsp7kJ2JsperlgevQ"  # @khalidbasalamah
RSS_URL = f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}"
MIN_DURATION_SEC = 900  # Minimal 15 menit (skip Shorts & potongan klip)
EMBEDDING_MODEL = "bge-m3"
EMBEDDING_DIM = int(os.getenv("KENARI_EMBEDDING_DIM", "1024"))

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "kajian_khalid_basalamah")
DB_PATH = "kajian_tracker.db"

kenari_client = OpenAI(
    api_key=decrypt_token(os.getenv("KENARI_API_KEY_ENCRYPTED", "")),
    base_url=os.getenv("KENARI_BASE_URL", "https://api.kenari.id/v1")
)
qdrant = QdrantClient(url=QDRANT_URL)


# 2. Inisialisasi Database SQLite & Qdrant Collection
def init_databases():
    # Setup SQLite untuk tracking video_id agar tidak diproses ulang
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS processed_videos (
            video_id TEXT PRIMARY KEY,
            title TEXT,
            duration_sec INTEGER,
            chunks_count INTEGER,
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()

    # Setup Qdrant Collection otomatis jika belum ada
    existing_collections = [c.name for c in qdrant.get_collections().collections]
    if COLLECTION_NAME not in existing_collections:
        logging.info(f"Membuat Qdrant collection baru: '{COLLECTION_NAME}'...")
        qdrant.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(
                size=EMBEDDING_DIM,
                distance=Distance.COSINE
            )
        )
        logging.info("Collection Qdrant berhasil dibuat.")
    else:
        logging.info(f"Qdrant collection '{COLLECTION_NAME}' sudah tersedia.")

    return conn


# 3. Helper Ekstraksi Metadata & Utilitas yt-dlp
def extract_kitab_from_title(title: str) -> str:
    """Mendeteksi nama kitab/seri dari pola judul video Ustadz Khalid."""
    patterns = [
        r"(Kitab\s+[^-\|]+)",
        r"(Sirah\s+Nabawiyah[^-\|]*)",
        r"(Bulughul\s+Maram[^-\|]*)",
        r"(Riyadhus\s+Shalihin[^-\|]*)",
        r"(Minhajul\s+Muslim[^-\|]*)",
        r"(Shahih\s+Bukhari[^-\|]*)",
        r"(Dosa-Dosa\s+Besar[^-\|]*)"
    ]
    for pattern in patterns:
        match = re.search(pattern, title, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return "Kajian Tematik / Umum"


def get_video_duration(url: str) -> int:
    """Mengambil durasi video dalam detik tanpa mengunduh video."""
    cmd = ["yt-dlp", "--get-duration", "--no-warnings", url]
    res = subprocess.run(cmd, capture_output=True, text=True)
    raw = res.stdout.strip()
    if not raw:
        return 0
    parts = raw.split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        return int(parts[0])
    except ValueError:
        return 0


def download_compressed_audio(video_id: str, url: str) -> str:
    """Mengunduh audio saja & mengompres ke Mono 16kHz MP3 (aman <25MB untuk Whisper)."""
    output_path = f"/tmp/{video_id}.mp3"
    if os.path.exists(output_path):
        os.remove(output_path)

    cmd = [
        "yt-dlp",
        "-x",
        "--audio-format", "mp3",
        "--postprocessor-args", "ffmpeg:-ac 1 -ar 16000 -b:a 32k",
        "--no-warnings",
        "-o", output_path,
        url
    ]
    subprocess.run(cmd, check=True)
    return output_path


# 4. Transkripsi & Semantic Chunking
def transcribe_audio(audio_path: str):
    """Mentranskrip audio ke teks bahasa Indonesia beserta timestamp tiap segmen."""
    file_size_mb = os.path.getsize(audio_path) / (1024 * 1024)
    logging.info(f"Mengirim audio ke Whisper API (Ukuran: {file_size_mb:.2f} MB)...")

    with open(audio_path, "rb") as audio_file:
        response = kenari_client.audio.transcriptions.create(
            model=os.getenv("KENARI_TRANSCRIPTION_MODEL", "whisper-large-v3-turbo"),
            file=audio_file,
            language="id",
            prompt="Kajian Islam Ustadz Khalid Basalamah, dalil Al-Quran, hadits shahih, kitab fiqih, sirah nabawiyah.",
            response_format="verbose_json",
            timestamp_granularities=["segment"]
        )
    segments = getattr(response, "segments", None)
    if not segments:
        raise RuntimeError("Kenari transcription did not return timestamped segments")
    return segments


def create_chunks_with_metadata(segments, max_chars: int = 1500, overlap_segments: int = 2):
    """Menggabungkan segmen Whisper menjadi potongan teks bermakna + deteksi sesi Tanya Jawab."""
    chunks = []
    current_segments = []
    current_length = 0
    is_qa_session = False

    qa_triggers = ["sesi tanya jawab", "pertanyaan pertama", "kita baca pertanyaan", "silakan yang mau bertanya"]

    for seg in segments:
        text_clean = seg.text.strip()
        if not text_clean:
            continue

        # Deteksi transisi masuk ke sesi tanya jawab
        if not is_qa_session and any(trigger in text_clean.lower() for trigger in qa_triggers):
            is_qa_session = True

        current_segments.append(seg)
        current_length += len(text_clean) + 1

        if current_length >= max_chars:
            start_sec = int(current_segments[0].start)
            end_sec = int(current_segments[-1].end)
            combined_text = " ".join(s.text.strip() for s in current_segments)

            chunks.append({
                "text": combined_text,
                "start_sec": start_sec,
                "end_sec": end_sec,
                "tipe_segmen": "tanya_jawab" if is_qa_session else "materi_inti"
            })

            # Simpan sedikit overlap dari segmen terakhir agar konteks tidak terputus
            current_segments = current_segments[-overlap_segments:] if overlap_segments > 0 else []
            current_length = sum(len(s.text.strip()) + 1 for s in current_segments)

    # Sisa segmen terakhir
    if current_segments:
        start_sec = int(current_segments[0].start)
        end_sec = int(current_segments[-1].end)
        combined_text = " ".join(s.text.strip() for s in current_segments)
        chunks.append({
            "text": combined_text,
            "start_sec": start_sec,
            "end_sec": end_sec,
            "tipe_segmen": "tanya_jawab" if is_qa_session else "materi_inti"
        })

    return chunks


# 5. Proses Embedding & Upload ke Qdrant
def embed_and_upsert(video_id: str, title: str, published_at: str, chunks: list):
    kitab_name = extract_kitab_from_title(title)
    texts = [c["text"] for c in chunks]

    # Batch embedding agar jauh lebih cepat & hemat API call
    emb_response = kenari_client.embeddings.create(
        input=texts,
        model=os.getenv("KENARI_EMBEDDING_MODEL", EMBEDDING_MODEL)
    )

    points = []
    for idx, chunk in enumerate(chunks):
        vector = emb_response.data[idx].embedding
        start_sec = chunk["start_sec"]

        points.append(PointStruct(
            id=str(uuid.uuid4()),
            vector=vector,
            payload={
                "video_id": video_id,
                "title": title,
                "kitab": kitab_name,
                "published_at": published_at,
                "tipe_segmen": chunk["tipe_segmen"],
                "start_sec": start_sec,
                "end_sec": chunk["end_sec"],
                "youtube_url": f"https://youtu.be/{video_id}?t={start_sec}",
                "text": chunk["text"]
            }
        ))

    qdrant.upsert(collection_name=COLLECTION_NAME, points=points)


# 6. Main Daily Pipeline
def run_daily_pipeline():
    conn = init_databases()
    logging.info("Mengecek RSS Feed channel @khalidbasalamah...")
    feed = feedparser.parse(RSS_URL)

    if not feed.entries:
        logging.warning("Tidak ada data ditemukan dari RSS Feed.")
        return

    new_videos_count = 0

    # Balik urutan agar video terlama diproses lebih dulu
    for entry in reversed(feed.entries):
        video_id = entry.yt_videoid
        title = entry.title
        url = entry.link
        published_at = entry.get("published", "")

        # Cek database SQLite
        row = conn.execute("SELECT 1 FROM processed_videos WHERE video_id = ?", (video_id,)).fetchone()
        if row:
            continue

        # Cek durasi video
        duration = get_video_duration(url)
        if duration == 0:
            logging.info(f"Skip (video live/upcoming atau belum tersedia): {title}")
            continue

        if duration < MIN_DURATION_SEC:
            logging.info(f"Skip (durasi {duration}s < 15 menit): {title}")
            # Catat ke DB dengan chunks_count=0 agar besok tidak dicek ulang
            conn.execute(
                "INSERT INTO processed_videos (video_id, title, duration_sec, chunks_count) VALUES (?, ?, ?, ?)",
                (video_id, title, duration, 0)
            )
            conn.commit()
            continue

        logging.info(f"=== Memproses Kajian Baru: {title} ({duration // 60} menit) ===")
        audio_path = None
        try:
            audio_path = download_compressed_audio(video_id, url)
            segments = transcribe_audio(audio_path)
            chunks = create_chunks_with_metadata(segments)
            embed_and_upsert(video_id, title, published_at, chunks)

            conn.execute(
                "INSERT INTO processed_videos (video_id, title, duration_sec, chunks_count) VALUES (?, ?, ?, ?)",
                (video_id, title, duration, len(chunks))
            )
            conn.commit()
            new_videos_count += 1
            logging.info(f"Berhasil mengindeks '{title}' -> {len(chunks)} chunks tersimpan di Qdrant.")

        except Exception as e:
            logging.error(f"Gagal memproses video {video_id} ({title}): {e}", exc_info=True)

        finally:
            if audio_path and os.path.exists(audio_path):
                os.remove(audio_path)

    logging.info(f"Selesai. Total kajian baru diproses hari ini: {new_videos_count}")
    conn.close()


if __name__ == "__main__":
    run_daily_pipeline()