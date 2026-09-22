import os
import re
import sys
import unicodedata
from pathlib import Path

VALID_EXTENSIONS = {".mp3", ".flac", ".m4a", ".ogg", ".wav", ".opus", ".aac"}

# Translation table for Unicode Small-Caps / Obfuscated alphabet
SMALL_CAPS_MAP = str.maketrans({
    'ᴀ': 'a', 'ʙ': 'b', 'ᴄ': 'c', 'ᴅ': 'd', 'ᴇ': 'e', 'ꜰ': 'f', 'ɢ': 'g',
    'ʜ': 'h', 'ɪ': 'i', 'ᴊ': 'j', 'ᴋ': 'k', 'ʟ': 'l', 'ᴍ': 'm', 'ɴ': 'n',
    'ᴏ': 'o', 'ᴘ': 'p', 'ǫ': 'q', 'ʀ': 'r', 'ꜱ': 's', 'ᴛ': 't', 'ᴜ': 'u',
    'ᴠ': 'v', 'ᴡ': 'w', 'x': 'x', 'ʏ': 'y', 'ᴢ': 'z'
})

# Buzzwords / tags to completely remove
BUZZWORDS = [
    r"unreleased", r"unrealized", r"leak(?:ed)?", r"new\s*leak", r"jw3\s*era\s*leak",
    r"(?:best|perfect|updated)\s*quality(?:\s*version)?", r"cdq(?:\s*edit)?(?:\s*remaster)?",
    r"hq", r"hd", r"4k", r"8d(?:\s*audio)?", r"full(?:\s*song)?", r"lyrics?", r"remaster(?:ed)?",
    r"snippet", r"tagged", r"clean", r"explicit", r"slowed(?:\s*\+\s*reverb)?",
    r"bass\s*boosted", r"amv", r"extended(?:\s*verse)?", r"og\s*extended", r"og\s*version", r"\bog\b",
    r"session\s*edit\s*&\s*unheard", r"session(?:s)?(?:\s*edit)?", r"studio\s*session(?:s)?",
    r"fixed\s*intro", r"skip\s*to\s*[\d:_]+(?:\s*min)?", r"\d+\s*minute(?:\s*version|\s*long)",
    r"(?:128|192|256|320)\s*kbps", r"\d{3,4}p", r"jcraigmix", r"drfl", r"wod",
    r"rare", r"fnl", r"main\s*mix", r"instrumental\s*remake", r"instrumental\s*\d*",
    r"\d{3,4}\s*hz", r"\be\b"  # 432Hz, 528Hz, [E] explicit tag
]

BUZZWORD_PATTERN = re.compile(
    r"[\(\[\{][^\)\]\}]*(?:" + "|".join(BUZZWORDS) + r")[^\)\]\}]*[\)\]\}]",
    re.IGNORECASE
)

# Pro Tools DAW bounce / Engineer tags
STEM_TAG_PATTERN = re.compile(
    r"[\(\[\{]\s*(?:NM|DD2|REX|CHB|DYBH|ALLURE|CHRJ|NS10|01020009|4040|audi|rs\s*\d+|GTJ|nacon|margate|darkalone|DYATL|tminus|cj|ghoul|rebirth|mello|watermelon|PSX|xr|yk\s*fil|\d{3,5})[\w\s\-\.]*[\)\]\}]",
    re.IGNORECASE
)

