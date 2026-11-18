"""Exercise public FastMCP Client APIs in memory and over real stdio pipes."""

import json
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from fastmcp import Client, FastMCP
from fastmcp.client.transports import StdioTransport
from fastmcp.exceptions import ToolError

from server import mcp


PROJECT_DIR = Path(__file__).resolve().parents[1]
NAMES = [
    "MoonMuffin Help",
    "NimbusNoodle Support",
    "PixelPuff Assist",
    "WobbleWidget Care",
]
EXPECTED_PROFILE = {
    "name": "NimbusNoodle Support",
    "market": "AI customer support for small retailers",
    "strengths": ["Human handoff", "Order-status templates", "Simple setup"],
    "weaknesses": ["Limited language coverage", "No voice support"],
    "website": "https://nimbusnoodle.example",
}


@pytest_asyncio.fixture
async def client() -> AsyncIterator[Client]:
    """Use FastMCP's supported in-memory transport, with no mock server."""
    async with Client(mcp) as connected_client:
        yield connected_client


def test_server_import_is_quiet() -> None:
    """Importing registers components without starting a server or printing."""
    result = subprocess.run(
        [sys.executable, "-c", "import server; assert server.mcp.name == 'MarketIntel'"],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    assert result.stdout == ""
    assert isinstance(mcp, FastMCP)


@pytest.mark.asyncio
async def test_tools_are_discoverable(client: Client) -> None:
    """Discovery exposes exactly the three tools and their argument schemas."""
    tools = {tool.name: tool for tool in await client.list_tools()}
    assert set(tools) == {"get_competitor", "list_competitors", "compare_competitors"}
    assert tools["get_competitor"].input_schema["required"] == ["name"]
    assert tools["get_competitor"].input_schema["properties"]["name"]["type"] == "string"
    assert set(tools["compare_competitors"].input_schema["required"]) == {"name1", "name2"}
    assert tools["list_competitors"].input_schema.get("required", []) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["NimbusNoodle Support", "  nimbusnoodle SUPPORT  "])
async def test_get_competitor(client: Client, name: str) -> None:
    """Lookups return the complete expected profile and normalize names."""
    result = await client.call_tool("get_competitor", {"name": name})
    assert not result.is_error
    assert result.data == EXPECTED_PROFILE
    assert json.loads(json.dumps(result.data)) == EXPECTED_PROFILE


@pytest.mark.asyncio
async def test_list_competitors(client: Client) -> None:
    """The listing supplies all names a student can use in subsequent calls."""
    result = await client.call_tool("list_competitors")
    assert result.data == NAMES


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["Missing Company", "", "   ", "Nimbus"])
async def test_unknown_competitor_error(client: Client, name: str) -> None:
    """Unknown, blank and partial names produce actionable MCP error results."""
    result = await client.call_tool("get_competitor", {"name": name}, raise_on_error=False)
    assert result.is_error
    assert "Competitor not found" in result.content[0].text
    assert repr(name.strip()) in result.content[0].text
    assert "list_competitors" in result.content[0].text


@pytest.mark.asyncio
async def test_client_raises_tool_error(client: Client) -> None:
    """The default client behavior raises FastMCP's documented ToolError."""
    with pytest.raises(ToolError, match="Competitor not found"):
        await client.call_tool("get_competitor", {"name": "Missing Company"})


@pytest.mark.asyncio
async def test_required_tool_argument(client: Client) -> None:
    """FastMCP validates missing required arguments before running the function."""
    result = await client.call_tool("get_competitor", {}, raise_on_error=False)
    assert result.is_error


@pytest.mark.asyncio
async def test_comparison(client: Client) -> None:
    """Comparison returns both profiles and only literal shared field values."""
    arguments = {"name1": "NimbusNoodle Support", "name2": "PixelPuff Assist"}
    result = await client.call_tool("compare_competitors", arguments)
    assert result.data["competitor1"] == EXPECTED_PROFILE
    assert result.data["competitor2"]["name"] == "PixelPuff Assist"
    assert result.data["same_market"] is True
    assert result.data["shared_strengths"] == ["Human handoff"]
    assert result.data["shared_weaknesses"] == ["No voice support"]
    assert "Fictional" in result.data["limitations"]
    repeated = await client.call_tool("compare_competitors", arguments)
    assert repeated.data == result.data


