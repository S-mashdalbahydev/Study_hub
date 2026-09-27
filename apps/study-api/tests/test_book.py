import unittest
from app.book import markdown_book


class SemanticBookTests(unittest.TestCase):
    def test_hierarchy_stable_ids_and_block_safe_pages(self):
        content = """# Systems\n\n## Part I — Foundations\n\n## Chapter 1 — Storage\n\n### 1.1 Records\n\nA complete paragraph.\n\n```python\nprint('whole block')\n```\n\n#### Example 1.1 — A record\n\n> An illustrative AI example.\n"""
        book = markdown_book(content, "Systems", "systems.md", target_chars=45)
        self.assertEqual([item["kind"] for item in book["toc"][:3]], ["part", "chapter", "section"])
        self.assertEqual(book["toc"][1]["parent_id"], book["toc"][0]["id"])
        self.assertEqual(book["toc"][2]["parent_id"], book["toc"][1]["id"])
        self.assertEqual(book["toc"][3]["parent_id"], book["toc"][2]["id"])
        self.assertEqual(sum(block["type"] == "code" for page in book["pages"] for block in page["blocks"]), 1)
        self.assertEqual(book["page_count"], len(book["pages"]) + 1)

    def test_duplicate_headings_receive_stable_unique_ids(self):
        book = markdown_book("# T\n\n## Chapter 1: A\n\n### Example\n\n### Example", "T")
        self.assertEqual([item["id"] for item in book["toc"][-2:]], ["example", "example-2"])

    def test_required_outline_maps_page_ranges_to_semantic_nodes(self):
        content = """# Data Book

## Introduction

## Chapter 1 — Foundations

### 1.1 Introduction

### 1.2 Data Types

#### Structured Data

#### Unstructured Data

### 1.3 Storage

## Chapter 2 — Architecture

### 2.1 Overview

### 2.2 Components

## References
"""
        ranges={"1-1-introduction":(4,6),"1-2-data-types":(7,12),"structured-data":(7,9),
                "unstructured-data":(10,12),"1-3-storage":(13,17)}
        book=markdown_book(content,"Data Book",source_ranges=ranges)
        by_id={node["id"]:node for node in book["toc"]}
        self.assertEqual(by_id["1-2-data-types"]["source_start_page"],7)
        self.assertEqual(by_id["1-2-data-types"]["source_end_page"],12)
        self.assertEqual(by_id["structured-data"]["parent_id"],"1-2-data-types")
        self.assertEqual(by_id["unstructured-data"]["parent_id"],"1-2-data-types")
        self.assertFalse(any(node["title"].startswith("Page ") for node in book["toc"]))
        self.assertEqual(sum(node["id"]=="1-2-data-types" for node in book["toc"]),1)

    def test_hostile_markdown_does_not_promote_page_text_and_recovers_missing_level(self):
        content="""# Hostile Book

## Chapter 1 — Foundations

**A VERY LARGE BOLD SENTENCE THAT IS NOT A HEADING**

This paragraph starts on page one and continues
halfway through the next physical page.

#### Missing intermediate heading number

## Chapter 2 — Architecture

### Repeated

### Repeated
"""
        book=markdown_book(content,"Hostile Book")
        titles=[node["title"] for node in book["toc"]]
        self.assertNotIn("A VERY LARGE BOLD SENTENCE THAT IS NOT A HEADING",titles)
        recovered=next(node for node in book["toc"] if node["title"]=="Missing intermediate heading number")
        self.assertEqual(recovered["kind"],"section")
        self.assertEqual(recovered["parent_id"],"chapter-1-foundations")
        self.assertEqual([node["id"] for node in book["toc"][-2:]], ["repeated","repeated-2"])

    def test_large_book_is_semantic_and_has_stable_ids(self):
        lines=["# Large Book"]
        ranges={}
        for chapter in range(1,21):
            lines.append(f"## Chapter {chapter} — Topic {chapter}")
            for section in range(1,7):
                title=f"{chapter}.{section} Concept"
                lines.extend([f"### {title}","A complete semantic content block."])
                ranges[f"{chapter}-{section}-concept"]=((chapter-1)*18+section*3-2,(chapter-1)*18+section*3)
        content="\n\n".join(lines)
        first=markdown_book(content,"Large Book",source_ranges=ranges)
        second=markdown_book(content,"Large Book",source_ranges=ranges)
        self.assertEqual(sum(node["kind"]=="chapter" for node in first["toc"]),20)
        self.assertEqual(sum(node["kind"]=="section" for node in first["toc"]),120)
        self.assertGreaterEqual(max(node["source_end_page"] or 0 for node in first["toc"]),300)
        self.assertFalse(any(node["title"].startswith("Page ") for node in first["toc"]))
        self.assertEqual([node["id"] for node in first["toc"]],[node["id"] for node in second["toc"]])


if __name__ == "__main__":
    unittest.main()
