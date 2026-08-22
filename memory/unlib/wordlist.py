"""
Built-in content filter wordlist.

Structure:
    WORDLIST: dict[category_name, dict] where each entry has:
        - words: list of exact words / phrases (lowercased)
        - patterns: list of regex fragments (compiled with re.IGNORECASE | re.WORD)
        - action: "redact" | "drop"
        - tier: 1 (mild) → 3 (severe)

Categories:
    profanity           - common swear words          → redact
    sexual_explicit     - explicit sexual content      → redact
    sexual_violence     - rape, assault, coercion      → drop
    hate_racial         - racial slurs                 → drop
    hate_gender         - misogynistic / transphobic   → drop
    hate_orientation    - homophobic slurs             → drop
    hate_religion       - religious hate terms         → drop
    hate_disability     - ableist slurs                → drop
    self_harm           - suicide / self-injury        → drop
    pii_patterns        - email, SSN, phone (regex)    → redact
"""

from typing import Dict, Any

WORDLIST: Dict[str, Dict[str, Any]] = {

    # ── PROFANITY (tier 1 — redact) ──────────────────────────────────────────
    "profanity": {
        "action": "redact",
        "tier": 1,
        "words": [
            "fucked", "fucker", "fucking",
            "ass", "asses",
            "bitch", "bitches", "bitchy",
            "damn", "damned", "goddamn", "goddamned",
            "crap", "crappy",
            "piss", "pissed", "pissing",
            "dick", "dicks",
            "cock", "cocks",
            "pussy", "pussies",
            "cunt", "cunts",
            "hell",         # mild — context-dependent, kept tier 1
            "jackass",
            "douchebag", "douche",
            "motherfucker", "motherfucking",
            "son of a bitch",
            "shut the fuck up", "stfu",
            "wtf", "omfg",
            "arse", "arsehole",       # British variants
            "bollocks", "wanker", "tosser", "twat",
            "blimey",                 # very mild, included for completeness
            "bloody hell",
        ],
        "patterns": [
            r"f+u+c+k+",             # leet / repeated letter evasion
            r"sh[i1!]+t",
            r"b[i1!]tch",
            r"a+s+h+o+l+e+",
        ],
    },

    # ── SEXUALLY EXPLICIT (tier 2 — redact) ──────────────────────────────────
    "sexual_explicit": {
        "action": "redact",
        "tier": 2,
        "words": [
            "porn", "porno", "pornography",
            "nude", "nudes", "naked",
            "masturbate", "masturbation", "masturbating",
            "ejaculate", "ejaculation",
            "orgasm",
            "erection", "boner",
            "blowjob", "blow job", "handjob", "hand job",
            "cum", "cumshot",
            "dildo", "vibrator",
            "anal sex", "oral sex",
            "threesome", "gangbang", "gang bang",
            "fetish",
            "hentai",
            "sexting",
            "sex tape",
            "onlyfans",              # platform name used in explicit context
        ],
        "patterns": [
            r"xxx",
            r"nsfw",
            r"18\s*\+\s*content",
        ],
    },

    # ── SEXUAL VIOLENCE (tier 3 — drop) ──────────────────────────────────────
    "sexual_violence": {
        "action": "drop",
        "tier": 3,
        "words": [
            "rape", "raped", "raping", "rapist", "rapes",
            "gang rape", "gang raped",
            "sexual assault", "sexually assaulted",
            "sexual abuse", "sexually abused",
            "molestation", "molest", "molested", "molesting", "molester",
            "child abuse", "child molestation",
            "groping", "groped",
            "non-consensual", "nonconsensual",
            "stealthing",
            "date rape",
            "statutory rape",
            "sex trafficking", "sexual trafficking",
            "coerced sex", "coercion",
            "incest",
            "bestiality",
            "child pornography", "child porn", "cp",   # in sexual context
            "csam",                                    # child sexual abuse material
            "lolicon", "shotacon",
        ],
        "patterns": [
            r"rape\w*",
            r"molest\w*",
            r"child\s+sex",
            r"minor\s+(sex|porn|nude)",
            r"underage\s+(sex|porn|nude)",
        ],
    },

    # ── RACIAL SLURS (tier 3 — drop) ─────────────────────────────────────────
    "hate_racial": {
        "action": "drop",
        "tier": 3,
        "words": [
            # NOTE: words are stored as-is so the filter can match them;
            # they are never surfaced in output.
            "nigger", "nigga", "nigg",
            "chink", "chinks",
            "gook", "gooks",
            "spic", "spics",
            "wetback", "wetbacks",
            "kike", "kikes",
            "towelhead", "raghead",
            "sand nigger",
            "beaner", "beaners",
            "coon", "coons",
            "jungle bunny",
            "porch monkey",
            "zip", "zipperhead",
            "slope",
            "cracker",              # anti-white slur, included for symmetry
            "white trash",
            "redskin",              # slur for Indigenous people
            "squaw",
            "half-breed",
            "mulatto",
            "jap", "japs",
            "kraut", "krauts",
            "frog",                 # anti-French
            "limey",
        ],
        "patterns": [
            r"n[i1!]+gg[aeu]+r?s?",
        ],
    },

    # ── GENDER / MISOGYNISTIC / TRANSPHOBIC SLURS (tier 3 — drop) ───────────
    "hate_gender": {
        "action": "drop",
        "tier": 3,
        "words": [
            "tranny", "trannies",
            "shemale",
            "he-she", "he/she",     # used derogatorily
            "trap",                 # derogatory trans usage
            "tr*p",
            "femoid", "foid",
            "roastie",
            "thot",
            "whore", "whores",
            "slut", "sluts",
            "skank",
            "harlot",
            "prostitute",           # neutral clinical word — listed for context flagging
            "dyke",                 # slur context; reclaimed by some, flagged by default
            "lesbo",
        ],
        "patterns": [],
    },

    # ── HOMOPHOBIC SLURS (tier 3 — drop) ─────────────────────────────────────
    "hate_orientation": {
        "action": "drop",
        "tier": 3,
        "words": [
            "faggot", "faggots", "fag", "fags",
            "queer",                # reclaimed but still flagged by default
            "homo", "homos",
            "sodomite",
            "pillow biter",
            "poofter", "poof",
            "nancy boy",
            "pansy",
        ],
        "patterns": [
            r"f[a4]+gg?[o0]+t",
        ],
    },

    # ── RELIGIOUS HATE (tier 3 — drop) ───────────────────────────────────────
    "hate_religion": {
        "action": "drop",
        "tier": 3,
        "words": [
            "jihadi",               # used as slur
            "islamophobe", "islamophobia",   # describing hate
            "crusader",             # used in hate-speech context
            "christ-killer",
            "infidel",              # weaponised context
            "kafir",                # slur context
            "papist",
            "anti-semitic", "antisemitic",
            "nazi", "nazis",
            "neo-nazi", "neonazi",
            "white supremacist", "white supremacy",
            "kkk", "ku klux klan",
            "holocaust denial", "holocaust denier",
            "heil hitler",
        ],
        "patterns": [
            r"heil\s+hitler",
            r"white\s+power",
            r"14\s*words",          # white nationalist slogan
        ],
    },

    # ── DISABILITY SLURS (tier 2 — redact) ───────────────────────────────────
    "hate_disability": {
        "action": "redact",
        "tier": 2,
        "words": [
            "retard", "retarded", "retards",
            "spastic", "spaz",
            "cripple", "crippled",
            "moron", "morons",
            "imbecile",
            "idiot",                # clinical origin, now slur
            "lunatic",
            "psycho",
            "schizo",
            "nutjob", "nut job",
            "crazy",                # mild — redact rather than drop
            "insane",
        ],
        "patterns": [],
    },

    # ── SELF-HARM / SUICIDE (tier 3 — drop) ──────────────────────────────────
    "self_harm": {
        "action": "drop",
        "tier": 3,
        "words": [
            "kill myself", "killing myself",
            "want to die",
            "commit suicide", "committed suicide",
            "self-harm", "self harm", "selfharm",
            "cutting myself",
            "overdose on",
            "hang myself",
            "slit my wrists",
            "end my life",
            "suicide method", "suicide methods",
            "how to kill",
        ],
        "patterns": [
            r"how\s+to\s+(commit\s+)?suicide",
            r"ways?\s+to\s+kill\s+(myself|yourself)",
            r"suicide\s+(note|letter|plan)",
        ],
    },

    # ── PII (tier 1 — redact via regex only) ─────────────────────────────────
    # These have no word lists, only patterns.
    "pii_patterns": {
        "action": "redact",
        "tier": 1,
        "words": [],
        "patterns": [
            r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Z|a-z]{2,}\b",   # email
            r"\b\d{3}[-.\s]?\d{2}[-.\s]?\d{4}\b",                         # SSN
            r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b",  # US phone
            r"\b4[0-9]{12}(?:[0-9]{3})?\b",                                # Visa
            r"\b5[1-5][0-9]{14}\b",                                         # Mastercard
            r"\b3[47][0-9]{13}\b",                                          # Amex
            r"\b(?:password|passwd|secret|api[_\s]?key)\s*[:=]\s*\S+",    # creds in text
        ],
    },
}


def all_drop_words() -> list[str]:
    """Flat list of all words whose action is 'drop'."""
    out = []
    for cat in WORDLIST.values():
        if cat["action"] == "drop":
            out.extend(cat.get("words", []))
    return out


def all_redact_words() -> list[str]:
    """Flat list of all words whose action is 'redact'."""
    out = []
    for cat in WORDLIST.values():
        if cat["action"] == "redact":
            out.extend(cat.get("words", []))
    return out


def categories_by_tier(tier: int) -> list[str]:
    return [name for name, cat in WORDLIST.items() if cat.get("tier") == tier]
