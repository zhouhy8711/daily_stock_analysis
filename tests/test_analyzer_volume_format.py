from src.analyzer import GeminiAnalyzer


def test_analyzer_formats_volume_in_lots() -> None:
    analyzer = object.__new__(GeminiAnalyzer)

    assert analyzer._format_volume(None) == "N/A"
    assert analyzer._format_volume(38800) == "3.88 万手"
    assert analyzer._format_volume(387998400) == "3.88 亿手"
    assert analyzer._format_volume(9800) == "9800 手"
