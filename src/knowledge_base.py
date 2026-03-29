"""Knowledge Base module: ingestion, auto-research, chapter management, and GitHub write-back.

Provides helpers for building and maintaining a structured knowledge base of
trading domain content, including webpage extraction, claim synthesis, chapter
lifecycle management, and optional GitHub persistence.
"""

import base64
import logging
import os
import platform
import re
import subprocess
import threading
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional
from collections import defaultdict
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import requests
from bs4 import BeautifulSoup
from fastapi import HTTPException
from pydantic import BaseModel

from src.config import config

logger = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────

KB_ROOT = Path(__file__).parent.parent / "knowledge-base"
KB_CHANGELOG_PATH = KB_ROOT / "CHANGELOG.md"

# ── Mirror and auto-research constants ───────────────────────────────────────

KB_MIRROR_BASE_URL = "https://r.jina.ai/"

AUTO_RESEARCH_DOMAINS = [
    "reuters.com",
    "bloomberg.com",
    "investopedia.com",
]
AUTO_RESEARCH_MAX_LINKS_PER_DOMAIN = 2
AUTO_RESEARCH_MAX_EXTRACTED_CLAIMS = 12
AUTO_RESEARCH_MIN_CLAIM_SCORE = 2
AUTO_RESEARCH_SIMILARITY_THRESHOLD = 0.86
AUTO_RESEARCH_MAX_CLAIMS_PER_SOURCE = 4

CLAIM_NOISE_PATTERNS = [
    "skip to content",
    "subscribe",
    "sign up",
    "customer support",
    "manage products",
    "remote login",
    "software updates",
    "url source:",
    "bloomberg the company",
]

PRICE_MOVEMENT_KEYWORD_WEIGHTS = {
    "inflation": 10,
    "interest rate": 12,
    "federal reserve": 12,
    "gdp": 7,
    "employment": 7,
    "earnings": 12,
    "guidance": 8,
    "valuation": 8,
    "revenue": 8,
    "margin": 7,
    "momentum": 6,
    "trend": 6,
    "breakout": 7,
    "volume": 6,
    "volatility": 6,
    "sentiment": 6,
    "liquidity": 6,
    "risk": 5,
    "sector": 5,
}

PRICE_MOVEMENT_DIMENSION_KEYWORDS = {
    "Macro": ["inflation", "interest", "federal reserve", "gdp", "employment", "yield"],
    "Fundamental": ["earnings", "valuation", "revenue", "margin", "cash flow", "guidance"],
    "Technical": ["trend", "momentum", "breakout", "support", "resistance", "moving average"],
    "Sentiment": ["sentiment", "positioning", "risk-on", "risk-off", "news flow"],
    "Risk": ["volatility", "drawdown", "liquidity", "stop", "risk management"],
}

PRICE_MOVEMENT_HORIZON_KEYWORDS = {
    "Short-term": ["intraday", "daily", "event window", "release", "catalyst", "near-term"],
    "Medium-term": ["weekly", "monthly", "swing", "regime", "rotation"],
    "Long-term": ["quarter", "multi-quarter", "long-term", "structural", "cycle"],
}

_WIKIPEDIA_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# ── GitHub write-back ─────────────────────────────────────────────────────────

GITHUB_API_BASE = "https://api.github.com"


# ── Pydantic models ───────────────────────────────────────────────────────────

class KnowledgeIngestRequest(BaseModel):
    topic: str
    url: Optional[str] = None


class KnowledgeIngestResponse(BaseModel):
    topic: str
    url: str
    source_title: str
    created_chapter: str
    created_topic_index: str
    changelog_updated: str
    status: str
    summary: str


class KnowledgeOpenExplorerRequest(BaseModel):
    path: str


class KnowledgeOpenExplorerResponse(BaseModel):
    status: str
    path: str
    message: str


class KnowledgeChapterStatusRequest(BaseModel):
    path: str
    status: str
    note: Optional[str] = None


class KnowledgeChapterStatusResponse(BaseModel):
    status: str
    path: str
    chapter_status: str
    message: str


# ── Path and slug helpers ─────────────────────────────────────────────────────

def _slugify(value: str) -> str:
    """Create filesystem-safe slug from user-provided topic text."""
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
    slug = slug.strip("-")
    return slug or "topic"


def _safe_rel_path(path: Path, root: Path) -> str:
    """Return POSIX relative path from root for API payloads."""
    return path.relative_to(root).as_posix()


