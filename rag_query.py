import os
import sys

from dotenv import load_dotenv
from openai import OpenAI
from qdrant_client import QdrantClient

from crypto_utils import decrypt_token


load_dotenv()

COLLECTION_NAME = os.getenv("COLLECTION_NAME", "kajian_khalid_basalamah")
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")

kenari_client = OpenAI(
    api_key=decrypt_token(os.getenv("KENARI_API_KEY_ENCRYPTED", "")),
    base_url=os.getenv("KENARI_BASE_URL", "https://api.kenari.id/v1")
)
qdrant = QdrantClient(url=QDRANT_URL)


def format_timestamp(seconds: int) -> str:
    minutes, remaining_seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{remaining_seconds:02d}"


def search_context(question: str, limit: int = 5) -> list[dict]:
    embedding = kenari_client.embeddings.create(
        input=question,
        model=os.getenv("KENARI_EMBEDDING_MODEL", "bge-m3"),
    ).data[0].embedding

    results = qdrant.query_points(
        collection_name=COLLECTION_NAME,
        query=embedding,
        limit=limit,
    ).points

    contexts = []
    for result in results:
        payload = result.payload or {}
        contexts.append(
            {
                "text": payload.get("text", ""),
                "title": payload.get("title", ""),
                "start_sec": int(payload.get("start_sec", 0)),
                "youtube_url": payload.get("youtube_url", ""),
                "score": result.score,
            }
        )
    return contexts


def answer_question(question: str, contexts: list[dict]) -> str:
    if not contexts:
        return "Maaf, tidak ditemukan konteks yang relevan."

    source_text = "\n\n".join(
        f"[{index}] {context['title']} ({format_timestamp(context['start_sec'])})\n"
        f"{context['text']}\nSumber: {context['youtube_url']}"
        for index, context in enumerate(contexts, start=1)
    )
    response = kenari_client.chat.completions.create(
        model=os.getenv("KENARI_CHAT_MODEL", "kenari-free"),
        temperature=0,
        messages=[
            {
                "role": "system",
                "content": (
                    "Jawab berdasarkan konteks saja. Jika konteks tidak cukup, "
                    "katakan bahwa informasinya tidak ditemukan. Sertakan sumber "
                    "video dan timestamp yang relevan."
                ),
            },
            {
                "role": "user",
                "content": f"Pertanyaan:\n{question}\n\nKonteks:\n{source_text}",
            },
        ],
    )
    return response.choices[0].message.content or "Jawaban kosong."


def main() -> None:
    question = " ".join(sys.argv[1:]).strip()
    if not question:
        question = input("Masukkan pertanyaan: ").strip()
    if not question:
        raise SystemExit("Pertanyaan tidak boleh kosong.")

    contexts = search_context(question)
    print(answer_question(question, contexts))


if __name__ == "__main__":
    main()
