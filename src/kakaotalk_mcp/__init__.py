"""KakaoTalk MCP Server — read & send KakaoTalk messages from Claude Code on macOS."""

from .reader import KakaoReader, load_chat_ids

__version__ = "0.1.0"
__all__ = ["KakaoReader", "load_chat_ids"]
