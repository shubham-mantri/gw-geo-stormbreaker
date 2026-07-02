import httpx
import respx

from gw_geo.measurement.probe.perplexity import PerplexityAdapter


@respx.mock
async def test_probe_maps_citations():
    respx.post("https://api.perplexity.ai/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "Foo is great"}}],
                "citations": ["https://a.com", "https://b.com"],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            },
        )
    )
    a = PerplexityAdapter(api_key="k", client=httpx.AsyncClient())
    r = await a.probe("best crm?")
    assert r.engine == "perplexity"
    assert r.cited_urls == ["https://a.com", "https://b.com"]
    assert r.cost_usd > 0
