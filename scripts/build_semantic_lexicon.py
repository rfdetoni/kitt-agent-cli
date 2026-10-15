#!/usr/bin/env python3
"""Compile explicitly supplied local lexical resources into an offline SQLite index.

No network access, package installation or corpora download occurs here.
Third-party data stay outside the wheel and require user license acknowledgement.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import tempfile

from kitt.context_filter.lexicon import CURATED, SEED_LEMMAS, normalize

LANGUAGES = {"pt", "en", "es"}
UPSTREAM = {
    "unimorph": {
        "pt": ("https://github.com/unimorph/por", "CC BY-SA 3.0"),
        "en": ("https://github.com/unimorph/eng", "CC BY-SA 3.0"),
        "es": ("https://github.com/unimorph/spa", "CC BY-SA 3.0"),
    },
    "omw": {
        "pt": ("https://github.com/omwn/omw-data", "CC BY-SA (OpenWN-PT; verify version)"),
        "en": ("https://github.com/omwn/omw-data", "verify dataset-specific license"),
        "es": ("https://github.com/omwn/omw-data", "CC BY 3.0 (MCR Spanish)"),
    },
}
MAX_INPUT_BYTES = 128 * 1024 * 1024
MAX_ROWS = 2_000_000
_HEAD = re.compile(r"^[^\W_]+$", re.UNICODE)


def source_specs(raw: list[str]) -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []
    for value in raw:
        language, separator, path = value.partition("=")
        if not separator or language not in LANGUAGES or not path:
            raise ValueError("Expected pt=PATH, en=PATH or es=PATH")
        resolved = Path(path).expanduser().resolve(strict=True)
        if not resolved.is_file() or resolved.stat().st_size > MAX_INPUT_BYTES:
            raise ValueError(f"Invalid or oversized linguistic source: {resolved}")
        out.append((language, resolved))
    return out


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_rows(path: Path):
    # Bounded streaming: reject invalid UTF-8 and datasets larger than advertised.
    with path.open(encoding="utf-8-sig") as stream:
        for count, line in enumerate(stream, 1):
            if count > MAX_ROWS:
                raise ValueError(f"Too many rows in {path}")
            if len(line) > 4096:
                continue
            if line.startswith("#") or not line.strip():
                continue
            yield line.rstrip("\r\n").split("\t")


def curated_lemmas(language: str) -> dict[str, str]:
    result: dict[str, str] = {}
    ambiguous: set[str] = set()
    for intent, languages in SEED_LEMMAS.items():
        for lemma in languages.get(language, ()):
            lemma = normalize(lemma)
            if lemma in result and result[lemma] != intent:
                ambiguous.add(lemma)
            result[lemma] = intent
    for lemma in ambiguous:
        result.pop(lemma)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--unimorph", action="append", default=[], metavar="LANG=FILE")
    parser.add_argument("--omw", action="append", default=[], metavar="LANG=FILE")
    parser.add_argument("--approve", action="append", default=[], metavar="REVIEWED_TSV")
    parser.add_argument("--accept-upstream-licenses", action="store_true")
    parser.add_argument("--wordfreq", action="store_true", help="Annotate Zipf frequencies if installed")
    parser.add_argument("--snowball", action="store_true", help="Annotate stems if installed")
    parser.add_argument("--spacy", action="append", default=[], metavar="LANG=LOCAL_MODEL")
    args = parser.parse_args(argv)
    unimorph = source_specs(args.unimorph)
    omw = source_specs(args.omw)
    approved = [Path(path).expanduser().resolve(strict=True) for path in args.approve]
    for path in approved:
        if not path.is_file() or path.stat().st_size > MAX_INPUT_BYTES:
            parser.error("Invalid reviewed TSV")
    if (unimorph or omw) and not args.accept_upstream_licenses:
        parser.error("Review each corpus license; pass --accept-upstream-licenses to import")
    try:
        freq = None
        if args.wordfreq:
            from wordfreq import zipf_frequency
            freq = zipf_frequency
        stemmers = {}
        if args.snowball:
            import snowballstemmer
            stemmers = {language: snowballstemmer.stemmer(name) for language, name in (
                ("pt", "portuguese"), ("en", "english"), ("es", "spanish")
            )}
        spacy_models = {}
        if args.spacy:
            import spacy
            for spec in args.spacy:
                lang, eq, model = spec.partition("=")
                if not eq or lang not in LANGUAGES or not model:
                    parser.error("--spacy requires LANG=LOCAL_MODEL")
                spacy_models[lang] = spacy.load(model)
    except ImportError as exc:
        parser.error(f"Optional build-time package not installed: {exc}")

    destination = args.output.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, filename = tempfile.mkstemp(prefix=".kitt-lexicon-", suffix=".db",
                                     dir=destination.parent)
    os.close(fd)
    try:
        with sqlite3.connect(filename) as db:
            db.execute("""CREATE TABLE entries (
                lang TEXT NOT NULL, form TEXT NOT NULL, intent TEXT NOT NULL,
                lemma TEXT NOT NULL, origin TEXT NOT NULL, approved INTEGER NOT NULL,
                stem TEXT, zipf REAL,
                PRIMARY KEY (lang, form, intent, origin)
            )""")
            db.execute("""CREATE INDEX entry_lookup ON entries(form, approved)""")
            db.execute("""CREATE TABLE sources (
                origin TEXT NOT NULL, lang TEXT NOT NULL, url TEXT NOT NULL,
                license TEXT NOT NULL, sha256 TEXT NOT NULL,
                PRIMARY KEY (origin, lang, sha256)
            )""")
            counters: dict[str, int] = {}

            def insert(lang, form, intent, lemma, origin, allowed):
                form, lemma = normalize(form), normalize(lemma)
                # Prevent phrases, punctuation or data records from becoming commands.
                if not 2 <= len(form) <= 60 or not _HEAD.fullmatch(form):
                    return
                if intent not in CURATED or lang not in LANGUAGES:
                    return
                stem = stemmers[lang].stemWord(form) if lang in stemmers else None
                if lang in spacy_models:
                    # Metadata only: POS/lemmatizer models cannot silently authorize intent.
                    doc = spacy_models[lang](form)
                    if doc and doc[0].lemma_:
                        lemma = normalize(doc[0].lemma_)
                score = float(freq(form, lang)) if freq else None
                db.execute(
                    "INSERT OR IGNORE INTO entries VALUES (?,?,?,?,?,?,?,?)",
                    (lang, form, intent, lemma, origin, int(allowed), stem, score),
                )
                counters[origin] = counters.get(origin, 0) + 1

            for intent, langs in CURATED.items():
                for lang, phrases in langs.items():
                    for phrase in phrases:
                        # Only single-token curated phrases are used as database keys.
                        insert(lang, phrase, intent, phrase, "kitt-reviewed", True)

            def provenance(kind, lang, path):
                url, license_name = UPSTREAM[kind][lang]
                db.execute("INSERT OR IGNORE INTO sources VALUES (?,?,?,?,?)",
                           (kind, lang, url, license_name, sha256(path)))

            for lang, path in unimorph:
                anchors = curated_lemmas(lang)
                provenance("unimorph", lang, path)
                for cells in source_rows(path):
                    if len(cells) < 3:
                        continue
                    lemma, form = normalize(cells[0]), normalize(cells[1])
                    features = set(cells[2].upper().split(";"))
                    # Imperatives only. Other verb conjugations are often statements.
                    if "V" not in features or "IMP" not in features:
                        continue
                    if lemma in anchors:
                        insert(lang, form, anchors[lemma], lemma, "unimorph", True)

            for lang, path in omw:
                anchors = curated_lemmas(lang)
                provenance("omw", lang, path)
                # OMW tab: synset-id, lemma, word. Candidate only; senses are
                # polysemous and cannot be promoted without human review.
                groups: dict[str, set[str]] = {}
                for cells in source_rows(path):
                    if len(cells) >= 3 and cells[1] == "lemma" and cells[0].endswith("-v"):
                        form = normalize(cells[2])
                        if _HEAD.fullmatch(form):
                            groups.setdefault(cells[0], set()).add(form)
                for words in groups.values():
                    senses = {anchors[word] for word in words if word in anchors}
                    if len(senses) != 1:
                        continue
                    intent = next(iter(senses))
                    for word in words:
                        insert(lang, word, intent, word, "omw-suggestion", False)

            for path in approved:
                # The operator explicitly curated the mapping: lang<TAB>form<TAB>intent.
                for cells in source_rows(path):
                    if len(cells) != 3:
                        raise ValueError(f"Invalid approved row in {path}")
                    lang, form, intent = (cell.strip() for cell in cells)
                    if lang not in LANGUAGES or intent not in CURATED:
                        raise ValueError(f"Unknown reviewed language/intent in {path}")
                    insert(lang, form, intent, form, "operator-reviewed", True)
                db.execute("INSERT OR IGNORE INTO sources VALUES (?,?,?,?,?)",
                           ("operator-reviewed", "*", str(path), "operator supplied", sha256(path)))
            # Approved cross-intent homographs are never valid as direct commands.
            db.execute("""UPDATE entries SET approved=0 WHERE form IN (
                SELECT form FROM entries WHERE approved=1
                GROUP BY form HAVING COUNT(DISTINCT intent)>1
            )""")
            db.commit()
        os.chmod(filename, 0o600)
        os.replace(filename, destination)
        print(f"Offline lexicon created: {destination}")
        print("Imported rows:", ", ".join(f"{key}={value}" for key, value in sorted(counters.items())))
        print("OMW suggestions are unapproved; only reviewed and imperative entries can route.")
        return 0
    finally:
        if os.path.exists(filename):
            os.unlink(filename)


if __name__ == "__main__":
    raise SystemExit(main())
