"""Place tags from source into target text using an LLM."""

import os

from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

SYSTEM_PROMPT = """You are a game localization tag placement assistant.

Your job: given a source sentence with inline tags {1}, {2}, ... and a plain target
translation, insert the tags into the target text at the correct positions.

Rules:
- Tags come in open/close pairs that wrap content (e.g., {1}number{2}, {3}skill name{4})
- accent-gn tags wrap: numbers, percentages, resource names, stat names, keywords
- linktext + Q5 tags always appear as a group of 4 wrapping a skill name
- physical tags wrap damage-related text
- Structural tags (br, size, tips) stay at the same relative position as in source
- Chinese measure words (点/枚/名/次/个) are dropped in English
- Time units: 秒→s, merged inside the tag (e.g., {5}4s{6})
- Spaces are required between tags and adjacent English words
- ALL tags from source must appear in target — same count, no extras, no omissions
- The plain text of the target must NOT be modified — only tags are inserted

Output ONLY the tagged target text. Nothing else."""


def get_client():
    """Create an OpenAI-compatible client from env vars."""
    api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("DEEPSEEK_BASE_URL") or os.getenv("OPENAI_BASE_URL")
    if not api_key:
        raise ValueError(
            "No API key found. Set DEEPSEEK_API_KEY or OPENAI_API_KEY in .env"
        )
    return OpenAI(api_key=api_key, base_url=base_url)


def get_model():
    return (
        os.getenv("DEEPSEEK_MODEL")
        or os.getenv("OPENAI_MODEL")
        or "deepseek-chat"
    )


def place_tags(src_text, src_tags, tgt_text, client=None, model=None):
    """Use LLM to place tags from source into target text.

    Args:
        src_text: Source text with {N} placeholders
        src_tags: List of tag info dicts from parse.classify_tag
        tgt_text: Plain target text without tags

    Returns:
        Tagged target text with {N} placeholders inserted
    """
    if not src_tags:
        return tgt_text

    if client is None:
        client = get_client()
    if model is None:
        model = get_model()

    tag_descriptions = "\n".join(
        f"  {{{t['id']}}}: {t['type']} — {t['detail']}" for t in src_tags
    )

    user_prompt = f"""Source (with tags):
{src_text}

Tag meanings:
{tag_descriptions}

Target (plain text, no tags):
{tgt_text}

Place all tags into the target text. Output ONLY the tagged text."""

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0,
    )
    result = response.choices[0].message.content.strip()

    import re
    from collections import Counter

    expected = Counter(t["id"] for t in src_tags)
    found = Counter(m.group(1) for m in re.finditer(r"\{(\d+)\}", result))

    if found != expected:
        missing = expected - found
        extra = found - expected
        warnings = []
        if missing:
            warnings.append(f"missing tags: {dict(missing)}")
        if extra:
            warnings.append(f"extra tags: {dict(extra)}")
        result = f"⚠️ TAG MISMATCH ({', '.join(warnings)})\n{result}"

    return result
