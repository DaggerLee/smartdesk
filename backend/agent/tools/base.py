from typing import Protocol, runtime_checkable


@runtime_checkable
class Tool(Protocol):
    """Interface every agent tool must satisfy.

    `declaration` must be a valid Gemini functionDeclaration dict, e.g.:
        {
            "name": "retrieve",
            "description": "Search the knowledge base for relevant chunks.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        }
    The agent loop passes all declarations to complete() so the LLM can choose
    which tool to call; it then dispatches to run() with the args the LLM returned.
    """

    name: str
    description: str
    declaration: dict  # Gemini functionDeclaration schema

    def run(self, **kwargs) -> dict:
        """Execute the tool and return a JSON-serialisable result dict."""
        ...
