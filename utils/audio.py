"""
utils/audio.py - PermBot Audiophile DSP Sound Engine
High-Resolution 48kHz Resampling, Peak Limiting & Studio EQ Presets
"""

import os
import discord
from typing import Dict, Any, Optional, Tuple

try:
    import mutagen
except ImportError:
    mutagen = None

SUPPORTED_MEDIA_EXTENSIONS: Tuple[str, ...] = (
    ".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".opus", ".wma", ".alac", ".aiff"
)

# --- 64-BIT STUDIO AUDIO FILTER PRESETS ---
HQ_RESAMPLE = "aresample=48000:resample_cutoff=1.0:precision=28:osf=s16:dither_method=triangular"

EQ_PRESETS: Dict[str, str] = {
    "off": f"{HQ_RESAMPLE},alimiter=limit=0.98:attack=5:release=50:asc=1",
    "studio": f"{HQ_RESAMPLE},equalizer=f=60:width_type=q:w=1.2:g=2.5,equalizer=f=280:width_type=q:w=1.5:g=-1.8,equalizer=f=3200:width_type=q:w=1.1:g=2.0,equalizer=f=12000:width_type=q:w=1.0:g=3.0,alimiter=limit=0.98:attack=5:release=50:asc=1",
    "bass": f"{HQ_RESAMPLE},equalizer=f=55:width_type=q:w=1.1:g=6.5,equalizer=f=110:width_type=q:w=1.0:g=4.0,equalizer=f=300:width_type=q:w=1.5:g=-2.5,volume=0.88,alimiter=limit=0.98:attack=5:release=60:asc=1",
    "heavybass": f"{HQ_RESAMPLE},equalizer=f=45:width_type=q:w=1.0:g=9.5,equalizer=f=95:width_type=q:w=1.2:g=6.5,equalizer=f=320:width_type=q:w=1.5:g=-3.5,volume=0.76,alimiter=limit=0.98:attack=4:release=80:asc=1",
    "vocal": f"{HQ_RESAMPLE},highpass=f=85,equalizer=f=300:width_type=q:w=1.5:g=-2.5,equalizer=f=2800:width_type=q:w=1.2:g=4.5,equalizer=f=6500:width_type=q:w=1.3:g=3.0,alimiter=limit=0.98:attack=5:release=50:asc=1",
    "treble": f"{HQ_RESAMPLE},highpass=f=100,equalizer=f=3800:width_type=q:w=1.0:g=3.5,equalizer=f=10000:width_type=q:w=1.0:g=6.0,volume=0.90,alimiter=limit=0.98:attack=5:release=50:asc=1",
    "electronic": f"{HQ_RESAMPLE},equalizer=f=50:width_type=q:w=1.2:g=7.0,equalizer=f=125:width_type=q:w=1.0:g=3.5,equalizer=f=500:width_type=q:w=1.5:g=-2.0,equalizer=f=5500:width_type=q:w=1.2:g=3.5,equalizer=f=13000:width_type=q:w=1.0:g=4.0,volume=0.85,alimiter=limit=0.98:attack=5:release=50:asc=1",
    "rock": f"{HQ_RESAMPLE},equalizer=f=75:width_type=q:w=1.0:g=4.0,equalizer=f=2400:width_type=q:w=1.2:g=3.5,equalizer=f=7000:width_type=q:w=1.0:g=3.0,alimiter=limit=0.98:attack=5:release=50:asc=1",
    "nightcore": f"asetrate=48000*1.25,{HQ_RESAMPLE},equalizer=f=10000:width_type=q:w=1.0:g=3.0,alimiter=limit=0.98:attack=5:release=50:asc=1",
    "vaporwave": f"asetrate=48000*0.82,{HQ_RESAMPLE},equalizer=f=350:width_type=q:w=1.5:g=3.0,equalizer=f=4000:width_type=q:w=1.5:g=-4.0,aecho=0.8:0.88:60:0.4,alimiter=limit=0.98:attack=5:release=50:asc=1",
    "8d": f"{HQ_RESAMPLE},apulsator=hz=0.125:amount=1:offset_l=0:offset_r=0.5:width=1,alimiter=limit=0.98:attack=5:release=50:asc=1",
    "normalized": f"{HQ_RESAMPLE},dynaudnorm=f=150:g=15:m=8:r=0.9:b=1,alimiter=limit=0.98:attack=5:release=50:asc=1"
}

