from gw_geo.ranking.features import (info_density, structure_score, freshness_days,
                                     cosine, extract_features)


class StubEmbedder:
    # deterministic: vector keyed on presence of the word "crm"
    def embed(self, text):
        return [1.0, 0.0] if "crm" in text.lower() else [0.0, 1.0]


def test_info_density_counts_stats_per_100_words():
    text = "Our tool cut costs by 40% and saved 12 hours " + "word " * 92
    assert info_density(text) > 0  # 2 numeric tokens in ~100 words


def test_structure_score_rewards_structure():
    plain = "just a paragraph of prose with no structure at all"
    rich = "## What is X\nX is Y.\n- a\n- b\n\n| col | col |\n|--|--|\n| 1 | 2 |"
    assert structure_score(rich) > structure_score(plain)


def test_freshness_days():
    assert freshness_days("2026-06-22", "2026-07-02") == 10.0
    assert freshness_days(None, "2026-07-02") is None


def test_cosine_orthogonal_and_parallel():
    assert cosine([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_cosine_zero_vector_is_zero():
    # Rule: cosine is 0.0 (not an error) if either input is a zero-vector.
    assert cosine([0.0, 0.0], [1.0, 0.0]) == 0.0
    assert cosine([1.0, 0.0], [0.0, 0.0]) == 0.0


def test_extract_features_builds_vector():
    fv = extract_features(content='CRM guide <script type="application/ld+json">{}</script>',
                          prompt_text="best crm", domain_authority=0.7, corroboration_count=3,
                          published_at="2026-06-30", embedder=StubEmbedder(), now="2026-07-02")
    assert fv.has_schema is True
    assert fv.embedding_similarity == 1.0   # both contain "crm"
    assert fv.domain_authority == 0.7 and fv.corroboration_count == 3
    assert fv.freshness_days == 2.0


def test_extract_features_detects_faq_heading():
    content = "## FAQ\nQ: what is x?\nA: y."
    fv = extract_features(content=content, prompt_text="x", domain_authority=0.5,
                          corroboration_count=0, published_at=None, embedder=StubEmbedder(),
                          now="2026-07-02")
    assert fv.has_faq is True
    assert fv.freshness_days is None


def test_extract_features_counts_markdown_tables_and_no_schema():
    content = "| a | b |\n|---|---|\n| 1 | 2 |"
    fv = extract_features(content=content, prompt_text="x", domain_authority=0.5,
                          corroboration_count=0, published_at=None, embedder=StubEmbedder(),
                          now="2026-07-02")
    assert fv.table_count == 1
    assert fv.has_schema is False