# Standalone junk patterns
STANDALONE_JUNK = [
    r"@[\w_]+",                                     # @johnmetadata
    r"\bprod(?:\.|\s*by)?\s*[\w\s,]+",              # prod. ColaBeats
    r"\bproduced\s+by\s+[\w\s]+",                   # Produced By Chef J
    r"\bchasethemoney\b", r"\bmetro\s*sizzle\s*faded\b",
    r"\bA\d{3,4}\b",                                # A800
    r"\bOPEN\s+[a-gA-G][#b]?m?\s+\d+\b",            # OPEN f#m 144
    r"\b\d{2,3}\s*bpm\b",                           # 170bpm
    r"\b(?:RUFF|rough\d*|ruff\d*)\b",               # RUFF
    r"\bv\d+(?:\.\d+)?\b",                          # v1.0, v2.1
    r"\b\d+\.\d+\b",                                # 4.11
    r"\b\d{3,4}\s*hz\b",                            # 432Hz, 528Hz outside brackets
    r"\s+TIJD\b", r"\s+invasion\b",
    r"\s+session(?:s)?\b",
    r"\b(?:rare|leak)\b",
]

TITLE_LOWERCASE_EXCEPTIONS = {"a", "an", "the", "and", "but", "or", "for", "nor", "on", "at", "to", "from", "by", "in", "of"}

def normalize_unicode_text(text: str) -> str:
    """Converts IPA small caps and math alphanumeric unicode characters to standard ASCII."""
    # Convert Mathematical Bold/Italic/Script to standard unicode characters
    normalized = unicodedata.normalize('NFKC', text)
    # Convert phonetic IPA small-caps to standard ASCII
    normalized = normalized.translate(SMALL_CAPS_MAP)
    return normalized

def smart_title_case(text: str) -> str:
    words = text.split()
    if not words:
        return ""
    result = []
    for i, word in enumerate(words):
        lower_word = word.lower()
        if lower_word in {"og", "cdq", "hq", "hd", "amv", "wod", "drfl", "nlmb", "ok", "rdr", "idk", "911"}:
            result.append(lower_word.upper())
        elif lower_word in {"pt.", "pt", "part", "vol.", "vol"}:
            result.append("Pt." if "pt" in lower_word else word.capitalize())
        elif i > 0 and lower_word in TITLE_LOWERCASE_EXCEPTIONS:
            result.append(lower_word)
        else:
            result.append(word.capitalize())
    return " ".join(result)

