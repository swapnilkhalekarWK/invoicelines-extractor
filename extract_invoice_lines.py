#!/usr/bin/env python3
"""Prototype CLI for extracting invoice lines from OCR coordinates.

The first supported input is the project's Textract-derived wrapper format:

    {"DocumentUniqueId": "...", "Serialized": "{\"Pages\": [...]}"}

The parser also accepts standard Amazon Textract DetectDocumentText JSON with
WORD blocks. The extraction is intentionally geometry-first and rule-assisted.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


NUMBER_RE = re.compile(
    r"^[+-]?(?:[€$£]?\s*)?\d{1,3}(?:[.,]\d{3})*(?:[.,]\d+)?%?$|^[+-]?(?:[€$£]?\s*)?\d+(?:[.,]\d+)?%?$"
)
CURRENCY_TOKENS = {"eur", "euro", "€", "$", "£", "â‚¬"}
TAX_CODE_RE = re.compile(r"^[A-Z]$")

HEADER_ANCHORS = {
    "tax_code": ("f",),
    "item_code": ("art.nr", "artnr", "article", "artikel", "item", "sku", "code"),
    "description": ("benaming", "description", "omschrijving", "product", "service"),
    "deposit": ("leeggoed", "deposit"),
    "pack_size": ("pack", "size"),
    "quantity": ("hoev", "qty", "quantity", "aantal"),
    "unit_price": ("eenhprijs", "unit price", "unitprice", "prijs", "price"),
    "amount": ("bedrag", "amount", "total", "totaal", "value", "nett", "net"),
    "vat": ("vat", "btw", "tax"),
    "drs": ("drs",),
}

END_ANCHORS = (
    "subtotal",
    "subtot",
    "totaal",
    "total",
    "vat",
    "btw",
    "tax",
    "te betalen",
    "balance due",
)

ADJUSTMENT_ANCHORS = (
    "discount",
    "adjustment",
    "korting",
    "voordeel",
    "hoeveelheidsvoordeel",
    "toegekend",
    "verrekend",
    "rebate",
)


@dataclass(frozen=True)
class Word:
    text: str
    page: int
    left: float
    top: float
    right: float
    bottom: float
    center_x: float
    center_y: float

    @property
    def height(self) -> float:
        return self.bottom - self.top

    def as_bbox(self) -> dict[str, float]:
        return {
            "left": round(self.left, 6),
            "top": round(self.top, 6),
            "right": round(self.right, 6),
            "bottom": round(self.bottom, 6),
        }


@dataclass
class Row:
    page: int
    words: list[Word]
    index: int

    @property
    def text(self) -> str:
        return " ".join(word.text for word in self.words)

    @property
    def center_y(self) -> float:
        return statistics.fmean(word.center_y for word in self.words)

    @property
    def bbox(self) -> dict[str, float]:
        return bbox_for_words(self.words)


@dataclass(frozen=True)
class ColumnLayout:
    header_row_index: int
    page: int
    anchors: dict[str, float]
    boundaries: dict[str, float]
    explicit_anchors: set[str]
    has_tax_code: bool


def bbox_for_words(words: Iterable[Word]) -> dict[str, float]:
    items = list(words)
    if not items:
        return {"left": 0.0, "top": 0.0, "right": 0.0, "bottom": 0.0}
    return {
        "left": round(min(word.left for word in items), 6),
        "top": round(min(word.top for word in items), 6),
        "right": round(max(word.right for word in items), 6),
        "bottom": round(max(word.bottom for word in items), 6),
    }


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().replace("â‚¬", "€")).strip()


def clean_token(text: str) -> str:
    return text.strip().replace("â‚¬", "€")


def parse_ocr_words(path: Path) -> list[Word]:
    raw = json.loads(path.read_text(encoding="utf-8-sig"))

    if isinstance(raw, dict) and "Serialized" in raw:
        serialized = raw["Serialized"]
        payload = json.loads(serialized) if isinstance(serialized, str) else serialized
        return parse_serialized_pages(payload)

    if isinstance(raw, dict) and "Pages" in raw:
        return parse_serialized_pages(raw)

    if isinstance(raw, dict) and "Blocks" in raw:
        return parse_textract_blocks(raw)

    raise ValueError(
        "Unsupported OCR JSON. Expected project wrapper, serialized Pages, or Textract Blocks."
    )


def parse_serialized_pages(payload: dict[str, Any]) -> list[Word]:
    words: list[Word] = []
    for page in payload.get("Pages", []):
        page_number = int(page.get("PageNumber", len(words) + 1))
        for region in page.get("Regions", []):
            for paragraph in region.get("Paragraphs", []):
                for line in paragraph.get("Lines", []):
                    for text_block in line.get("TextBlocks", []):
                        for word_payload in text_block.get("Words", []):
                            coords = word_payload.get("Coordinates", {})
                            text = clean_token(str(word_payload.get("Text", "")))
                            if not text or not coords:
                                continue
                            words.append(
                                Word(
                                    text=text,
                                    page=page_number,
                                    left=float(coords["Left"]),
                                    top=float(coords["Top"]),
                                    right=float(coords["Right"]),
                                    bottom=float(coords["Bottom"]),
                                    center_x=float(coords["CenterX"]),
                                    center_y=float(coords["CenterY"]),
                                )
                            )
    return words


def parse_textract_blocks(payload: dict[str, Any]) -> list[Word]:
    words: list[Word] = []
    for block in payload.get("Blocks", []):
        if block.get("BlockType") != "WORD":
            continue
        text = clean_token(str(block.get("Text", "")))
        box = block.get("Geometry", {}).get("BoundingBox", {})
        if not text or not box:
            continue
        left = float(box["Left"])
        top = float(box["Top"])
        width = float(box["Width"])
        height = float(box["Height"])
        words.append(
            Word(
                text=text,
                page=int(block.get("Page", 1)),
                left=left,
                top=top,
                right=left + width,
                bottom=top + height,
                center_x=left + width / 2,
                center_y=top + height / 2,
            )
        )
    return words


def reconstruct_rows(words: list[Word]) -> list[Row]:
    rows: list[Row] = []
    row_index = 0
    pages = sorted({word.page for word in words})

    for page in pages:
        page_words = sorted(
            (word for word in words if word.page == page),
            key=lambda item: (item.center_y, item.left),
        )
        if not page_words:
            continue

        heights = [word.height for word in page_words if word.height > 0]
        median_height = statistics.median(heights) if heights else 0.008
        tolerance = min(max(median_height * 0.7, 0.003), 0.012)

        current: list[Word] = []
        current_y: float | None = None

        for word in page_words:
            if current_y is None or abs(word.center_y - current_y) <= tolerance:
                current.append(word)
                current_y = statistics.fmean(item.center_y for item in current)
                continue

            rows.append(Row(page=page, words=sorted(current, key=lambda item: item.left), index=row_index))
            row_index += 1
            current = [word]
            current_y = word.center_y

        if current:
            rows.append(Row(page=page, words=sorted(current, key=lambda item: item.left), index=row_index))
            row_index += 1

    return rows


def header_score(row: Row) -> int:
    score = 0
    for anchors in HEADER_ANCHORS.values():
        if find_header_word(row.words, anchors):
            score += 1
    return score


def find_header_row(rows: list[Row]) -> Row:
    candidates = sorted(
        (row for row in rows if header_score(row) >= 3),
        key=lambda row: (header_score(row), -row.center_y),
        reverse=True,
    )
    if not candidates:
        raise ValueError("Could not find a line-item table header row.")
    return candidates[0]


def infer_columns(header: Row) -> ColumnLayout:
    anchors = default_anchors()
    explicit_anchors: set[str] = set()
    header_words = header.words

    for column, labels in HEADER_ANCHORS.items():
        match = find_header_word(header_words, labels)
        if match:
            anchors[column] = match.center_x
            explicit_anchors.add(column)

    boundaries = {
        "tax_item": midpoint(anchors["tax_code"], anchors["item_code"]),
        "item_description": midpoint(anchors["item_code"], anchors["description"]),
        "description_numeric": anchors["deposit"] - 0.03,
        "deposit_quantity": midpoint(anchors["deposit"], anchors["quantity"]),
        "quantity_unit": midpoint(anchors["quantity"], anchors["unit_price"]),
        "unit_amount": midpoint(anchors["unit_price"], anchors["amount"]),
    }
    boundaries["description_numeric"] = min(
        max(boundaries["description_numeric"], boundaries["item_description"] + 0.12),
        boundaries["deposit_quantity"] - 0.02,
    )

    return ColumnLayout(
        header_row_index=header.index,
        page=header.page,
        anchors=anchors,
        boundaries=boundaries,
        explicit_anchors=explicit_anchors,
        has_tax_code=(
            "tax_code" in explicit_anchors
            and "item_code" in explicit_anchors
            and anchors["tax_code"] < anchors["item_code"]
        ),
    )


def default_anchors() -> dict[str, float]:
    return {
        "tax_code": 0.02,
        "item_code": 0.055,
        "description": 0.12,
        "deposit": 0.45,
        "pack_size": 0.45,
        "quantity": 0.51,
        "unit_price": 0.57,
        "amount": 0.64,
        "vat": 0.72,
        "drs": 0.93,
    }


def midpoint(a: float, b: float) -> float:
    return (a + b) / 2


def find_header_word(words: list[Word], labels: tuple[str, ...]) -> Word | None:
    for word in words:
        normalized = normalize_text(word.text).replace(".", "")
        original = normalize_text(word.text)
        for label in labels:
            label_normalized = label.replace(".", "")
            if original == label or normalized == label_normalized:
                return word
    for word in words:
        normalized = normalize_text(word.text)
        if any(label in normalized for label in labels):
            for label in labels:
                if len(label) > 2 and label in normalized:
                    return word
    return None


def extract_invoice_lines(rows: list[Row]) -> tuple[list[dict[str, Any]], ColumnLayout]:
    header = find_header_row(rows)
    layout = infer_columns(header)
    extracted: list[dict[str, Any]] = []
    misses_after_lines = 0

    for row in rows:
        if row.page != header.page or row.index <= header.index:
            continue

        text = normalize_text(row.text)
        if extracted and any(anchor in text for anchor in END_ANCHORS):
            break

        line = parse_product_line(row, layout)
        if not line:
            line = parse_adjustment_line(row)

        if line:
            extracted.append(line)
            misses_after_lines = 0
            continue

        if extracted:
            misses_after_lines += 1
            if misses_after_lines >= 2:
                break

    return extracted, layout


def parse_product_line(row: Row, layout: ColumnLayout) -> dict[str, Any] | None:
    words = sorted(row.words, key=lambda item: item.left)
    if not words:
        return None

    tax_word: Word | None = None
    item_word: Word | None = None
    if (
        layout.has_tax_code
        and len(words) > 1
        and TAX_CODE_RE.match(words[0].text)
        and looks_item_code(words[1].text)
    ):
        tax_word = words[0]
        item_word = words[1]
    elif looks_item_code(words[0].text):
        item_word = words[0]

    if not item_word:
        return None

    used_words: set[Word] = {item_word}
    if tax_word:
        used_words.add(tax_word)

    quantity_word = numeric_word_near_anchor(words, layout.anchors["quantity"], used_words)
    if quantity_word:
        used_words.add(quantity_word)

    unit_price_word = numeric_word_near_anchor(words, layout.anchors["unit_price"], used_words)
    if unit_price_word:
        used_words.add(unit_price_word)

    amount_word = numeric_word_near_anchor(words, layout.anchors["amount"], used_words)
    if amount_word:
        used_words.add(amount_word)

    for ignored_column in ("deposit", "vat", "drs"):
        if ignored_column in layout.explicit_anchors:
            ignored_word = numeric_word_near_anchor(
                words,
                layout.anchors[ignored_column],
                used_words,
                allow_percent=True,
            )
            if ignored_word:
                used_words.add(ignored_word)

    description_words = [
        word
        for word in words
        if word not in used_words and normalize_text(word.text) not in CURRENCY_TOKENS
    ]

    tax_code = tax_word.text if tax_word else None
    item_code = item_word.text
    description = join_words(description_words) or None
    quantity = quantity_word.text if quantity_word else None
    unit_price = unit_price_word.text if unit_price_word else None
    amount = amount_word.text if amount_word else None

    has_product_shape = bool(item_code and description and amount)
    if not has_product_shape:
        return None

    if tax_code and not TAX_CODE_RE.match(tax_code):
        return None
    if item_code and not looks_item_code(item_code):
        return None

    arithmetic_ok = arithmetic_matches(quantity, unit_price, amount)
    confidence = product_confidence(
        item_code=item_code,
        tax_code=tax_code,
        description=description,
        quantity=quantity,
        unit_price=unit_price,
        amount=amount,
        arithmetic_ok=arithmetic_ok,
    )

    return {
        "line_type": "product",
        "tax_code": tax_code,
        "item_code": item_code,
        "description": description,
        "quantity": quantity,
        "unit_price": unit_price,
        "amount": amount,
        "confidence": confidence,
        "page": row.page,
        "bbox": row.bbox,
        "raw_text": row.text,
    }


def split_columns(words: list[Word], boundaries: dict[str, float]) -> dict[str, list[Word]]:
    columns = {
        "tax_code": [],
        "item_code": [],
        "description": [],
        "deposit": [],
        "quantity": [],
        "unit_price": [],
        "amount": [],
    }

    for word in words:
        x = word.center_x
        if x < boundaries["tax_item"]:
            columns["tax_code"].append(word)
        elif x < boundaries["item_description"]:
            columns["item_code"].append(word)
        elif x < boundaries["description_numeric"]:
            columns["description"].append(word)
        elif x < boundaries["deposit_quantity"]:
            columns["deposit"].append(word)
        elif x < boundaries["quantity_unit"]:
            columns["quantity"].append(word)
        elif x < boundaries["unit_amount"]:
            columns["unit_price"].append(word)
        else:
            columns["amount"].append(word)

    return columns


def looks_item_code(text: str) -> bool:
    value = clean_token(text)
    if not value or normalize_text(value) in CURRENCY_TOKENS:
        return False
    if value.endswith("%"):
        return False
    return any(char.isdigit() for char in value)


def numeric_core(text: str) -> str:
    return clean_token(text).strip().rstrip("*")


def is_numeric_text(text: str, *, allow_percent: bool = False) -> bool:
    value = numeric_core(text)
    if not value or normalize_text(value) in CURRENCY_TOKENS:
        return False
    if value.endswith("%") and not allow_percent:
        return False
    return bool(NUMBER_RE.match(value))


def numeric_word_near_anchor(
    words: list[Word],
    anchor_x: float,
    used_words: set[Word],
    *,
    allow_percent: bool = False,
    max_distance: float = 0.12,
) -> Word | None:
    candidates = [
        word
        for word in words
        if word not in used_words and is_numeric_text(word.text, allow_percent=allow_percent)
    ]
    if not candidates:
        return None

    nearest = min(candidates, key=lambda word: abs(word.center_x - anchor_x))
    if abs(nearest.center_x - anchor_x) > max_distance:
        return None
    return nearest


def parse_adjustment_line(row: Row) -> dict[str, Any] | None:
    normalized = normalize_text(row.text)
    if not any(anchor in normalized for anchor in ADJUSTMENT_ANCHORS):
        return None

    numeric_words = [word for word in row.words if first_numeric_text([word])]
    amount_word = numeric_words[-1] if numeric_words else None
    if not amount_word:
        return None

    description_words = [
        word
        for word in row.words
        if word != amount_word and normalize_text(word.text) not in CURRENCY_TOKENS
    ]
    description = join_words(description_words) or row.text

    confidence = 0.86
    if any(anchor in normalized for anchor in ("discount", "korting", "voordeel", "hoeveelheidsvoordeel")):
        confidence += 0.05

    return {
        "line_type": "adjustment",
        "tax_code": None,
        "item_code": None,
        "description": description,
        "quantity": None,
        "unit_price": None,
        "amount": amount_word.text,
        "confidence": round(min(confidence, 0.95), 3),
        "page": row.page,
        "bbox": row.bbox,
        "raw_text": row.text,
    }


def join_words(words: Iterable[Word]) -> str:
    return " ".join(word.text for word in sorted(words, key=lambda item: item.left)).strip()


def first_numeric_text(words: Iterable[Word]) -> str | None:
    for word in sorted(words, key=lambda item: item.left):
        if is_numeric_text(word.text):
            return clean_token(word.text)
    return None


def parse_number(text: str | None) -> float | None:
    if not text:
        return None

    value = numeric_core(text)
    value = re.sub(r"[€$£\s%]", "", value)
    if not value:
        return None

    if "," in value and "." in value:
        decimal_separator = "," if value.rfind(",") > value.rfind(".") else "."
        thousands_separator = "." if decimal_separator == "," else ","
        value = value.replace(thousands_separator, "")
        value = value.replace(decimal_separator, ".")
    elif "," in value:
        value = value.replace(",", ".")

    try:
        return float(value)
    except ValueError:
        return None


def arithmetic_matches(quantity: str | None, unit_price: str | None, amount: str | None) -> bool:
    quantity_value = parse_number(quantity)
    unit_price_value = parse_number(unit_price)
    amount_value = parse_number(amount)
    if quantity_value is None or unit_price_value is None or amount_value is None:
        return False

    expected = quantity_value * unit_price_value
    tolerance = max(0.02, abs(amount_value) * 0.01)
    return math.isclose(expected, amount_value, abs_tol=tolerance)


def product_confidence(
    *,
    item_code: str | None,
    tax_code: str | None,
    description: str | None,
    quantity: str | None,
    unit_price: str | None,
    amount: str | None,
    arithmetic_ok: bool,
) -> float:
    confidence = 0.25
    confidence += 0.05 if tax_code else 0.0
    confidence += 0.10 if item_code else 0.0
    confidence += 0.15 if description else 0.0
    confidence += 0.10 if quantity else 0.0
    confidence += 0.10 if unit_price else 0.0
    confidence += 0.10 if amount else 0.0
    confidence += 0.25 if arithmetic_ok else 0.0
    return round(min(confidence, 0.99), 3)


def debug_rows(rows: list[Row]) -> None:
    print("Reconstructed rows:", file=sys.stderr)
    for row in rows:
        print(
            f"{row.index:03d} page={row.page} y={row.center_y:.6f} {row.text}",
            file=sys.stderr,
        )


def debug_columns(layout: ColumnLayout) -> None:
    print("Inferred columns:", file=sys.stderr)
    print(f"  header_row_index: {layout.header_row_index}", file=sys.stderr)
    print(f"  anchors: {json.dumps(layout.anchors, sort_keys=True)}", file=sys.stderr)
    print(f"  explicit_anchors: {json.dumps(sorted(layout.explicit_anchors))}", file=sys.stderr)
    print(f"  has_tax_code: {layout.has_tax_code}", file=sys.stderr)
    print(f"  boundaries: {json.dumps(layout.boundaries, sort_keys=True)}", file=sys.stderr)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract product/service and adjustment invoice lines from OCR JSON."
    )
    parser.add_argument("ocr_file", type=Path, help="Path to OCR JSON or project .txt wrapper.")
    parser.add_argument(
        "--debug-rows",
        action="store_true",
        help="Print reconstructed OCR rows to stderr.",
    )
    parser.add_argument(
        "--debug-columns",
        action="store_true",
        help="Print inferred column anchors and boundaries to stderr.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    words = parse_ocr_words(args.ocr_file)
    rows = reconstruct_rows(words)
    lines, layout = extract_invoice_lines(rows)

    if args.debug_rows:
        debug_rows(rows)
    if args.debug_columns:
        debug_columns(layout)

    json_kwargs = {"ensure_ascii": False}
    if args.pretty:
        json_kwargs["indent"] = 2
    print(json.dumps(lines, **json_kwargs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
