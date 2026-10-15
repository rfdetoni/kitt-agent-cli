# KITT Lexicon: PT-BR, EN and ES (opt-in)

The Agent now supports a conservative offline lexical intent layer. The original human request is
always forwarded unchanged, and all execution authorization remains with the host policy.
The reverse-proxy LLM-first route is unchanged. The default semantic mode is `off`.

## Sources, usage and redistribution

| Source | Application | License / conditions |
|---|---|---|
| UniMorph [PT](https://github.com/unimorph/por), [EN](https://github.com/unimorph/eng), [ES](https://github.com/unimorph/spa) | Selected imperative forms of curated software verbs | CC BY-SA 3.0 for those corpora |
| [Open Multilingual Wordnet](https://github.com/omwn/omw-data) | Unapproved candidates by verb synset | License varies by language: OpenWN-PT CC BY-SA 3.0; Princeton WordNet own open license |
| [wordfreq](https://github.com/rspeer/wordfreq) | Build-time Zipf frequency metadata | Apache 2.0 software, CC BY-SA 4.0 data |
| [Snowball](https://github.com/snowballstem/snowball) | Optional build-time stemming metadata | Verify the selected library license |
| [spaCy](https://github.com/explosion/spaCy) | Optional build-time lemmatization | MIT code; individual model licenses vary |

**No corpora are bundled or downloaded by this feature.** Download, pin and review the individual
source versions explicitly. Attribute each dataset and respect its redistribution obligations.
Derived databases containing CC BY-SA material may have ShareAlike obligations; they are
not automatically covered by the KITT MIT code license.

## Offline compilation

Obtain UniMorph plain files and the desired OMW `wn-data-*.tab` corpus separately.
Run from an Agent CLI checkout:

```bash
python scripts/build_semantic_lexicon.py \
  --unimorph pt=/data/unimorph/por \
  --unimorph en=/data/unimorph/eng \
  --unimorph es=/data/unimorph/spa \
  --omw pt=/data/omw/wn-data-por.tab \
  --accept-upstream-licenses \
  --output ~/.local/share/kitt/lexicon.db
```

The compiler makes **no HTTP requests**. It records SHA-256 and provenance in the SQLite
`sources` table and atomically publishes the result. Imports are size/row bounded.
WordNet synonyms are **suggestions only** (`approved=0`); lexical similarity is never
automatically turned into a command.

Explicitly reviewed additions can be imported with `--approve reviewed.tsv`, with tab-separated
lines in the format `pt<TAB>ajuste<TAB>DEBUG`, `en<TAB>remediate<TAB>DEBUG`.
Only one-word commands are supported by the operator TSV; curated phrases are code reviewed.
Ambiguous forms belonging to different intents are disabled.

Optional build-time metadata switches: `--wordfreq` (installed wordfreq package),
`--snowball` (installed snowballstemmer package), or `--spacy pt=pt_core_news_sm`
(an installed spaCy model). None are runtime dependencies. Frequencies, stems and
lemmata cannot authorize operations.

## Configure the optional runtime

```bash
export KITT_SEMANTIC_MODE=auto
export KITT_SEMANTIC_LEXICON_DB="$HOME/.local/share/kitt/lexicon.db"
```

Without the database, only the compact built-in reviewed vocabulary is available.
With the database, the index is read via an exact-match SQLite query (read-only).
Never point the operator-controlled database setting at an untrusted workspace file.
Embedding inference (Ollama E5) stays independent and optional; ambiguous cases abstain.
Safety checks, original-request fidelity, approvals and tool policy remain mandatory.

## Validation

```bash
python -m pytest -q tests/test_offline_lexicon.py tests/test_local_semantic_integration.py tests/test_multilingual_semantic_fidelity.py
python -m compileall -q kitt scripts tests
python packaging/verify_cleanroom.py
```

These tests do not establish production-wide classifier accuracy or quantified token savings.
Those require a new held-out multilingual dataset and end-to-end evaluation.
