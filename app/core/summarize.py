
import json

MAX_LIST_ENTRIES = 25
MAX_RESULT_CHARS = 2000

# Backstop for file contents, independent of what the tool returned.
MAX_CONTENT_CHARS = 6000

# Per search result. Five of these is the whole web-search budget.
MAX_SNIPPET_CHARS = 1200


def for_model(tool_name: str, result: dict) -> str:
    """Compact a tool result down to what the model actually needs to reason."""
    if not isinstance(result, dict):
        return str(result)[:MAX_RESULT_CHARS]

    if tool_name == "list_directory" and result.get("success"):
        entries = result.get("entries", [])

        payload = {
            "success": True,
            "path": result.get("path"),
            "count": len(entries),
            "entries": [e["name"] for e in entries[:MAX_LIST_ENTRIES]],
        }

        if len(entries) > MAX_LIST_ENTRIES:
            payload["truncated"] = len(entries) - MAX_LIST_ENTRIES

        return json.dumps(payload)

    # Search results now carry page text, which is the point -- but five
    # full pages would overflow the context window on their own.
    if tool_name == "search_web" and result.get("success"):
        return json.dumps({
            "success": True,
            "query": result.get("query"),
            "answer": result.get("answer"),
            "links_only": result.get("links_only", False),
            "results": [
                {
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "content": (item.get("content") or "")[:MAX_SNIPPET_CHARS],
                }
                for item in result.get("results", [])[:5]
            ],
        })

    if tool_name == "find_files" and result.get("success"):
        results = result.get("results", [])

        return json.dumps({
            "success": True,
            "query": result.get("query"),
            "count": result.get("count"),
            "truncated": result.get("truncated"),
            "results": [
                {"name": item["name"], "path": item["path"]}
                for item in results[:MAX_LIST_ENTRIES]
            ],
        })

    # File contents get a bigger allowance than other results, but still a
    # hard one: a 16k-character PDF is ~4000 tokens and overflowed the
    # server's 8192-token window on its own.
    if tool_name == "read_file" and result.get("success"):
        content = result.get("content", "")
        truncated = bool(result.get("truncated"))

        if len(content) > MAX_CONTENT_CHARS:
            content = content[:MAX_CONTENT_CHARS]
            truncated = True

        return json.dumps({
            "success": True,
            "name": result.get("name"),
            "path": result.get("path"),
            "pages": result.get("pages"),
            "truncated": truncated,
            "content": content,
        })

    return json.dumps(result)[:MAX_RESULT_CHARS]
