from dataclasses import dataclass
from typing import Optional, Tuple, List, Dict
from enum import Enum
import re
from backend.config import GROQ_API_KEY, GROQ_MODEL, ROUTER_TEMPERATURE, groq_model_kwargs


class RouteType(str, Enum):
    """Types of query routes."""
    KNOWLEDGE = "KNOWLEDGE"
    META = "META"
    GREETING = "GREETING"
    CLARIFICATION = "CLARIFICATION"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    SUMMARY = "SUMMARY"
    COMPARISON = "COMPARISON"
    FOLLOW_UP = "FOLLOW_UP"


# Keyword patterns for fast pre-filtering (avoids LLM call)
GREETING_PATTERNS = [
    r"^(hi|hello|hey|howdy|greetings|good\s*(morning|afternoon|evening))(\s+there)?[\s!?.]*$",
    r"^(thanks|thank\s*you|thx|ty)[\s!?.]*$",
    r"^(bye|goodbye|see\s*you|later)[\s!?.]*$",
    r"^(how\s*are\s*you|what'?s\s*up|sup)[\s!?.]*$",
]

META_PATTERNS = [
    r"(what|which|list|show|display).*(documents?|files?|sources?).*(have|uploaded|available|stored)",
    r"(what|how\s*many).*(documents?|files?).*(do\s*i|i)\s*have",
    r"^(list|show)\s*(my\s*)?(documents?|files?|sources?)[\s!?.]*$",
    r"what\s*(can|do)\s*you\s*(do|know|have)",
    r"(your|the)\s*capabilities",
]

SUMMARY_PATTERNS = [
    r"^summarize\s+",
    r"^(give|provide|create)\s*(me\s*)?(a\s*)?summary",
    r"^(what\s*is\s*the\s*)?(main|key)\s*(points?|ideas?|takeaways?)",
    r"^tldr\s*",
    r"summarize\s*(this|the|that)",
]

COMPARISON_PATTERNS = [
    r"compare\s+.+\s+(and|with|to|vs\.?)\s+",
    r"(difference|differences)\s*(between|of)",
    r"(how|what).*(different|similar|compare)",
    r".+\s+vs\.?\s+.+",
]

CLARIFICATION_PATTERNS = [
    r"^(more|details?|explain|elaborate|continue)[\s!?.]*$",
    r"^(yes|no|ok|okay|sure|yep|nope)[\s!?.]*$",
    r"^(what|huh|hmm)[\s!?.]*$",
]

# Coreference detection patterns for rewrite optimization
PRONOUN_PATTERNS = [
    r'\b(it|its|this|that|these|those|they|them|their|he|she|him|her|his)\b',
    r'\b(the document|the file|the book|the article|the text)\b',
    r'\b(above|previous|earlier|last|before|mentioned)\b',
]


@dataclass
class RouteResult:
    """Result of query classification."""
    route_type: RouteType
    confidence: float = 1.0
    reasoning: Optional[str] = None
    rewritten_query: Optional[str] = None


REWRITE_PROMPT = """You are a coreference resolution system. Given conversation history and a follow-up query, rewrite the query to be fully self-contained.

Rules:
1. Replace ALL pronouns (it, they, that, this, etc.) with their actual referents from the conversation
2. Replace vague references ("the document", "the book", "the concept") with specific names
3. If the query references "more about X" or "explain further", include what X refers to
4. Keep the rewritten query concise and natural
5. If the query is already self-contained, return it unchanged
6. Do NOT add information that wasn't in the conversation

Chat History:
{history}

Latest Query: "{query}"

Rewritten Query (self-contained, no pronouns or vague references):"""


CLASSIFY_AND_CORRECT_PROMPT = """You are a query classifier for a personal knowledge base assistant.

1. Fix any spelling mistakes or typos in the query
2. Classify it into ONE category: KNOWLEDGE, SUMMARY, COMPARISON, FOLLOW_UP, META, GREETING, CLARIFICATION, OUT_OF_SCOPE

Respond in this EXACT format (category | corrected query):
CATEGORY | corrected query

Examples:
- "explain the concpt on the boks" → KNOWLEDGE | explain the concept on the books
- "summarize the main topcs" → SUMMARY | summarize the main topics
- "hello" → GREETING | hello
- "what documets do i have" → META | what documents do i have
- "what is brahmacharya" → KNOWLEDGE | what is brahmacharya
- "explain each document in detail" → META | explain each document in detail
- "tell me about all my books" → META | tell me about all my books
- "what does every document talk about" → META | what does every document talk about

Categories:
KNOWLEDGE - Questions seeking information from documents
SUMMARY - Requests to summarize documents or topics
COMPARISON - Compare two or more things
FOLLOW_UP - References previous context
META - Questions about the system, what documents exist, document overviews, or capabilities. Includes queries about "each/all/every document" or asking what the collection contains.
GREETING - Greetings, small talk
CLARIFICATION - Too vague or ambiguous
OUT_OF_SCOPE - Outside capabilities (code, emails, web, calculations)

Query: "{query}"
"""


