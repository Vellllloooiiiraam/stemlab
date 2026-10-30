"""StemLab — configuração central e catálogo de modelos."""
from __future__ import annotations

import sys
from pathlib import Path

# Empacotado com PyInstaller (StemLab.exe) o código roda de dentro do pacote e
# os caminhos precisam ser resolvidos de outro jeito.
FROZEN = bool(getattr(sys, "frozen", False))

if FROZEN:
    # Pasta onde está o .exe (o pacote portátil inteiro).
    BASE_DIR = Path(sys.executable).resolve().parent
    # Recursos embutidos (web/, bin/) ficam em `_internal`.
    RUNTIME_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR))
    # Dados do usuário ficam ao lado do .exe, para não poluir o pacote.
    DATA_DIR = BASE_DIR / "StemLab dados"
else:
    BASE_DIR = Path(__file__).resolve().parent.parent
    RUNTIME_DIR = BASE_DIR
    DATA_DIR = BASE_DIR

UPLOADS_DIR = DATA_DIR / "uploads"
OUTPUT_DIR = DATA_DIR / "output"
JOBS_FILE = DATA_DIR / "jobs.json"
WEB_DIR = RUNTIME_DIR / "web"

# Binários que viajam junto do executável (ffmpeg).
BUNDLED_BIN = RUNTIME_DIR / "bin"

HOST = "127.0.0.1"
PORT = 8765

MAX_UPLOAD_MB = 800

ALLOWED_EXTENSIONS = {
    # áudio
    ".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".oga",
    ".opus", ".wma", ".aiff", ".aif",
    # contêineres de vídeo (só o áudio é usado na separação)
    ".mp4", ".mka", ".webm",
    # família MPEG: vídeo (program stream) e áudio MPEG Layer II
    ".mpeg", ".mpg", ".mpe", ".mp2", ".mpa",
}

# Rótulos em português para cada faixa (stem) que o Demucs produz.
STEM_LABELS = {
    "vocals": "Voz",
    "drums": "Bateria",
    "bass": "Baixo",
    "other": "Outros",
    "guitar": "Guitarra",
    "piano": "Piano",
    "no_vocals": "Sem voz (playback)",
}

STEM_ORDER = ["vocals", "drums", "bass", "guitar", "piano", "other", "no_vocals"]

FOUR_STEMS = ["vocals", "drums", "bass", "other"]
SIX_STEMS = ["vocals", "drums", "bass", "guitar", "piano", "other"]

# Catálogo real de modelos disponíveis no Demucs 4.1 (`demucs --list-models`).
# "factor" é usado só para estimativa de tempo relativo.
MODELS: list[dict] = [
    {
        "id": "htdemucs",
        "label": "HT Demucs — 4 faixas",
        "stems": FOUR_STEMS,
        "note": "Padrão. Melhor equilíbrio entre qualidade e tempo.",
        "factor": 1.0,
        "tag": "recomendado",
    },
    {
        "id": "htdemucs_ft",
        "label": "HT Demucs FT — 4 faixas (alta qualidade)",
        "stems": FOUR_STEMS,
        "note": "Qualidade superior. Bem mais lento (cerca de 4x).",
        "factor": 4.0,
        "tag": "lento",
    },
    {
        "id": "htdemucs_6s",
        "label": "HT Demucs 6S — 6 faixas",
        "stems": SIX_STEMS,
        "note": "Separa também guitarra e piano, porém com qualidade menor nesses dois.",
        "factor": 1.15,
        "tag": "",
    },
    {
        "id": "hdemucs_mmi",
        "label": "Hybrid Demucs MMI — 4 faixas",
        "stems": FOUR_STEMS,
        "note": "Geração anterior do modelo híbrido.",
        "factor": 1.0,
        "tag": "",
    },
    {
        "id": "mdx_extra",
        "label": "MDX Extra — 4 faixas",
        "stems": FOUR_STEMS,
        "note": "Alternativa da família MDX, boa qualidade.",
        "factor": 0.85,
        "tag": "",
    },
    {
        "id": "mdx_extra_q",
        "label": "MDX Extra Q — 4 faixas (rápido)",
        "stems": FOUR_STEMS,
        "note": "Versão quantizada do MDX Extra.",
        "factor": 0.6,
        "tag": "rápido",
    },
    {
        "id": "mdx_q",
        "label": "MDX Q — 4 faixas (mais rápido)",
        "stems": FOUR_STEMS,
        "note": "O mais rápido do pacote. Qualidade abaixo do HT Demucs.",
        "factor": 0.45,
        "tag": "rápido",
    },
]

MODEL_BY_ID = {m["id"]: m for m in MODELS}

# Modelos que são conjuntos (bag) de 4 redes treinadas -> 4 passadas sequenciais.
BAG_MODELS = {"htdemucs_ft"}


def public_models() -> list[dict]:
    """Catálogo exposto à interface (sem campos internos)."""
    out = []
    for m in MODELS:
        out.append({
            "id": m["id"],
            "label": m["label"],
            "stems": [
                {"id": s, "label": STEM_LABELS.get(s, s)} for s in m["stems"]
            ],
            "note": m["note"],
            "tag": m["tag"],
        })
    return out