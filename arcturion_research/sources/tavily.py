"""Tavily grounded web search. Key: environment variable TAVILY_API_KEY."""
import requests

from ..http import headers, require_key

ENDPOINT = "https://api.tavily.com/search"



def search(query: str, *, max_results: int = 8, search_depth: str = "advanced",
           include_images: bool = True, include_raw_content: bool = False,
           topic: str = "general") -> dict:
    key = require_key("TAVILY_API_KEY")
    body = {
        "api_key": key,
        "query": query,
        "search_depth": search_depth,
        "include_images": include_images,
        "include_image_descriptions": include_images,
        "include_raw_content": include_raw_content,
        "max_results": max_results,
        "topic": topic,
    }
    r = requests.post(ENDPOINT, json=body, headers=headers(), timeout=30)
    r.raise_for_status()
    return r.json()


def normalize(resp: dict) -> list[dict]:
    out = []
    for item in resp.get("results", []) or []:
        out.append({
            "source": "tavily",
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": (item.get("content") or "")[:600],
            "score": item.get("score", 0.0),
            "published": item.get("published_date", ""),
        })
    for img in resp.get("images", []) or []:
        if isinstance(img, dict):
            out.append({
                "source": "tavily-image",
                "url": img.get("url", ""),
                "description": img.get("description", ""),
                "type": "image",
            })
        elif isinstance(img, str):
            out.append({"source": "tavily-image", "url": img, "type": "image"})
    if resp.get("answer"):
        out.append({"source": "tavily-answer", "text": resp["answer"], "type": "answer"})
    return out