CLASSIFICATION_PROMPT = """You are a query classifier for a personal knowledge base assistant. Classify the user's query into ONE of these categories:

KNOWLEDGE - Questions seeking specific information from documents
SUMMARY - Requests to summarize documents or topics
COMPARISON - Compare two or more things
FOLLOW_UP - Follow-up questions referencing previous context
META - Questions about the system itself, what documents exist, or capabilities
GREETING - Greetings, thanks, small talk
CLARIFICATION - Query is too vague or ambiguous
OUT_OF_SCOPE - Requests outside capabilities

User Query: "{query}"

Respond with ONLY the category name, nothing else."""


# LangChain LCEL chain initialization (lazy)
_classify_chain = None
_rewrite_chain = None
_llm = None


def _get_chains():
    """Lazy-initialize LangChain LCEL chains."""
    global _classify_chain, _rewrite_chain, _llm

    if _classify_chain is not None:
        return _classify_chain, _rewrite_chain

    if not GROQ_API_KEY:
        return None, None

    from langchain_groq import ChatGroq
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.output_parsers import StrOutputParser

    _llm = ChatGroq(
        api_key=GROQ_API_KEY,
        model_name=GROQ_MODEL,
        temperature=ROUTER_TEMPERATURE,
        max_tokens=150,
        **groq_model_kwargs(),
    )

    # Classification chain: prompt -> LLM -> parse string output
    classify_prompt = ChatPromptTemplate.from_messages([
        ("user", CLASSIFY_AND_CORRECT_PROMPT)
    ])
    _classify_chain = classify_prompt | _llm | StrOutputParser()

    # Rewrite chain: prompt -> LLM -> parse string output
    rewrite_prompt = ChatPromptTemplate.from_messages([
        ("user", REWRITE_PROMPT)
    ])
    _rewrite_chain = rewrite_prompt | _llm | StrOutputParser()

    return _classify_chain, _rewrite_chain


