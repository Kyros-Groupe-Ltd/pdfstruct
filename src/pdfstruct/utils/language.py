"""Language detection using word frequency heuristics."""

from __future__ import annotations

from collections import Counter

# Top high-frequency function words per language
LANGUAGE_MARKERS: dict[str, set[str]] = {
    "en": {"the", "and", "is", "in", "to", "of", "a", "that", "it", "for",
           "was", "on", "are", "with", "as", "this", "be", "at", "have", "from",
           "or", "an", "by", "not", "but", "what", "all", "were", "when", "can"},
    "fr": {"le", "la", "les", "de", "des", "du", "un", "une", "et", "est",
           "en", "que", "qui", "dans", "pour", "pas", "sur", "ce", "sont", "au",
           "avec", "il", "elle", "nous", "vous", "ils", "ont", "cette", "par", "mais"},
    "es": {"el", "la", "los", "las", "de", "del", "en", "un", "una", "que",
           "es", "por", "con", "para", "se", "su", "al", "no", "son", "como",
           "pero", "fue", "esta", "tiene", "hay", "todo", "entre", "cuando", "muy", "nos"},
    "de": {"der", "die", "das", "und", "ist", "von", "den", "ein", "eine", "mit",
           "auf", "des", "dem", "nicht", "sich", "auch", "als", "noch", "nach", "wird",
           "bei", "aus", "wie", "aber", "sind", "hat", "oder", "kann", "dass", "nur"},
    "pt": {"de", "da", "do", "dos", "das", "em", "um", "uma", "que", "para",
           "com", "por", "no", "na", "nos", "nas", "ao", "aos", "foi", "mais",
           "como", "mas", "ou", "se", "tem", "seu", "sua", "ele", "ela", "isso"},
    "it": {"il", "la", "le", "di", "del", "della", "un", "una", "che", "per",
           "con", "non", "sono", "da", "dei", "delle", "gli", "nel", "nella", "anche",
           "come", "questo", "questa", "ma", "suo", "sua", "hanno", "stato", "essere", "alla"},
    "nl": {"de", "het", "van", "een", "en", "in", "is", "dat", "op", "te",
           "met", "voor", "zijn", "aan", "niet", "ook", "wordt", "door", "maar", "bij"},
    "ar": {"في", "من", "على", "إلى", "أن", "هذا", "التي", "الذي", "هو", "كان",
           "عن", "مع", "هذه", "بين", "كل", "ما", "لا", "هي", "أو", "قد"},
    "zh": {"的", "是", "在", "了", "不", "和", "有", "人", "这", "中",
           "大", "为", "上", "个", "会", "也", "到", "说", "要", "就"},
}


def detect_language(text: str) -> str:
    """Detect the primary language of a text string.

    Returns ISO 639-1 language code (e.g., 'en', 'fr', 'es').
    Returns 'en' as default when detection is uncertain.
    """
    if not text or len(text.strip()) < 20:
        return "en"

    # Tokenize: lowercase, split on whitespace and punctuation
    words = text.lower().split()
    # Strip punctuation from words
    cleaned = []
    for w in words:
        w = w.strip(".,;:!?\"'()[]{}«»""''—–-/\\")
        if w:
            cleaned.append(w)

    if not cleaned:
        return "en"

    word_set = set(cleaned)
    word_counter = Counter(cleaned)
    total_words = len(cleaned)

    # Score each language by how many of its marker words appear
    scores: dict[str, float] = {}
    for lang, markers in LANGUAGE_MARKERS.items():
        # Count both unique matches and weighted frequency
        matches = word_set & markers
        unique_score = len(matches) / len(markers)

        # Frequency-weighted score: how much of the text is marker words
        freq_score = sum(word_counter.get(m, 0) for m in markers) / total_words

        # Combined score (unique presence + frequency weight)
        scores[lang] = (unique_score * 0.6) + (freq_score * 0.4)

    if not scores:
        return "en"

    best_lang = max(scores, key=scores.get)
    best_score = scores[best_lang]

    # Require minimum confidence
    if best_score < 0.02:
        return "en"

    return best_lang