@pytest.mark.asyncio
async def test_different_markets(client: Client) -> None:
    """Companies in different stated segments are not marked as the same market."""
    result = await client.call_tool(
        "compare_competitors", {"name1": "NimbusNoodle Support", "name2": "MoonMuffin Help"}
    )
    assert result.data["same_market"] is False
    assert result.data["shared_strengths"] == ["Simple setup"]
    assert result.data["shared_weaknesses"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name1,name2,missing",
    [
        ("Missing One", "PixelPuff Assist", ["Missing One"]),
        ("NimbusNoodle Support", "Missing Two", ["Missing Two"]),
        ("Missing One", "Missing Two", ["Missing One", "Missing Two"]),
    ],
)
async def test_comparison_identifies_missing(
    client: Client, name1: str, name2: str, missing: list[str]
) -> None:
    """Report either missing competitor, including both when both are absent."""
    result = await client.call_tool(
        "compare_competitors", {"name1": name1, "name2": name2}, raise_on_error=False
    )
    assert result.is_error
    for name in missing:
        assert name in result.content[0].text


@pytest.mark.asyncio
async def test_resources_are_discoverable_and_readable(client: Client) -> None:
    """Both URIs are listed and deliver their intended contextual content."""
    resources = await client.list_resources()
    assert {str(resource.uri) for resource in resources} == {
        "market://overview", "market://competitors"
    }
    overview = await client.read_resource("market://overview")
    assert "fictional" in overview[0].text.lower()
    assert "limitations" in overview[0].text.lower()
    dataset = await client.read_resource("market://competitors")
    profiles = json.loads(dataset[0].text)
    assert sorted(profile["name"] for profile in profiles) == NAMES
    nimbus = next(
        profile for profile in profiles if profile["name"] == EXPECTED_PROFILE["name"]
    )
    assert nimbus == EXPECTED_PROFILE
    for profile in profiles:
        assert set(profile) == {"name", "market", "strengths", "weaknesses", "website"}
        assert profile["website"].endswith(".example")


@pytest.mark.asyncio
async def test_prompts_are_discoverable(client: Client) -> None:
    """Discovery shows both reusable templates and their required parameters."""
    prompts = {prompt.name: prompt for prompt in await client.list_prompts()}
    assert set(prompts) == {"competitor_analysis", "market_summary"}
    assert prompts["competitor_analysis"].arguments[0].name == "name"
    assert prompts["competitor_analysis"].arguments[0].required
    assert prompts["market_summary"].arguments[0].name == "industry"
    assert prompts["market_summary"].arguments[0].required


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prompt_name,arguments,expected",
    [
        ("competitor_analysis", {"name": "NimbusNoodle Support"}, "NimbusNoodle Support"),
        ("market_summary", {"industry": "AI customer support"}, "AI customer support"),
    ],
)
async def test_prompt_retrieval(
    client: Client, prompt_name: str, arguments: dict[str, str], expected: str
) -> None:
    """Retrieving a prompt returns instructions, including the fictional-data caveat."""
    result = await client.get_prompt(prompt_name, arguments)
    assert result.messages[0].role == "user"
    text = result.messages[0].content.text
    assert expected in text
    assert "verified facts" in text
    assert "assumptions" in text
    assert "fictional" in text
    assert "no LLM has been executed" in text


@pytest.mark.asyncio
async def test_real_stdio_from_another_directory() -> None:
    """Launch the real venv interpreter and server with pipes, like Inspector."""
    transport = StdioTransport(
        command=sys.executable,
        args=[str(PROJECT_DIR / "server.py")],
        cwd=str(PROJECT_DIR.parent),
    )
    async with Client(transport, timeout=30) as stdio_client:
        assert len(await stdio_client.list_tools()) == 3
        result = await stdio_client.call_tool(
            "get_competitor", {"name": "NimbusNoodle Support"}
        )
        assert result.data == EXPECTED_PROFILE
        assert len(await stdio_client.list_resources()) == 2
        overview = await stdio_client.read_resource("market://overview")
        assert "fictional" in overview[0].text
        assert len(await stdio_client.list_prompts()) == 2
        prompt = await stdio_client.get_prompt(
            "competitor_analysis", {"name": "NimbusNoodle Support"}
        )
        assert "NimbusNoodle Support" in prompt.messages[0].content.text
        error = await stdio_client.call_tool(
            "get_competitor", {"name": "Missing"}, raise_on_error=False
        )
        assert error.is_error