def clean_song_name(raw_stem: str) -> str:
    # 1. Normalize Unicode, IPA small-caps, and odd symbols
    cleaned = normalize_unicode_text(raw_stem)
    cleaned = re.sub(r"[＊*★☆✨【】✔️_]", " ", cleaned)
    cleaned = cleaned.replace("⧸", " ").replace("／", " ")

    # 2. Strip leading uploader brackets and ripper hashes
    cleaned = re.sub(r"^\s*\[[^\]]+\]\s*", "", cleaned)
    cleaned = re.sub(r"^[a-zA-Z0-9]{6,12}-+", "", cleaned)

    # 3. Strip bracketed buzzwords and DAW stems
    for _ in range(3):
        cleaned = BUZZWORD_PATTERN.sub("", cleaned)
        cleaned = STEM_TAG_PATTERN.sub("", cleaned)

    # 4. Strip standalone junk / producer tags
    for pat in STANDALONE_JUNK:
        cleaned = re.sub(pat, "", cleaned, flags=re.IGNORECASE)

    # 5. Extract features using STRICT word boundaries
    features = []
    feat_matches = re.finditer(r"[\(\[\{]\s*\b(?:with|ft\.?|feat\.?)\b\s+([^\)\]\}]+)[\)\]\}]", cleaned, re.IGNORECASE)
    for m in feat_matches:
        feat_name = m.group(1).strip()
        if feat_name and feat_name.lower() not in [f.lower() for f in features]:
            features.append(feat_name)
    cleaned = re.sub(r"[\(\[\{]\s*\b(?:with|ft\.?|feat\.?)\b\s+[^\)\]\}]+[\)\]\}]", "", cleaned, flags=re.IGNORECASE)

    # 6. Check for inverted Artist - Title (e.g. "Song Name - Juice WRLD")
    inv_match = re.match(r"^(.*?)\s*[-_]?\s*Juice\s*(?:W(?:o)?r(?:l)?d)?\s*$", cleaned, re.IGNORECASE)
    if inv_match and inv_match.group(1).strip():
        cleaned = f"Juice WRLD - {inv_match.group(1)}"

    # 7. Standardize artist prefix
    artist_match = re.match(r"^\s*Juice\s*(?:W(?:o)?rld)?\s*[-_]?\s*(.*)$", cleaned, re.IGNORECASE)
    if artist_match:
        title_part = artist_match.group(1)
        cleaned = f"Juice WRLD - {title_part}"

    # 8. Split artist and title
    if " - " in cleaned:
        artist, title = cleaned.split(" - ", 1)
    elif "-" in cleaned:
        artist, title = cleaned.split("-", 1)
    else:
        artist, title = "Juice WRLD", cleaned

    # 9. Strip duplicate rip indexes (e.g. "_1", "(1)") WITHOUT breaking "Pt. 2"
    title = re.sub(r"\s*[\(\[]\d+[\)\]]$", "", title)
    title = re.sub(r"(?<!\bpt)(?<!\bpt\.)(?<!\bpart)(?<!\bvol)(?<!\bvol\.)\s+\d+$", "", title, flags=re.IGNORECASE)

    # 10. Format Title and Append Features cleanly
    clean_title = smart_title_case(title.strip())

    # Format Pt. 2 properly if lowercase or missing period
    clean_title = re.sub(r"\bPt\s+(\d+)\b", r"Pt. \1", clean_title)

    valid_features = [f for f in features if "juice" not in f.lower()]
    if valid_features:
        feat_str = ", ".join(valid_features)
        clean_title = f"{clean_title} (feat. {feat_str})"

    # Clean empty brackets and normalize spaces
    clean_title = re.sub(r"[\(\[]\s*[\)\]]", "", clean_title)
    clean_title = re.sub(r"\s+", " ", clean_title).strip()
    clean_title = re.sub(r"^[^\w\(\[]+|[^\w\)\]]+$", "", clean_title).strip()

    artist = "Juice WRLD" if "juice" in artist.lower() else smart_title_case(artist.strip())

    final_name = f"{artist} - {clean_title}"
    final_name = re.sub(r'[<>:"/\\|?*]', "", final_name).strip()
    return final_name

def run(target_folder: str, apply: bool = False):
    folder = Path(target_folder).resolve()
    if not folder.is_dir():
        print(f"❌ Error: '{folder}' is not a valid directory.")
        return

    changes = []
    for root, _, files in os.walk(folder):
        for file in sorted(files):
            file_path = Path(root) / file
            if file_path.suffix.lower() in VALID_EXTENSIONS:
                new_stem = clean_song_name(file_path.stem)
                if new_stem and new_stem != file_path.stem:
                    new_file = file_path.with_name(f"{new_stem}{file_path.suffix}")
                    changes.append((file_path, new_file))

    if not changes:
        print("✅ All audio files are clean.")
        return

    print(f"\nFound {len(changes)} files to rename:\n" + "=" * 80)
    for old, new in changes:
        print(f"BEFORE: {old.name}")
        print(f"AFTER:  {new.name}")
        print("-" * 80)

    if not apply:
        print("\n⚠️  DRY RUN ONLY. No files were modified.")
        print(f"To apply changes, run: python tools/clean_music.py \"{target_folder}\" --apply")
    else:
        print("\nApplying changes to disk...")
        success = 0
        for old, new in changes:
            try:
                if new.exists() and new != old:
                    print(f"⚠️ Skipped (already exists): {new.name}")
                    continue
                old.rename(new)
                success += 1
            except Exception as e:
                print(f"❌ Error renaming {old.name}: {e}")
        print(f"\n🎉 Successfully renamed {success}/{len(changes)} files.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python tools/clean_music.py <music_folder> [--apply]")
        sys.exit(1)

    target = sys.argv[1]
    should_apply = "--apply" in sys.argv
    run(target, apply=should_apply)
