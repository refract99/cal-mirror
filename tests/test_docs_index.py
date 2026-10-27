from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"


def test_every_doc_is_listed_in_the_index():
    index = (DOCS / "README.md").read_text()
    names = sorted(p.name for p in DOCS.glob("*.md") if p.name != "README.md")
    missing = [name for name in names if f"]({name})" not in index]
    assert names
    assert missing == []
