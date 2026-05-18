from importlib import import_module

from app.legacy.models.database import DimFacet, DimValue, EntityPinyin, Synonym
from app.legacy.services.dimension_service import DimensionService
from app.legacy.services.entity_extraction import EntityExtractor
from app.legacy.services.pinyin_service import PinyinService
from app.legacy.services.synonym_service import SynonymService


class FakeScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter_by(self, **kwargs):
        filtered = []
        for row in self._rows:
            if all(getattr(row, key) == value for key, value in kwargs.items()):
                filtered.append(row)
        return FakeQuery(filtered)

    def order_by(self, *_args, **_kwargs):
        return self

    def all(self):
        return list(self._rows)


class FakeDbSession:
    def __init__(self, *, synonyms=None, entities=None, facets=None, values=None):
        self._synonyms = synonyms or []
        self._entities = entities or []
        self._facets = facets or []
        self._values = values or []

    def execute(self, _stmt):
        if self._entities:
            return FakeScalarResult(self._entities)
        return FakeScalarResult(self._synonyms)

    def query(self, model):
        if model is DimFacet:
            return FakeQuery(self._facets)
        if model is DimValue:
            return FakeQuery(self._values)
        raise AssertionError(f"Unexpected query model: {model}")


def test_legacy_modules_import_smoke():
    modules = [
        "app.legacy.config.regex_patterns",
        "app.legacy.models.database",
        "app.legacy.models.admin_models",
        "app.legacy.services.dimension_service",
        "app.legacy.services.synonym_service",
        "app.legacy.services.pinyin_service",
        "app.legacy.services.entity_extraction",
        "app.legacy.services.engineering_naming",
        "app.legacy.utils.emissions",
        "app.legacy.utils.fault_code",
    ]

    for module_name in modules:
        assert import_module(module_name) is not None


def test_entity_extractor_extracts_expected_entities():
    extractor = EntityExtractor()

    result = extractor.extract(
        "东风天锦_D530.KM8N_整车电路图_EDC17CV44_国六.PDF",
        hierarchy_parts=["整车电路图", "东风", "天锦", "KM"],
    )

    assert result["brand"] == "东风"
    assert result["series"] == "天锦"
    assert "D530" in result["platform_codes"]
    assert "EDC17CV44" in result["ecus"]
    assert "国六" in result["emissions"]
    assert "电路图" in result["doc_types"]
    assert "整车电路图" in result["doc_types"]


def test_synonym_service_expands_terms_from_cache():
    SynonymService._global_term_to_group_cache = None
    SynonymService._global_group_to_terms_cache = None

    fake_db = FakeDbSession(
        synonyms=[
            Synonym(group_id="brand_df", term="东风", category="brand", is_primary=True),
            Synonym(group_id="brand_df", term="DFAC", category="brand", is_primary=False),
            Synonym(group_id="supplier_bosch", term="博世", category="supplier", is_primary=True),
        ]
    )
    service = SynonymService(fake_db)

    assert service.expand_term("DFAC") == {"东风", "DFAC"}
    assert service.expand_terms(["DFAC", "博世"]) == {"东风", "DFAC", "博世"}


def test_pinyin_service_correct_query_from_entity_index():
    PinyinService._global_entity_cache.clear()
    PinyinService._global_pinyin_cache.clear()
    PinyinService._global_abbr_cache.clear()
    PinyinService._global_cache_loaded = False

    fake_db = FakeDbSession(
        entities=[
            EntityPinyin(
                entity_type="series",
                entity_value="天锦",
                pinyin="tianjin",
                pinyin_tone="tiān jǐn",
                pinyin_abbr="tj",
                frequency=10,
            ),
            EntityPinyin(
                entity_type="brand",
                entity_value="东风",
                pinyin="dongfeng",
                pinyin_tone="dōng fēng",
                pinyin_abbr="df",
                frequency=20,
            ),
        ]
    )
    service = PinyinService(fake_db)

    result = service.correct_query("东风天景电路图")

    assert result.has_correction is True
    assert result.corrected_query == "东风天锦电路图"
    assert any(item.corrected == "天锦" for item in result.corrections)


def test_dimension_service_loads_and_matches_values():
    service = DimensionService()
    service._facets = {}
    service._values = {}
    service._values_by_id = {}
    service._match_entries = []
    service._loaded = False

    fake_db = FakeDbSession(
        facets=[
            DimFacet(
                facet_key="brand",
                facet_name="品牌",
                question="请选择品牌：",
                priority=1,
                db_field="brand",
                parent_facet_key=None,
                match_mode="dict",
                specificity=1,
                is_active=True,
            ),
            DimFacet(
                facet_key="series",
                facet_name="系列",
                question="请选择系列：",
                priority=2,
                db_field="series",
                parent_facet_key="brand",
                match_mode="dict",
                specificity=2,
                is_active=True,
            ),
        ],
        values=[
            DimValue(id=1, facet_key="brand", value="东风", match_patterns="东风,dfac", is_active=True),
            DimValue(
                id=2,
                facet_key="series",
                value="天锦",
                match_patterns="天锦",
                parent_value_id=1,
                is_active=True,
                sort_order=10,
            ),
            DimValue(
                id=3,
                facet_key="series",
                value="天锦KR",
                match_patterns="天锦KR,KR",
                parent_value_id=2,
                is_active=True,
                sort_order=20,
            ),
        ],
    )

    service.load(fake_db)
    matched = service.match("东风天锦KR电路图")

    assert matched["brand"] == ["东风"]
    assert matched["series"][0] == "天锦KR"
    assert "天锦" in matched["series"]
    assert service.get_parent("series", "天锦KR") == ("series", "天锦")
    assert service.get_ancestor_chain("series", "天锦KR") == [("series", "天锦"), ("brand", "东风")]