class QueryRouter:
    """Routes queries to appropriate handlers using LangChain LCEL classification."""

    def __init__(self):
        # Compile regex patterns for faster matching
        self._greeting_re = [re.compile(p, re.IGNORECASE) for p in GREETING_PATTERNS]
        self._meta_re = [re.compile(p, re.IGNORECASE) for p in META_PATTERNS]
        self._summary_re = [re.compile(p, re.IGNORECASE) for p in SUMMARY_PATTERNS]
        self._comparison_re = [re.compile(p, re.IGNORECASE) for p in COMPARISON_PATTERNS]
        self._clarification_re = [re.compile(p, re.IGNORECASE) for p in CLARIFICATION_PATTERNS]
        self._pronoun_re = [re.compile(p, re.IGNORECASE) for p in PRONOUN_PATTERNS]

    def _keyword_prefilter(self, query: str) -> Optional[Tuple[RouteType, str]]:
        """
        Fast keyword-based routing for obvious cases.
        Returns (RouteType, reasoning) if matched, None otherwise.
        """
        query_clean = query.strip()

        for pattern in self._greeting_re:
            if pattern.search(query_clean):
                return (RouteType.GREETING, "Keyword match: greeting pattern")

        for pattern in self._meta_re:
            if pattern.search(query_clean):
                return (RouteType.META, "Keyword match: meta/system query")

        for pattern in self._summary_re:
            if pattern.search(query_clean):
                return (RouteType.SUMMARY, "Keyword match: summary request")

        for pattern in self._comparison_re:
            if pattern.search(query_clean):
                return (RouteType.COMPARISON, "Keyword match: comparison request")

        if len(query_clean.split()) <= 2:
            for pattern in self._clarification_re:
                if pattern.search(query_clean):
                    return (RouteType.CLARIFICATION, "Keyword match: vague/unclear query")

        return None

    def _needs_rewrite(self, query: str) -> bool:
        """Check if query contains references that need coreference resolution."""
        for pattern in self._pronoun_re:
            if pattern.search(query):
                return True
        return False

    def _format_history(self, chat_history: List[Dict[str, str]]) -> str:
        """Format chat history into a readable string."""
        lines = []
        for msg in chat_history:
            role = msg.get("role", "user").capitalize()
            content = msg.get("content", "")
            if len(content) > 500:
                content = content[:500] + "..."
            lines.append(f"{role}: {content}")
        return "\n".join(lines)

    def _parse_classify_output(self, raw: str) -> Tuple[RouteType, Optional[str]]:
        """Parse 'CATEGORY | corrected query' into (RouteType, Optional[str])."""
        if "|" in raw:
            parts = raw.split("|", 1)
            category = parts[0].strip().upper()
            corrected = parts[1].strip().strip('"\'')
            route_type = self._parse_response(category)
            return (route_type, corrected if corrected else None)
        return (self._parse_response(raw.strip().upper()), None)

    def _parse_response(self, response: str) -> RouteType:
        """Parse the LLM response into a RouteType."""
        response = response.strip().upper()

        route_map = {
            "KNOWLEDGE": RouteType.KNOWLEDGE,
            "META": RouteType.META,
            "GREETING": RouteType.GREETING,
            "CLARIFICATION": RouteType.CLARIFICATION,
            "OUT_OF_SCOPE": RouteType.OUT_OF_SCOPE,
            "SUMMARY": RouteType.SUMMARY,
            "COMPARISON": RouteType.COMPARISON,
            "FOLLOW_UP": RouteType.FOLLOW_UP,
        }

        if response in route_map:
            return route_map[response]

        for key, route_type in route_map.items():
            if key in response:
                return route_type

        return RouteType.KNOWLEDGE

    def classify_fast(self, query: str) -> RouteResult:
        """Keyword-only routing (no LLM call); anything unmatched is KNOWLEDGE."""
        prefilter_result = self._keyword_prefilter(query)
        if prefilter_result:
            route_type, reasoning = prefilter_result
            return RouteResult(route_type=route_type, confidence=0.9, reasoning=reasoning)
        return RouteResult(route_type=RouteType.KNOWLEDGE, confidence=0.7, reasoning="No keyword match")

    async def classify(self, query: str, chat_history: Optional[List[Dict[str, str]]] = None) -> RouteResult:
        """
        Classify a query into a route type using LangChain LCEL chains.

        Uses keyword pre-filter first for speed, falls back to LangChain LCEL for complex cases.
        When chat_history is provided, rewrites referential queries before classification.
        """
        # Try fast keyword pre-filter first
        prefilter_result = self._keyword_prefilter(query)
        if prefilter_result:
            route_type, reasoning = prefilter_result
            return RouteResult(
                route_type=route_type,
                confidence=0.9,
                reasoning=reasoning
            )

        classify_chain, rewrite_chain = _get_chains()

        # No chat history: single LangChain LCEL call for classify + correct
        if not chat_history:
            if not classify_chain:
                return RouteResult(route_type=RouteType.KNOWLEDGE, confidence=0.5, reasoning="No LLM available")

            try:
                raw = await classify_chain.ainvoke({"query": query})
                route_type, corrected = self._parse_classify_output(raw)
                if corrected and corrected.lower() == query.lower():
                    corrected = None
                return RouteResult(
                    route_type=route_type,
                    confidence=1.0,
                    reasoning="LangChain LCEL classify+correct",
                    rewritten_query=corrected
                )
            except Exception:
                return RouteResult(route_type=RouteType.KNOWLEDGE, confidence=0.5, reasoning="Classification error")

        # With chat history: rewrite first (if coreference detected), then classify
        rewritten_query = None
        if rewrite_chain and self._needs_rewrite(query):
            try:
                history_str = self._format_history(chat_history)
                rewritten = await rewrite_chain.ainvoke({"history": history_str, "query": query})
                rewritten = rewritten.strip()
                if rewritten and rewritten.lower() != query.lower():
                    rewritten_query = rewritten
            except Exception:
                pass

        classify_query = rewritten_query or query

        if not classify_chain:
            return RouteResult(
                route_type=RouteType.KNOWLEDGE,
                confidence=0.5,
                reasoning="No LLM available",
                rewritten_query=rewritten_query
            )

        try:
            # Build a simple classification-only LCEL chain for the rewritten query
            from langchain_core.prompts import ChatPromptTemplate
            from langchain_core.output_parsers import StrOutputParser

            classify_only_prompt = ChatPromptTemplate.from_messages([
                ("user", CLASSIFICATION_PROMPT)
            ])
            classify_only_chain = classify_only_prompt | _llm | StrOutputParser()

            raw = await classify_only_chain.ainvoke({"query": classify_query})
            route_type = self._parse_response(raw)

            return RouteResult(
                route_type=route_type,
                confidence=1.0,
                reasoning="LangChain LCEL rewrite+classify",
                rewritten_query=rewritten_query
            )
        except Exception:
            return RouteResult(
                route_type=RouteType.KNOWLEDGE,
                confidence=0.5,
                reasoning="Classification error",
                rewritten_query=rewritten_query
            )

    def classify_sync(self, query: str) -> RouteResult:
        """Synchronous version of classify."""
        prefilter_result = self._keyword_prefilter(query)
        if prefilter_result:
            route_type, reasoning = prefilter_result
            return RouteResult(
                route_type=route_type,
                confidence=0.9,
                reasoning=reasoning
            )

        classify_chain, _ = _get_chains()
        if not classify_chain:
            return RouteResult(
                route_type=RouteType.KNOWLEDGE,
                confidence=0.5,
                reasoning="No LLM available"
            )

        try:
            raw = classify_chain.invoke({"query": query})
            route_type, corrected = self._parse_classify_output(raw)
            if corrected and corrected.lower() == query.lower():
                corrected = None
            return RouteResult(
                route_type=route_type,
                confidence=1.0,
                reasoning="LangChain LCEL classify+correct",
                rewritten_query=corrected
            )
        except Exception:
            return RouteResult(
                route_type=RouteType.KNOWLEDGE,
                confidence=0.5,
                reasoning="Classification error"
            )
