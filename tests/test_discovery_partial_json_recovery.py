"""Pins the regression where discovery's JSON output got truncated by
max_tokens and the orchestrator silently dropped ALL discovered products.

THE ACTUAL ROOT CAUSE behind the cards-don't-render symptom (caught by
adding server-side _dbg lines to chat.py and reading the logs from a real
"i want shirts from kith" turn):

  - DiscoveryAgent had max_tokens=2048.
  - Real Kith catalogue has 4+ products with verbose descriptions
    (Stone Island specs, WTAPS materials, etc.).
  - The JSON output exceeded 2048 tokens and was truncated mid-string:
        ```json
        {"products":[{"name":"X","description":"Long text..."},{"name":"Y","desc...
                                                                  ^^^ TRUNCATED
  - parse_response() in agents/base.py fell through every parse attempt
    and returned {"parse_error": "non_json", "raw": "<truncated text>"}.
  - The orchestrator's storage step requires `result["products"]` to be a
    list (orchestrator.py:384). With parse_error there's no products key,
    so last_discovered_products is NEVER updated.
  - chat.py's emit-decision block sees products_before == products_after
    (both empty) and doesn't emit the products SSE event.
  - No products event → no fetch → no cards. User sees the agent describe
    products in prose (because the model has its own context) but no cards.

Two fixes:
  1. Bumped DiscoveryAgent.max_tokens 2048 → 8192.
  2. Added _recover_partial_json() so even if the response is truncated,
     the COMPLETE items in the products array are salvaged.

This test pins both fixes against regression.
"""

from __future__ import annotations

from agents.base import BaseAgent


class _FakeClient:
    """Minimal stand-in — parse_response doesn't actually use the client."""

    class messages:
        @staticmethod
        async def create(**_):
            raise RuntimeError("parse_response tests should not call the client")


class _ConcreteAgent(BaseAgent):
    model = "test-model"
    system_prompt = "test"
    tool_specs: list = []

    def __init__(self):
        super().__init__(_FakeClient())


class TestPartialJsonRecovery:
    def test_complete_json_unchanged(self):
        """Recovery is a no-op when the JSON is complete and well-formed."""
        text = '{"products": [{"name": "x"}, {"name": "y"}], "notes": "ok"}'
        result = _ConcreteAgent()._parse_final(text)
        assert result == {
            "products": [{"name": "x"}, {"name": "y"}],
            "notes": "ok",
        }

    def test_complete_json_in_markdown_fence(self):
        text = '```json\n{"products": [{"name": "x"}], "notes": "ok"}\n```'
        result = _ConcreteAgent()._parse_final(text)
        assert result["products"] == [{"name": "x"}]

    def test_truncated_in_array_recovers_complete_items(self):
        """The discovery agent's actual failure mode: response was cut off
        mid-string on the LAST item. The PREVIOUSLY-completed items must
        still be salvaged so the orchestrator sees them."""
        truncated = (
            "```json\n"
            '{"products": ['
            '{"product_id": "a", "name": "First Item"},'
            '{"product_id": "b", "name": "Second Item"},'
            '{"product_id": "c", "name": "Third Item Truncate'
        )
        result = _ConcreteAgent()._parse_final(truncated)
        assert "products" in result, f"Expected products key, got: {result}"
        # The two FULLY-formed items should be recovered.
        assert len(result["products"]) == 2
        assert result["products"][0] == {"product_id": "a", "name": "First Item"}
        assert result["products"][1] == {"product_id": "b", "name": "Second Item"}

    def test_truncated_with_no_complete_items_returns_empty_array(self):
        """If truncation hit the very first item, return an empty array
        (still a valid response shape) so the orchestrator can move on."""
        truncated = (
            '```json\n{"products": [{"product_id": "a", "name": "First Item Truncated mid-str'
        )
        result = _ConcreteAgent()._parse_final(truncated)
        assert "products" in result
        assert result["products"] == []
        assert result.get("parse_recovered") == "empty_array_after_truncation"

    def test_truncated_with_nested_objects_inside_item(self):
        """Items may contain nested objects (attributes). Recovery must
        respect nested brace depth and not stop early."""
        truncated = (
            '{"products": ['
            '{"id": "a", "attributes": {"color": "red", "size": "M"}},'
            '{"id": "b", "attributes": {"color": "blue", "size": "L"}},'
            '{"id": "c", "attributes": {"color": "green", "siz'
        )
        result = _ConcreteAgent()._parse_final(truncated)
        assert "products" in result
        assert len(result["products"]) == 2
        assert result["products"][0]["attributes"]["color"] == "red"
        assert result["products"][1]["attributes"]["color"] == "blue"

    def test_truncated_with_escaped_quotes_in_strings(self):
        """Don't get confused by `\\"` inside a string when tracking depth."""
        truncated = (
            '{"products": ['
            '{"id": "a", "name": "Item with \\"quotes\\" inside"},'
            '{"id": "b", "name": "Another \\"quoted\\" item"},'
            '{"id": "c", "name": "Truncated half\\"way'
        )
        result = _ConcreteAgent()._parse_final(truncated)
        assert "products" in result
        assert len(result["products"]) == 2
        assert "quotes" in result["products"][0]["name"]

    def test_no_json_at_all_returns_parse_error(self):
        """Random text with no JSON structure still gets the original error."""
        text = "this is just prose, not JSON at all"
        result = _ConcreteAgent()._parse_final(text)
        assert result == {"parse_error": "non_json", "raw": text}

    def test_empty_response_returns_empty_error(self):
        result = _ConcreteAgent()._parse_final("")
        assert result == {"parse_error": "empty_response"}

    def test_ranked_array_recovery(self):
        """The same recovery works for evaluation agent output (ranked
        array instead of products)."""
        truncated = (
            '{"ranked": ['
            '{"product_id": "a", "score": 0.9},'
            '{"product_id": "b", "score": 0.8},'
            '{"product_id": "c", "score": 0.7, "rationale": "good val'
        )
        result = _ConcreteAgent()._parse_final(truncated)
        assert "ranked" in result
        assert len(result["ranked"]) == 2


class TestDiscoveryMaxTokensBumped:
    """The token-limit bump from 2048 to 8192 must remain in place."""

    def test_discovery_agent_max_tokens_at_least_8192(self):
        from agents.discovery import DiscoveryAgent

        assert DiscoveryAgent.max_tokens >= 8192, (
            "DiscoveryAgent.max_tokens was bumped from 2048 to 8192 to fit "
            "real-merchant catalogues like Kith. Reducing it risks regressing "
            "the cards-don't-render bug — discovery JSON gets truncated and "
            "the orchestrator drops every product silently."
        )