def _validate_kb_relative_path(relative_path: str) -> Path:
    """Resolve and validate a chapter path inside the knowledge-base root."""
    candidate = (KB_ROOT / relative_path).resolve()
    kb_root_resolved = KB_ROOT.resolve()
    if kb_root_resolved not in candidate.parents and candidate != kb_root_resolved:
        raise HTTPException(status_code=400, detail="Invalid chapter path")
    if not candidate.exists() or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Chapter not found")
    if candidate.suffix.lower() != ".md":
        raise HTTPException(status_code=400, detail="Only markdown chapter files are supported")
    return candidate


def _build_kb_topic_paths(topic: str) -> tuple[Path, Path]:
    """Return topic directory and chapters directory under section/topic/chapter hierarchy."""
    topic_slug = _slugify(topic)
    topic_dir = KB_ROOT / "sections" / "02-trading-domain" / "topics" / f"auto-{topic_slug}"
    chapters_dir = topic_dir / "chapters"
    chapters_dir.mkdir(parents=True, exist_ok=True)
    return topic_dir, chapters_dir


def _write_topic_index_if_missing(topic_dir: Path, topic: str) -> Path:
    """Create topic-level index file on first ingestion."""
    topic_index = topic_dir / "TOPIC.md"
    if topic_index.exists():
        return topic_index

    topic_index.write_text(
        "\n".join(
            [
                f"# Topic: {topic}",
                "",
                "## Purpose",
                "- Auto-created by knowledge-base builder submissions.",
                "- Keep chapters in Draft until reviewed and promoted.",
                "",
                "## Chapter Folder",
                "- chapters/",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return topic_index


# ── Changelog helpers ─────────────────────────────────────────────────────────

def _ensure_kb_changelog() -> None:
    """Ensure changelog exists so ingestion can append audit entries."""
    if KB_CHANGELOG_PATH.exists():
        return
    KB_CHANGELOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    KB_CHANGELOG_PATH.write_text("# Knowledge Base Changelog\n", encoding="utf-8")


def _append_kb_changelog(entry: str) -> None:
    """Append ingestion updates to changelog for traceability."""
    _ensure_kb_changelog()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with KB_CHANGELOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"\n- [{timestamp}] {entry}\n")


# ── KB tree builder ───────────────────────────────────────────────────────────

def _extract_chapter_status(chapter_path: Path) -> str:
    """Read chapter status from markdown frontmatter; default to Draft."""
    try:
        with chapter_path.open("r", encoding="utf-8") as handle:
            lines = [handle.readline().strip() for _ in range(40)]
    except OSError:
        return "Draft"

    if not lines or lines[0] != "---":
        return "Draft"

    for line in lines[1:]:
        if line == "---":
            break
        if line.startswith("status:"):
            value = line.split(":", 1)[1].strip()
            return value or "Draft"

    return "Draft"


def _extract_chapter_analysis_metrics(markdown_content: str) -> dict:
    """Extract chapter analysis metrics from markdown content when available."""
    defaults = {
        "weighted_relevance_score": 0,
        "relevance_score": 0,
        "source_quality_score": 0,
        "confidence_band": "Unknown",
    }

    if not markdown_content:
        return defaults

    weighted_match = re.search(r"Weighted Relevance Score:\s*(\d{1,3})/100", markdown_content, flags=re.IGNORECASE)
    relevance_match = re.search(r"(?:^|\n)-\s*Relevance Score:\s*(\d{1,3})/100", markdown_content, flags=re.IGNORECASE)
    source_quality_match = re.search(r"Source Quality Score:\s*(\d{1,3})/100", markdown_content, flags=re.IGNORECASE)
    confidence_match = re.search(r"Confidence Band:\s*([A-Za-z]+)", markdown_content, flags=re.IGNORECASE)

    weighted_score = int(weighted_match.group(1)) if weighted_match else defaults["weighted_relevance_score"]
    relevance_score = int(relevance_match.group(1)) if relevance_match else defaults["relevance_score"]
    source_quality_score = int(source_quality_match.group(1)) if source_quality_match else defaults["source_quality_score"]
    confidence_band = confidence_match.group(1).title() if confidence_match else defaults["confidence_band"]

    return {
        "weighted_relevance_score": max(0, min(100, weighted_score)),
        "relevance_score": max(0, min(100, relevance_score)),
        "source_quality_score": max(0, min(100, source_quality_score)),
        "confidence_band": confidence_band,
    }


def _build_kb_tree() -> dict:
    """Build section/topic/chapter tree for knowledge-base viewer."""
    sections_dir = KB_ROOT / "sections"
    sections = []

    if not sections_dir.exists():
        return {
            "kb_root": KB_ROOT.as_posix(),
            "sections": sections,
            "total_topics": 0,
            "total_chapters": 0,
        }

    total_topics = 0
    total_chapters = 0

    for section_dir in sorted([p for p in sections_dir.iterdir() if p.is_dir()]):
        topics_root = section_dir / "topics"
        topics = []

        if topics_root.exists():
            for topic_dir in sorted([p for p in topics_root.iterdir() if p.is_dir()]):
                chapter_dir = topic_dir / "chapters"
                topic_index_path = topic_dir / "TOPIC.md"

                chapters = []
                if chapter_dir.exists():
                    for chapter_path in sorted(
                        [p for p in chapter_dir.iterdir() if p.is_file() and p.suffix.lower() == ".md"],
                        reverse=True,
                    ):
                        try:
                            chapter_content = chapter_path.read_text(encoding="utf-8")
                        except OSError:
                            chapter_content = ""
                        chapter_status = _extract_chapter_status(chapter_path)
                        chapter_analysis = _extract_chapter_analysis_metrics(chapter_content)
                        chapters.append(
                            {
                                "name": chapter_path.stem,
                                "relative_path": _safe_rel_path(chapter_path, KB_ROOT),
                                "updated_at": datetime.fromtimestamp(chapter_path.stat().st_mtime).isoformat(),
                                "status": chapter_status,
                                **chapter_analysis,
                            }
                        )

                topics.append(
                    {
                        "name": topic_dir.name,
                        "relative_path": _safe_rel_path(topic_dir, KB_ROOT),
                        "topic_index": _safe_rel_path(topic_index_path, KB_ROOT)
                        if topic_index_path.exists()
                        else None,
                        "chapter_count": len(chapters),
                        "chapters": chapters,
                    }
                )

        total_topics += len(topics)
        total_chapters += sum(topic["chapter_count"] for topic in topics)
        sections.append(
            {
                "name": section_dir.name,
                "relative_path": _safe_rel_path(section_dir, KB_ROOT),
                "topic_count": len(topics),
                "topics": topics,
            }
        )

    return {
        "kb_root": KB_ROOT.as_posix(),
        "sections": sections,
        "total_topics": total_topics,
        "total_chapters": total_chapters,
    }


# ── Webpage extraction ────────────────────────────────────────────────────────

def _validate_ingestion_url(url: str) -> None:
    """Allow only HTTP(S) URLs for ingestion to prevent invalid schemes."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="Please provide a valid HTTP/HTTPS URL")


def _extract_webpage_text(url: str) -> dict:
    """Fetch webpage and return title plus paragraph snippets for ingestion."""
    response = requests.get(url, headers=_WIKIPEDIA_REQUEST_HEADERS, timeout=20)

    if response.status_code >= 400:
        if response.status_code in {401, 402, 403, 406, 429, 451}:
            parsed = urlparse(url)
            mirror_candidates = [
                f"{KB_MIRROR_BASE_URL}{url}",
                f"{KB_MIRROR_BASE_URL}http://{parsed.netloc}{parsed.path}"
                f"{'?' + parsed.query if parsed.query else ''}",
            ]

            for mirror_url in mirror_candidates:
                try:
                    mirror_response = requests.get(
                        mirror_url,
                        headers=_WIKIPEDIA_REQUEST_HEADERS,
                        timeout=25,
                    )
                    mirror_response.raise_for_status()
                    text = mirror_response.text
                    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
                    paragraphs = [line for line in lines if len(line) >= 60][:8]
                    return {
                        "title": "Mirror extract",
                        "paragraphs": paragraphs or [text[:600]],
                    }
                except requests.RequestException:
                    continue

            return {
                "title": "Blocked source (manual review required)",
                "paragraphs": [
                    (
                        f"Automated extraction was blocked by the source (HTTP {response.status_code}). "
                        "A draft chapter was created with source metadata only; add summary content manually."
                    )
                ],
            }

        response.raise_for_status()

    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    for node in soup(["script", "style", "noscript"]):
        node.decompose()

    title = (soup.title.string or "").strip() if soup.title else ""
    paragraphs = []
    for paragraph in soup.find_all("p"):
        text = paragraph.get_text(" ", strip=True)
        if len(text) >= 60:
            paragraphs.append(text)
        if len(paragraphs) >= 8:
            break

    if not paragraphs:
        body_text = soup.get_text(" ", strip=True)
        body_text = re.sub(r"\s+", " ", body_text)
        if body_text:
            paragraphs = [body_text[:600]]

    return {
        "title": title or "Untitled source",
        "paragraphs": paragraphs,
    }


# ── Auto-research and claim synthesis ────────────────────────────────────────

def _resolve_ddg_result_url(href: str) -> str:
    """Resolve direct URL from DuckDuckGo redirect links when needed."""
    parsed = urlparse(href)
    netloc = parsed.netloc.lower()
    if (netloc == "duckduckgo.com" or netloc.endswith(".duckduckgo.com")) and parsed.path.startswith("/l/"):
        query = parse_qs(parsed.query)
        if "uddg" in query and query["uddg"]:
            return unquote(query["uddg"][0])
    return href


def _discover_domain_links(topic: str, domain: str, max_links: int = AUTO_RESEARCH_MAX_LINKS_PER_DOMAIN) -> list[str]:
    """Discover topic-relevant article URLs for a specific domain."""
    query = f"site:{domain} {topic} stock trading investing"
    try:
        response = requests.get(
            "https://duckduckgo.com/html/",
            params={"q": query},
            headers=_WIKIPEDIA_REQUEST_HEADERS,
            timeout=20,
        )
        response.raise_for_status()
    except requests.RequestException:
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    links: list[str] = []
    seen: set[str] = set()

    for anchor in soup.find_all("a", href=True):
        classes = anchor.get("class") or []
        if "result__a" not in classes:
            continue

        candidate = _resolve_ddg_result_url(anchor["href"])
        parsed = urlparse(candidate)
        if parsed.scheme not in {"http", "https"}:
            continue
        if domain not in parsed.netloc.lower():
            continue
        if candidate in seen:
            continue

        seen.add(candidate)
        links.append(candidate)
        if len(links) >= max_links:
            break

    return links


def _fallback_domain_search_url(topic: str, domain: str) -> str:
    """Build domain-native search page URL for resilient ingestion fallback."""
    encoded = quote_plus(topic)
    if domain == "reuters.com":
        return f"https://www.reuters.com/site-search/?query={encoded}"
    if domain == "bloomberg.com":
        return f"https://www.bloomberg.com/search?query={encoded}"
    return f"https://www.investopedia.com/search?q={encoded}"


def _discover_topic_sources(topic: str) -> list[str]:
    """Discover candidate source URLs across Reuters, Bloomberg, and Investopedia."""
    sources: list[str] = []
    seen: set[str] = set()

    for domain in AUTO_RESEARCH_DOMAINS:
        links = _discover_domain_links(topic, domain)
        if not links:
            links = [_fallback_domain_search_url(topic, domain)]

        for link in links:
            if link in seen:
                continue
            seen.add(link)
            sources.append(link)

    return sources


def _source_domain_from_url(url: str) -> str:
    """Map source URL to a known research domain label."""
    host = urlparse(url).netloc.lower()
    for domain in AUTO_RESEARCH_DOMAINS:
        if domain in host:
            return domain
    return host or "unknown"


def _is_claim_noise(claim: str) -> bool:
    """Filter navigation/boilerplate fragments that degrade chapter quality."""
    claim_l = claim.lower().strip()
    if len(claim_l) < 70:
        return True
    if claim_l.startswith("[") and "](" in claim_l:
        return True
    if claim_l.startswith("*"):
        return True
    if any(pattern in claim_l for pattern in CLAIM_NOISE_PATTERNS):
        return True
    return False


def _is_low_quality_source_extract(source_title: str, paragraphs: list[str]) -> bool:
    """Detect blocked/noisy extraction responses that should not feed claim synthesis."""
    title_l = (source_title or "").lower()
    if "blocked source" in title_l:
        return True

    if len(paragraphs) == 1:
        body_l = paragraphs[0].lower()
        if "automated extraction was blocked" in body_l:
            return True

    return False


def _claim_quality_score(claim: str, topic: str) -> int:
    """Score claim relevance and informativeness for ranking."""
    claim_l = claim.lower()
    topic_tokens = [t for t in re.split(r"[^a-z0-9]+", topic.lower()) if len(t) >= 4]

    finance_keywords = {
        "inflation", "interest", "gdp", "yield", "bond", "earnings", "revenue",
        "equity", "volatility", "liquidity", "valuation", "fed", "central bank",
    }

    score = 0
    score += min(2, sum(1 for t in topic_tokens if t in claim_l))
    score += min(2, sum(1 for k in finance_keywords if k in claim_l))
    if 80 <= len(claim) <= 400:
        score += 1
    if not any(noise in claim_l for noise in ["subscribe", "sign up", "advertisement", "cookie"]):
        score += 1
    return score


def _rank_and_deduplicate_claims(claims: list[dict[str, str]], topic: str) -> list[str]:
    """Keep high-quality, low-duplicate claims with balanced source coverage."""
    ranked = sorted(
        claims,
        key=lambda c: _claim_quality_score(c["text"], topic),
        reverse=True,
    )

    selected: list[str] = []
    preliminary_selected: list[str] = []
    source_counts: dict[str, int] = defaultdict(int)

    def _is_duplicate(candidate: str) -> bool:
        return any(
            SequenceMatcher(None, candidate.lower(), existing.lower()).ratio() >= AUTO_RESEARCH_SIMILARITY_THRESHOLD
            for existing in selected
        )

    available_domains = {c.get("source_domain", "unknown") for c in ranked}

    # Pass 1: keep one strong claim from each available source when possible.
    for domain in available_domains:
        for claim in ranked:
            if claim.get("source_domain", "unknown") != domain:
                continue
            text = claim["text"]
            if _claim_quality_score(text, topic) < AUTO_RESEARCH_MIN_CLAIM_SCORE:
                continue
            preliminary_selected.append(text)
            break

    for text in preliminary_selected:
        if _is_duplicate(text):
            continue
        selected.append(text)
        source_domain = next(
            (c.get("source_domain", "unknown") for c in ranked if c["text"] == text),
            "unknown",
        )
        source_counts[source_domain] += 1

    # Pass 2: fill remaining slots by score while keeping source cap.
    for claim in ranked:
        text = claim["text"]
        domain = claim.get("source_domain", "unknown")
        if _claim_quality_score(text, topic) < AUTO_RESEARCH_MIN_CLAIM_SCORE:
            continue
        if source_counts[domain] >= AUTO_RESEARCH_MAX_CLAIMS_PER_SOURCE:
            continue
        if _is_duplicate(text):
            continue

        selected.append(text)
        source_counts[domain] += 1
        if len(selected) >= AUTO_RESEARCH_MAX_EXTRACTED_CLAIMS:
            break

    return selected


def _derive_trading_application_insights(topic: str, claims: list[str]) -> list[str]:
    """Transform extracted claims into actionable trading-oriented guidance."""
    insights: list[str] = []

    keyword_rules = {
        "inflation": "Map inflation surprises to rates-sensitive sectors; avoid entries before confirmation candles.",
        "interest": "Track rate expectations and rotate position sizing toward beneficiaries of the rate regime.",
        "earnings": "Use earnings catalyst windows with strict stop-loss sizing and post-event trend confirmation.",
        "gdp": "Treat GDP trend shifts as medium-horizon regime signals and rebalance sector exposure gradually.",
        "employment": "Use labor data as a volatility trigger and avoid oversized positions into release windows.",
        "oil": "Monitor energy-price shocks for second-order impacts on transportation, consumer, and inflation proxies.",
        "bond": "Watch yield-curve direction for risk-on/risk-off positioning and duration-sensitive equities.",
    }

    for claim in claims:
        claim_lower = claim.lower()
        matched_rule = None
        for keyword, rule in keyword_rules.items():
            if keyword in claim_lower:
                matched_rule = rule
                break

        if not matched_rule:
            matched_rule = (
                "Use this as context only; require price, volume, and trend confirmation "
                "before entering a trade based on the narrative."
            )

        snippet = claim if len(claim) <= 170 else f"{claim[:167]}..."
        insights.append(f"{snippet} -> {matched_rule}")

        if len(insights) >= 8:
            break

    if not insights:
        insights.append(
            f"No high-quality claims extracted for '{topic}'. Keep the chapter in Draft and add manual analyst notes."
        )

    return insights


def _extract_frontmatter_field(markdown: str, field: str) -> str:
    """Extract a frontmatter field value from markdown, if present."""
    lines = markdown.splitlines()
    if not lines or lines[0].strip() != "---":
        return ""

    field_prefix = f"{field.strip().lower()}:"
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            break
        if stripped.lower().startswith(field_prefix):
            return stripped.split(":", 1)[1].strip()
    return ""


def _extract_markdown_bullets_under_heading(markdown: str, heading: str) -> list[str]:
    """Extract bullet lines under a specific markdown heading."""
    target = heading.strip().lower()
    lines = markdown.splitlines()
    in_section = False
    bullets: list[str] = []

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("#"):
            heading_text = stripped.lstrip("#").strip().lower()
            if in_section and heading_text != target:
                break
            in_section = heading_text == target
            continue

        if in_section and stripped.startswith("- "):
            bullets.append(stripped[2:].strip())

    return bullets


def _extract_fallback_sentences(markdown: str, max_sentences: int = 3) -> list[str]:
    """Extract readable fallback sentences from markdown content."""
    without_code = re.sub(r"```.*?```", " ", markdown, flags=re.DOTALL)
    plain = re.sub(r"`[^`]*`", " ", without_code)
    plain = re.sub(r"#+\s*", " ", plain)
    plain = re.sub(r"\s+", " ", plain).strip()

    if not plain:
        return []

    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", plain)
        if len(sentence.strip()) >= 35
    ]
    return sentences[:max_sentences]


def _infer_primary_horizon(corpus: str) -> str:
    """Infer dominant prediction horizon from keyword matches."""
    scores = {key: 0 for key in PRICE_MOVEMENT_HORIZON_KEYWORDS}
    for horizon, keywords in PRICE_MOVEMENT_HORIZON_KEYWORDS.items():
        for keyword in keywords:
            if keyword in corpus:
                scores[horizon] += 1

    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "Medium-term"


def _extract_source_urls(markdown_content: str) -> list[str]:
    """Extract source URLs from chapter markdown."""
    references = _extract_markdown_bullets_under_heading(markdown_content, "References")
    urls: list[str] = []
    seen: set[str] = set()

    for line in references:
        match = re.search(r"https?://\S+", line)
        if not match:
            continue
        url = match.group(0).rstrip(").,]}")
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)

    if urls:
        return urls

    for match in re.findall(r"https?://\S+", markdown_content):
        url = match.rstrip(").,]}")
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
        if len(urls) >= 8:
            break

    return urls


def _build_source_quality_assessment(
    source_urls: list[str],
    summary: str,
    claims: list[str],
    trading_insights: list[str],
    markdown_content: str,
) -> dict:
    """Assess confidence quality of extracted source material."""
    score = 35
    reasons: list[str] = []

    unique_domains = {
        _source_domain_from_url(url)
        for url in (source_urls or [])
        if url and url.startswith(("http://", "https://"))
    }

    if unique_domains:
        domain_bonus = min(20, len(unique_domains) * 6)
        score += domain_bonus
        reasons.append(f"Diverse source domains detected ({len(unique_domains)}).")
    else:
        reasons.append("No explicit source URLs detected.")

    trusted_domains = {"reuters.com", "bloomberg.com", "investopedia.com"}
    trusted_count = len(unique_domains.intersection(trusted_domains))
    if trusted_count:
        trusted_bonus = min(20, trusted_count * 7)
        score += trusted_bonus
        reasons.append(f"Trusted market sources present ({trusted_count}).")

    if len(claims or []) >= 3:
        score += 10
        reasons.append("Sufficient extracted claims available for synthesis.")

    if len(trading_insights or []) >= 2:
        score += 8
        reasons.append("Actionable screening notes are available.")

    summary_lower = (summary or "").lower()
    markdown_lower = (markdown_content or "").lower()
    if "manual review" in summary_lower or "insufficient" in summary_lower:
        score -= 12
        reasons.append("Summary indicates limited confidence and manual review requirement.")

    if "blocked source" in markdown_lower:
        score -= 10
        reasons.append("At least one source had extraction/access limitations.")

    score = max(0, min(100, score))

    if score >= 75:
        confidence_band = "High"
    elif score >= 50:
        confidence_band = "Medium"
    else:
        confidence_band = "Low"

    return {
        "source_quality_score": score,
        "confidence_band": confidence_band,
        "confidence_reasons": reasons[:5],
    }


def _build_price_movement_analysis(
    topic: str,
    summary: str,
    claims: list[str],
    trading_insights: list[str],
    markdown_content: str = "",
    source_urls: Optional[list[str]] = None,
) -> dict:
    """Build deterministic analysis for how content improves price-movement prediction skill."""
    corpus = " ".join(
        [
            topic or "",
            summary or "",
            " ".join(claims or []),
            " ".join(trading_insights or []),
            markdown_content or "",
        ]
    ).lower()

    relevance_score = 20
    for keyword, weight in PRICE_MOVEMENT_KEYWORD_WEIGHTS.items():
        if keyword in corpus:
            relevance_score += weight

    market_dimensions: list[str] = []
    for dimension, keywords in PRICE_MOVEMENT_DIMENSION_KEYWORDS.items():
        if any(keyword in corpus for keyword in keywords):
            market_dimensions.append(dimension)
            relevance_score += 5

    relevance_score = min(100, relevance_score)

    source_assessment = _build_source_quality_assessment(
        source_urls=source_urls or [],
        summary=summary,
        claims=claims,
        trading_insights=trading_insights,
        markdown_content=markdown_content,
    )

    weighted_relevance_score = int(round((relevance_score * 0.7) + (source_assessment["source_quality_score"] * 0.3)))

    if weighted_relevance_score >= 75:
        skill_impact = "High"
    elif weighted_relevance_score >= 50:
        skill_impact = "Medium"
    else:
        skill_impact = "Low"

    key_takeaways: list[str] = []
    seen_takeaways: set[str] = set()

    for candidate in [summary, *(claims or [])]:
        text = (candidate or "").strip()
        key = text.lower()
        if not text or key in seen_takeaways:
            continue
        key_takeaways.append(text)
        seen_takeaways.add(key)
        if len(key_takeaways) >= 4:
            break

    if len(key_takeaways) < 2:
        for sentence in _extract_fallback_sentences(markdown_content, max_sentences=4):
            key = sentence.lower()
            if key in seen_takeaways:
                continue
            key_takeaways.append(sentence)
            seen_takeaways.add(key)
            if len(key_takeaways) >= 4:
                break

    if not key_takeaways:
        key_takeaways = [
            "Insufficient structured claims detected. Keep chapter in Draft and enrich with analyst notes.",
        ]

    application_notes = [insight.strip() for insight in (trading_insights or []) if insight.strip()][:4]
    if not application_notes:
        application_notes = [
            "Treat this chapter as context input and combine with trend, volume, and risk controls before execution.",
            "Use signals from this chapter to narrow watchlist candidates, then validate with technical confirmation.",
        ]

    return {
        "relevance_score": relevance_score,
        "weighted_relevance_score": weighted_relevance_score,
        "source_quality_score": source_assessment["source_quality_score"],
        "confidence_band": source_assessment["confidence_band"],
        "confidence_reasons": source_assessment["confidence_reasons"],
        "prediction_skill_impact": skill_impact,
        "primary_horizon": _infer_primary_horizon(corpus),
        "market_dimensions": market_dimensions or ["General"],
        "key_takeaways": key_takeaways,
        "application_notes": application_notes,
    }


def _build_chapter_viewer_insights(markdown_content: str, default_title: str = "") -> dict:
    """Build viewer summary and price-movement analysis for a chapter."""
    chapter_title = _extract_frontmatter_field(markdown_content, "title") or default_title

    source_summary_lines = _extract_markdown_bullets_under_heading(markdown_content, "Source Summary")
    summary = source_summary_lines[0] if source_summary_lines else ""

    if not summary:
        extracted_claims = _extract_markdown_bullets_under_heading(markdown_content, "Extracted Claims")
        summary = extracted_claims[0] if extracted_claims else ""

    if not summary:
        fallback = _extract_fallback_sentences(markdown_content, max_sentences=1)
        summary = fallback[0] if fallback else "No summary available yet."

    claims = _extract_markdown_bullets_under_heading(markdown_content, "Extracted Claims")
    trading_insights = _extract_markdown_bullets_under_heading(markdown_content, "Actionable Rules Derived")

    analysis = _build_price_movement_analysis(
        topic=chapter_title,
        summary=summary,
        claims=claims,
        trading_insights=trading_insights,
        markdown_content=markdown_content,
        source_urls=_extract_source_urls(markdown_content),
    )

    return {
        "summary": summary,
        "price_movement_analysis": analysis,
    }


def _auto_research_topic(topic: str) -> dict:
    """Run multi-source topic research and synthesize claims for chapter generation."""
    source_urls = _discover_topic_sources(topic)

    raw_claims: list[dict[str, str]] = []
    seen_claims: set[str] = set()
    used_sources: list[dict[str, str]] = []

    for source_url in source_urls:
        source_domain = _source_domain_from_url(source_url)
        try:
            extracted = _extract_webpage_text(source_url)
        except requests.RequestException:
            continue

        paragraphs = extracted.get("paragraphs", [])
        if not paragraphs:
            continue

        source_title = extracted.get("title", "Untitled source")
        if _is_low_quality_source_extract(source_title, paragraphs):
            continue

        used_sources.append({"url": source_url, "title": source_title})

        for paragraph in paragraphs:
            normalized = re.sub(r"\s+", " ", paragraph).strip()
            if len(normalized) < 60:
                continue
            if _is_claim_noise(normalized):
                continue
            key = normalized.lower()
            if key in seen_claims:
                continue
            seen_claims.add(key)
            raw_claims.append({"text": normalized, "source_domain": source_domain})

    claims = _rank_and_deduplicate_claims(raw_claims, topic)
    trading_insights = _derive_trading_application_insights(topic, claims)
    summary = claims[0] if claims else (
        "Automated discovery found limited extractable content; manual review and enrichment required."
    )

    source_title = (
        "Auto research synthesis from Reuters, Bloomberg, and Investopedia"
        if used_sources
        else "Auto research attempted (insufficient extractable source content)"
    )

    return {
        "source_title": source_title,
        "summary": summary,
        "claims": claims[:8],
        "trading_insights": trading_insights,
        "source_urls": [item["url"] for item in used_sources],
        "source_labels": [f"{item['title']} ({item['url']})" for item in used_sources],
    }


# ── Chapter status management ─────────────────────────────────────────────────

def _apply_chapter_status_update(chapter_path: Path, new_status: str, note: Optional[str]) -> None:
    """Update chapter frontmatter status and append review note for auditability."""
    content = chapter_path.read_text(encoding="utf-8")
    lines = content.splitlines()

    if len(lines) < 3 or lines[0].strip() != "---":
        raise HTTPException(status_code=400, detail="Chapter missing valid frontmatter")

    end_idx = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end_idx = idx
            break

    if end_idx is None:
        raise HTTPException(status_code=400, detail="Chapter frontmatter is not closed")

    frontmatter = lines[1:end_idx]
    body = lines[end_idx + 1:]

    def _set_frontmatter_field(field: str, value: str) -> None:
        prefix = f"{field}:"
        for i, line in enumerate(frontmatter):
            if line.startswith(prefix):
                frontmatter[i] = f"{field}: {value}"
                return
        frontmatter.append(f"{field}: {value}")

    _set_frontmatter_field("status", new_status)
    _set_frontmatter_field("last_reviewed", datetime.now().strftime("%Y-%m-%d"))

    review_lines = [
        "",
        "# Review Decision",
        f"- Status updated to {new_status} on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}.",
    ]
    if note and note.strip():
        review_lines.append(f"- Note: {note.strip()}")

    updated_content = "\n".join(
        ["---", *frontmatter, "---", *body, *review_lines]
    ).strip() + "\n"
    chapter_path.write_text(updated_content, encoding="utf-8")


# ── GitHub write-back ─────────────────────────────────────────────────────────

def _github_headers() -> dict:
    """Build GitHub API auth headers from GITHUB_TOKEN env var."""
    token = config.GITHUB_TOKEN
    if not token:
        return {}
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _github_commit_files(files: dict[str, str], commit_message: str) -> bool:
    """Commit one or more files to GitHub via the Contents API.

    files: mapping of repo-relative POSIX path -> file content (UTF-8 text).
    Returns True on success, False if GITHUB_TOKEN is absent or call fails.
    """
    token = config.GITHUB_TOKEN
    if not token:
        return False

    repo = config.GITHUB_REPO
    branch = config.GITHUB_BRANCH
    headers = _github_headers()
    success = True

    for repo_path, content_text in files.items():
        encoded = base64.b64encode(content_text.encode("utf-8")).decode("ascii")
        api_url = f"{GITHUB_API_BASE}/repos/{repo}/contents/{repo_path}"

        existing_sha = None
        try:
            get_resp = requests.get(api_url, headers=headers, params={"ref": branch}, timeout=10)
            if get_resp.status_code == 200:
                existing_sha = get_resp.json().get("sha")
        except requests.RequestException:
            pass

        body: dict = {
            "message": commit_message,
            "content": encoded,
            "branch": branch,
        }
        if existing_sha:
            body["sha"] = existing_sha

        try:
            put_resp = requests.put(api_url, headers=headers, json=body, timeout=15)
            if put_resp.status_code not in {200, 201}:
                logger.warning(
                    "GitHub write-back failed for %s: %s %s",
                    repo_path,
                    put_resp.status_code,
                    put_resp.text[:200],
                )
                success = False
        except requests.RequestException as exc:
            logger.warning("GitHub write-back request error for %s: %s", repo_path, exc)
            success = False

    return success


# ── OS explorer integration ───────────────────────────────────────────────────

def _open_in_explorer(target: Path) -> None:
    """Open file location in OS explorer/finder for local workflows."""
    if os.name == "nt":
        subprocess.run(["explorer", "/select,", str(target)], check=False)
        return

    if os.name == "posix":
        if platform.system().lower() == "darwin":
            subprocess.run(["open", "-R", str(target)], check=False)
        else:
            subprocess.run(["xdg-open", str(target.parent)], check=False)
        return

    raise RuntimeError("Unsupported operating system for explorer integration")