EQ_DESCRIPTIONS: Dict[str, str] = {
    "off": "Flat pure pass-through with anti-clipping limiter.",
    "studio": "Audiophile master with wide stereo soundstage & crystal clarity.",
    "bass": "Punchy sub-bass & kick with clean low-mids.",
    "heavybass": "Subwoofer bass drive with automatic peak control.",
    "vocal": "Acoustic & vocal speech clarity (cuts low-end mud).",
    "treble": "Airy top-end sparkle and open highs.",
    "electronic": "EDM curve with punchy kicks and bright synths.",
    "rock": "Warm punchy rhythm bass and driving lead mids.",
    "nightcore": "+25% Speed & Pitch with high-frequency polish.",
    "vaporwave": "-18% Slowed & Reverb analog lo-fi texture.",
    "8d": "360-degree binaural surround audio.",
    "normalized": "Dynamic volume leveling across quiet and loud tracks."
}


def create_audio_source(
    url_or_path: str,
    is_local: bool = False,
    eq_preset: str = "off",
    volume: float = 1.0,
    seek_seconds: float = 0.0
) -> discord.AudioSource:
    is_remote = url_or_path.startswith("http://") or url_or_path.startswith("https://")

    before_opts = []
    if seek_seconds > 0:
        before_opts.append(f"-ss {seek_seconds:.2f}")

    if is_remote:
        before_opts.extend([
            "-reconnect 1",
            "-reconnect_streamed 1",
            "-reconnect_delay_max 5",
            "-nostdin"
        ])
    else:
        before_opts.append("-nostdin")

    before_options_str = " ".join(before_opts)

    filter_chain = []
    preset_key = eq_preset.lower().strip() if eq_preset else "off"
    preset_filter = EQ_PRESETS.get(preset_key, EQ_PRESETS["off"])
    if preset_filter:
        filter_chain.append(preset_filter)

    if volume != 1.0 and volume > 0:
        filter_chain.append(f"volume={volume:.2f}")

    af_filter_str = ",".join(filter_chain) if filter_chain else f"{HQ_RESAMPLE},alimiter=limit=0.98:attack=5:release=50:asc=1"
    options_str = f'-vn -af "{af_filter_str}" -f s16le -ar 48000 -ac 2'

    return discord.FFmpegPCMAudio(
        url_or_path,
        before_options=before_options_str,
        options=options_str
    )


def get_local_file_metadata(filepath: str) -> Dict[str, Any]:
    meta = {
        "title": os.path.splitext(os.path.basename(filepath))[0],
        "artist": "Local Audio",
        "duration": 0,
        "path": filepath
    }
    if not mutagen:
        return meta
    try:
        audio = mutagen.File(filepath)
        if audio is not None:
            if hasattr(audio.info, "length"):
                meta["duration"] = int(audio.info.length)
            if "title" in audio:
                meta["title"] = str(audio["title"][0])
            elif "TIT2" in audio:
                meta["title"] = str(audio["TIT2"].text[0])
            if "artist" in audio:
                meta["artist"] = str(audio["artist"][0])
            elif "TPE1" in audio:
                meta["artist"] = str(audio["TPE1"].text[0])
    except Exception:
        pass
    return meta


def scan_library(directory: str) -> list:
    found = []
    if not os.path.exists(directory):
        return found
    for root, _, files in os.walk(directory):
        for file in files:
            if file.lower().endswith(SUPPORTED_MEDIA_EXTENSIONS):
                full_path = os.path.join(root, file)
                found.append(get_local_file_metadata(full_path))
    return found

scan_local_library = scan_library
get_file_metadata = get_local_file_metadata
